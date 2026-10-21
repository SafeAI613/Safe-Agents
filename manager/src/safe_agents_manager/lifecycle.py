"""Stopping the manager when the desktop app goes away, even if it crashed."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


def watch_stdin(on_close: Callable[[], None]) -> threading.Thread:
    """Call ``on_close`` on a ``shutdown`` line or when stdin closes (the parent exited)."""

    def run() -> None:
        try:
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    break
        except (OSError, ValueError):
            pass
        log.info("stdin closed or shutdown requested")
        on_close()

    thread = threading.Thread(target=run, name="stdin-watch", daemon=True)
    thread.start()
    return thread


if sys.platform == "win32":
    import ctypes

    _SYNCHRONIZE = 0x00100000
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _WAIT_TIMEOUT = 0x102
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def pid_alive(pid: int) -> bool:
        handle = _kernel32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            return bool(_kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT)
        finally:
            _kernel32.CloseHandle(handle)

else:

    def pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def watch_parent(
    pid: int, on_gone: Callable[[], None], interval_s: float = 2.0
) -> threading.Thread:
    def run() -> None:
        while pid_alive(pid):
            time.sleep(interval_s)
        log.info("parent process %s is gone", pid)
        on_gone()

    thread = threading.Thread(target=run, name="parent-watch", daemon=True)
    thread.start()
    return thread
