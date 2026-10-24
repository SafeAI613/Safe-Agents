"""Creating an isolated Python environment for one agent with ``uv``."""

from __future__ import annotations

import os
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from typing import Protocol

from .config import Settings
from .errors import InstallError
from .layout import AgentLayout
from .manifest import REQUIREMENTS_FILENAME

# The server package every agent runs on. Pinned to a minor range so a new release
# cannot silently change the API the desktop client talks to.
LANGGRAPH_CLI_REQUIREMENT = "langgraph-cli[inmem]>=0.4.30,<0.5"

LogFn = Callable[[str], None]


class EnvBuilder(Protocol):
    def build(self, layout: AgentLayout, python_spec: str, log: LogFn) -> None: ...


class UvEnvBuilder:
    def __init__(self, settings: Settings, timeout_s: float = 900.0) -> None:
        self._settings = settings
        self._timeout_s = timeout_s

    def _uv_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # Only use Pythons that uv manages inside our data folder, never the user's system one.
        env["UV_PYTHON_PREFERENCE"] = "only-managed"
        env["UV_PYTHON_INSTALL_DIR"] = str(self._settings.python_dir)
        env["UV_CACHE_DIR"] = str(self._settings.cache_dir)
        env["UV_NO_PROGRESS"] = "1"  # plain lines instead of spinners in the install log
        env.pop("VIRTUAL_ENV", None)
        return env

    def _run(self, args: list[str], log: LogFn) -> None:
        """Run uv, forwarding each output line as it appears (first installs take minutes)."""
        cmd = [str(self._settings.uv_path), *args]
        log("$ " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                # No console window per uv call when running under the desktop app.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=self._uv_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise InstallError(f"uv was not found at '{self._settings.uv_path}'") from exc

        timed_out = threading.Event()

        def on_timeout() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(self._timeout_s, on_timeout)
        timer.start()
        tail: deque[str] = deque(maxlen=15)
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip()
                if line:
                    tail.append(line)
                    log(line)
            returncode = proc.wait()
        finally:
            timer.cancel()
        if timed_out.is_set():
            raise InstallError(f"'uv {args[0]}' timed out after {self._timeout_s:.0f}s")
        if returncode != 0:
            details = "\n".join(tail)
            raise InstallError(f"'uv {args[0]}' failed (exit {returncode}):\n{details}")

    def build(self, layout: AgentLayout, python_spec: str, log: LogFn) -> None:
        self._run(["venv", str(layout.venv), "--python", python_spec], log)
        python = layout.venv_executable("python")
        requirements = layout.src / REQUIREMENTS_FILENAME
        self._run(
            [
                "pip",
                "install",
                "--python",
                str(python),
                "-r",
                str(requirements),
                LANGGRAPH_CLI_REQUIREMENT,
            ],
            log,
        )
        if not layout.venv_executable("langgraph").is_file():
            raise InstallError("langgraph CLI is missing from the agent environment")
