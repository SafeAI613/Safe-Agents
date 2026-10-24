from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from safe_agents_manager import fsutil


def test_removes_read_only_files(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.txt"
    target.parent.mkdir()
    target.write_text("x")
    target.chmod(0o444)
    fsutil.remove_tree(tmp_path / "a")
    assert not (tmp_path / "a").exists()


def test_retries_while_files_are_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()
    calls = {"n": 0}
    real_rmtree = shutil.rmtree

    def flaky(path: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("in use by another process")
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(fsutil.shutil, "rmtree", flaky)
    fsutil.remove_tree(folder, delay_s=0)
    assert calls["n"] == 3 and not folder.exists()

    folder.mkdir()
    calls["n"] = -100
    with pytest.raises(PermissionError):
        fsutil.remove_tree(folder, attempts=2, delay_s=0)
