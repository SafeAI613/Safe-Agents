"""File-system helpers."""

from __future__ import annotations

import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any


def _clear_readonly_and_retry(func: Any, target: str, *_: Any) -> None:
    # Windows marks some venv files read-only; rmtree fails on them without this.
    Path(target).chmod(stat.S_IWRITE)
    func(target)


def remove_tree(path: Path, attempts: int = 5, delay_s: float = 0.3) -> None:
    """Delete a folder. Retries briefly: on Windows a process that just exited (an agent
    server, an antivirus scan) can keep files locked for a moment."""
    for attempt in range(attempts):
        if not path.exists():
            return
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_clear_readonly_and_retry)
            else:
                shutil.rmtree(path, onerror=_clear_readonly_and_retry)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s)
