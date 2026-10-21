"""Creating an isolated Python environment for one agent with ``uv``."""

from __future__ import annotations

import os
import subprocess
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
        env.pop("VIRTUAL_ENV", None)
        return env

    def _run(self, args: list[str], log: LogFn) -> None:
        cmd = [str(self._settings.uv_path), *args]
        log("$ " + " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                env=self._uv_env(),
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise InstallError(f"uv was not found at '{self._settings.uv_path}'") from exc
        except subprocess.TimeoutExpired as exc:
            raise InstallError(f"'uv {args[0]}' timed out after {self._timeout_s:.0f}s") from exc
        output = (proc.stdout + proc.stderr).strip()
        for line in output.splitlines():
            log(line)
        if proc.returncode != 0:
            tail = "\n".join(output.splitlines()[-15:])
            raise InstallError(f"'uv {args[0]}' failed (exit {proc.returncode}):\n{tail}")

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
