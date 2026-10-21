"""Agent servers left behind by a manager that was killed outright.

Windows ties agents to the manager with a Job Object (see ``winjob``). On macOS/Linux a
SIGKILLed manager cannot clean up, so every agent it starts is recorded here and the next
manager stops the ones that are still running. A recorded PID is only signalled if the
process's command line still points into that agent's folder, so a PID reused by an
unrelated program is never touched.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .lifecycle import pid_alive

log = logging.getLogger(__name__)


def _command_line(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            return None
    if sys.platform == "darwin":
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        return result.stdout or None
    return None


def _stop_group(pid: int, timeout_s: float = 5.0) -> None:
    if sys.platform == "win32":
        return
    # Agents are started as session leaders, so their process group id equals their pid.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_s
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


class PidRegistry:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, object]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, dict[str, object]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self._path)

    def add(self, name: str, pid: int, root: Path) -> None:
        with self._lock:
            data = self._read()
            data[name] = {"pid": pid, "root": str(root)}
            self._write(data)

    def remove(self, name: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(name, None) is not None:
                self._write(data)

    def reap(self) -> list[int]:
        """Stop recorded agents that are still running; call before starting any agent."""
        stopped = []
        with self._lock:
            for name, entry in self._read().items():
                pid, root = entry.get("pid"), entry.get("root")
                if not isinstance(pid, int) or not isinstance(root, str):
                    continue
                command = _command_line(pid) if pid_alive(pid) else None
                if command and root in command:
                    log.warning("stopping %s (pid %s) left over from a previous run", name, pid)
                    _stop_group(pid)
                    stopped.append(pid)
            self._write({})
        return stopped
