from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path

import pytest
from conftest import write_zip

from safe_agents_manager.archive import safe_extract, sha256_file, verify_sha256
from safe_agents_manager.errors import ArchiveError

LIMITS = {"max_archive_bytes": 10_000_000, "max_extracted_bytes": 10_000_000, "max_files": 100}


def test_strips_single_github_root_folder(tmp_path: Path) -> None:
    archive = write_zip(
        tmp_path / "a.zip", {"repo-1.0/manifest.json": "{}", "repo-1.0/x/y.txt": "z"}
    )
    safe_extract(archive, tmp_path / "out", **LIMITS)
    assert (tmp_path / "out" / "manifest.json").is_file()
    assert (tmp_path / "out" / "x" / "y.txt").read_text() == "z"


def test_keeps_layout_when_files_at_root(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "a.zip", {"manifest.json": "{}", "agent/graph.py": ""})
    safe_extract(archive, tmp_path / "out", **LIMITS)
    assert (tmp_path / "out" / "agent" / "graph.py").is_file()


@pytest.mark.parametrize(
    "name", ["../evil.txt", "a/../../evil.txt", "/etc/evil", "C:/evil.txt", "..\\evil.txt"]
)
def test_rejects_path_traversal(tmp_path: Path, name: str) -> None:
    archive = write_zip(tmp_path / "a.zip", {name: "x", "ok.txt": "y"})
    with pytest.raises(ArchiveError):
        safe_extract(archive, tmp_path / "out", **LIMITS)
    assert not (tmp_path / "evil.txt").exists()


def test_rejects_symlinks(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ArchiveError, match="symbolic link"):
        safe_extract(archive, tmp_path / "out", **LIMITS)


def test_rejects_zip_bomb_by_real_size(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\0" * 2_000_000)
    with pytest.raises(ArchiveError, match="expands"):
        safe_extract(archive, tmp_path / "out", **{**LIMITS, "max_extracted_bytes": 1_000_000})


def test_rejects_too_many_files(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "a.zip", {f"f{i}.txt": "" for i in range(5)})
    with pytest.raises(ArchiveError, match="entries"):
        safe_extract(archive, tmp_path / "out", **{**LIMITS, "max_files": 3})


def test_rejects_non_zip(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    archive.write_text("not a zip")
    with pytest.raises(ArchiveError, match="valid zip"):
        safe_extract(archive, tmp_path / "out", **LIMITS)


def test_skips_dotenv_files(tmp_path: Path) -> None:
    archive = write_zip(tmp_path / "a.zip", {"r/.env": "OPENROUTER_API_KEY=leak", "r/a.txt": "x"})
    safe_extract(archive, tmp_path / "out", **LIMITS)
    assert not (tmp_path / "out" / ".env").exists()
    assert (tmp_path / "out" / "a.txt").exists()


def test_sha256(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"foo")
    expected = hashlib.sha256(b"foo").hexdigest()
    assert sha256_file(path) == expected
    verify_sha256(path, expected.upper())
    with pytest.raises(ArchiveError, match="mismatch"):
        verify_sha256(path, "0" * 64)
