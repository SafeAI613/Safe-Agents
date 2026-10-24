"""Starting, health-checking and stopping one ``langgraph dev`` server per agent."""

from __future__ import annotations

import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import Settings
from .errors import AgentStartError, MissingSecretError
from .layout import AGENT_TOKEN_ENV, AGENT_TOKEN_HEADER, AgentLayout, write_langgraph_config
from .manifest import AgentManifest
from .orphans import PidRegistry
from .ports import PortAllocator
from .secret_store import SecretProvider
from .store import InstalledAgent
from .winjob import assign_to_kill_on_close_job

log = logging.getLogger(__name__)

# The only variables an agent inherits from the manager. Everything else (including any
# secret that happens to be in the user's environment) must be declared in the manifest.
_BASE_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
)

CommandFactory = Callable[[AgentLayout, int], list[str]]


def langgraph_dev_command(layout: AgentLayout, port: int) -> list[str]:
    return [
        str(layout.venv_executable("langgraph")),
        "dev",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-browser",
        "--no-reload",
        "--config",
        str(layout.langgraph_config),
    ]


def build_agent_env(
    manifest: AgentManifest,
    secrets: SecretProvider,
    *,
    strict: bool,
    agent_id: str | None = None,
    base_environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    base = os.environ if base_environ is None else base_environ
    env = {key: base[key] for key in _BASE_ENV_KEYS if base.get(key)}
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "NO_COLOR": "1",
            "LOG_COLOR": "false",
            # langgraph-cli sends usage analytics by default; never without the user's consent.
            "LANGGRAPH_CLI_NO_ANALYTICS": "1",
        }
    )
    missing: list[str] = []
    for key in manifest.required_env:
        value = secrets.get(key, agent_id)
        if value:
            env[key] = value
        else:
            missing.append(key)
    for key in manifest.optional_env:
        value = secrets.get(key, agent_id)
        if value:
            env[key] = value
    # Tracing is a platform feature: once the user sets a LangSmith key every agent reports
    # to its own project, whether or not the agent declared the key.
    langsmith_key = secrets.get("LANGSMITH_API_KEY")
    if langsmith_key:
        env["LANGSMITH_API_KEY"] = langsmith_key
        env["LANGSMITH_TRACING"] = "true"
        env["LANGSMITH_PROJECT"] = f"safe-agents-{manifest.agent_id}"
    if missing and strict:
        raise MissingSecretError(f"missing required secrets: {', '.join(missing)}")
    return env


def tail_lines(path: Path, max_lines: int, max_bytes: int = 64 * 1024) -> list[str]:
    if not path.is_file():
        return []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        text = fh.read().decode("utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


# A separate process group lets us stop the server together with any child it spawned.
if sys.platform == "win32":
    _CREATION_FLAGS = subprocess.CREATE_NEW_PROCESS_GROUP
else:
    _CREATION_FLAGS = 0
_NEW_SESSION = sys.platform != "win32"


def _terminate_tree(proc: subprocess.Popen[bytes], timeout_s: float) -> None:
    if proc.poll() is None:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("process %s did not stop in %.0fs, killing it", proc.pid, timeout_s)
    if sys.platform == "win32":
        if proc.poll() is None:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
            )
    else:
        # Also removes stragglers left in the group after the leader exited.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


@dataclass
class AgentProcess:
    agent_id: str
    graph_id: str
    port: int
    process: subprocess.Popen[bytes]
    log_path: Path
    # Clients must send it in the x-api-key header; see layout.PLATFORM_AUTH_SOURCE.
    token: str
    last_activity: float = field(default_factory=time.monotonic)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def auth_headers(self) -> dict[str, str]:
        return {AGENT_TOKEN_HEADER: self.token}


class Supervisor:
    def __init__(
        self,
        settings: Settings,
        ports: PortAllocator,
        secrets: SecretProvider,
        command_factory: CommandFactory = langgraph_dev_command,
        pids: PidRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._pids = pids or PidRegistry(settings.home / "runtime" / "agents.json")
        self._ports = ports
        self._secrets = secrets
        self._command_factory = command_factory
        self._running: dict[str, AgentProcess] = {}
        self._lock = threading.Lock()
        self._agent_locks: dict[str, threading.Lock] = {}

    def _agent_lock(self, agent_id: str) -> threading.Lock:
        with self._lock:
            return self._agent_locks.setdefault(agent_id, threading.Lock())

    # -- public API -------------------------------------------------------------------------

    def get(self, agent_id: str) -> AgentProcess | None:
        """Return the running process, cleaning up the entry if the server has died."""
        with self._lock:
            handle = self._running.get(agent_id)
        if handle is None or handle.alive:
            return handle
        log.warning(
            "agent %s exited unexpectedly with code %s", agent_id, handle.process.returncode
        )
        self._forget(handle)
        return None

    def start(self, agent: InstalledAgent) -> AgentProcess:
        with self._agent_lock(agent.id):
            existing = self.get(agent.id)
            if existing is not None:
                return existing
            handle = self._launch(agent.id, agent.layout, agent.manifest, strict=True)
            with self._lock:
                self._running[agent.id] = handle
            return handle

    def stop(self, agent_id: str) -> bool:
        with self._agent_lock(agent_id):
            with self._lock:
                handle = self._running.get(agent_id)
            if handle is None:
                return False
            self._shutdown(handle)
            return True

    def touch(self, agent_id: str) -> bool:
        """Record that the agent is in use (called by the UI's heartbeat)."""
        handle = self.get(agent_id)
        if handle is not None:
            handle.last_activity = time.monotonic()
        return handle is not None

    def stop_idle(self, max_idle_s: float) -> list[str]:
        with self._lock:
            handles = list(self._running.values())
        now = time.monotonic()
        stopped = []
        for handle in handles:
            if now - handle.last_activity > max_idle_s:
                log.info("stopping %s after %.0fs without use", handle.agent_id, max_idle_s)
                self.stop(handle.agent_id)
                stopped.append(handle.agent_id)
        return stopped

    def stop_all(self) -> None:
        with self._lock:
            agent_ids = list(self._running)
        for agent_id in agent_ids:
            self.stop(agent_id)

    def probe(self, layout: AgentLayout, manifest: AgentManifest) -> None:
        """Smoke test: the server starts and serves the manifest's graph. Secrets not required."""
        handle = self._launch(f"install-{manifest.agent_id}", layout, manifest, strict=False)
        try:
            response = httpx.post(
                f"{handle.url}/assistants/search",
                json={"graph_id": manifest.graph_id, "limit": 1},
                headers=handle.auth_headers,
                timeout=10,
            )
            response.raise_for_status()
            if not response.json():
                raise AgentStartError(f"server started but graph '{manifest.graph_id}' is missing")
        except httpx.HTTPError as exc:
            raise AgentStartError(f"graph check failed: {exc}") from exc
        finally:
            self._shutdown(handle)

    # -- internals --------------------------------------------------------------------------

    def _launch(
        self, name: str, layout: AgentLayout, manifest: AgentManifest, *, strict: bool
    ) -> AgentProcess:
        env = build_agent_env(manifest, self._secrets, strict=strict, agent_id=manifest.agent_id)
        token = secrets.token_urlsafe(32)
        env[AGENT_TOKEN_ENV] = token
        env["CORS_ALLOW_ORIGINS"] = ",".join(self._settings.cors_origins)
        # Regenerate on every start so agents installed by an older version get current auth.
        write_langgraph_config(layout, manifest)
        port = self._ports.allocate()
        self._settings.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._settings.logs_dir / f"{name}.log"
        command = self._command_factory(layout, port)
        try:
            with log_path.open("ab") as log_fh:
                stamp = datetime.now(UTC).isoformat(timespec="seconds")
                log_fh.write(f"\n=== {stamp} starting {name} on port {port} ===\n".encode())
                log_fh.flush()
                process = subprocess.Popen(
                    command,
                    cwd=layout.root,  # relative paths in langgraph.json resolve from here
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_fh,
                    stderr=subprocess.STDOUT,
                    creationflags=_CREATION_FLAGS,
                    start_new_session=_NEW_SESSION,
                )
        except OSError as exc:
            self._ports.release(port)
            raise AgentStartError(f"could not start agent server: {exc}") from exc
        assign_to_kill_on_close_job(process)
        self._pids.add(name, process.pid, layout.root)

        handle = AgentProcess(name, manifest.graph_id, port, process, log_path, token)
        try:
            self._wait_healthy(handle)
        except BaseException:
            self._shutdown(handle)
            raise
        log.info("agent %s is up on %s", name, handle.url)
        return handle

    def _wait_healthy(self, handle: AgentProcess) -> None:
        deadline = time.monotonic() + self._settings.startup_timeout_s
        while time.monotonic() < deadline:
            if not handle.alive:
                tail = "\n".join(tail_lines(handle.log_path, 20))
                raise AgentStartError(
                    f"agent server exited with code {handle.process.returncode}:\n{tail}"
                )
            with suppress(httpx.HTTPError):
                if httpx.get(f"{handle.url}/ok", timeout=2).status_code == 200:
                    return
            time.sleep(0.5)
        raise AgentStartError(
            f"agent server did not become healthy within {self._settings.startup_timeout_s:.0f}s"
        )

    def _shutdown(self, handle: AgentProcess) -> None:
        _terminate_tree(handle.process, self._settings.stop_timeout_s)
        self._forget(handle)

    def reap_orphans(self) -> list[int]:
        return self._pids.reap()

    def _forget(self, handle: AgentProcess) -> None:
        self._pids.remove(handle.agent_id)
        with self._lock:
            if self._running.get(handle.agent_id) is handle:
                del self._running[handle.agent_id]
        self._ports.release(handle.port)
