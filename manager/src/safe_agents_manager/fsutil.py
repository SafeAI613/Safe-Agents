"""File-system helpers."""

from __future__ import annotations

import shutil
import stat
import sys
from pathlib import Path
from typing import Any


def _clear_readonly_and_retry(func: Any, target: str, *_: Any) -> None:
    # Windows marks some venv files read-only; rmtree fails on them without this.
    Path(target).chmod(stat.S_IWRITE)
    func(target)


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)
    else:
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)
