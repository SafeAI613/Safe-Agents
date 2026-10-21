"""Safe handling of downloaded agent archives (hash check and zip extraction)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .errors import ArchiveError

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024
# Files that must never be installed from an archive, even if a developer committed them.
_BLOCKED_NAMES = frozenset({".env"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256_file(path)
    if not hmac.compare_digest(actual, expected.strip().lower()):
        raise ArchiveError(f"sha256 mismatch: expected {expected}, got {actual}")


def _entry_parts(info: zipfile.ZipInfo) -> tuple[str, ...]:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or (path.parts and ":" in path.parts[0]):
        raise ArchiveError(f"archive entry has an absolute path: {info.filename!r}")
    parts = tuple(p for p in path.parts if p not in ("", "."))
    if ".." in parts:
        raise ArchiveError(f"archive entry escapes the target folder: {info.filename!r}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ArchiveError(f"archive entry is a symbolic link: {info.filename!r}")
    return parts


def _common_root(entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]]) -> str | None:
    """GitHub archives wrap everything in ``<repo>-<tag>/``; detect that single top folder."""
    roots = {parts[0] for _, parts in entries if parts}
    if len(roots) != 1:
        return None
    root = roots.pop()
    has_nested = any(len(parts) > 1 for _, parts in entries)
    file_at_root = any(len(parts) == 1 and not info.is_dir() for info, parts in entries)
    return root if has_nested and not file_at_root else None


def safe_extract(
    archive: Path,
    dest: Path,
    *,
    max_archive_bytes: int,
    max_extracted_bytes: int,
    max_files: int,
) -> None:
    """Extract ``archive`` into ``dest``, rejecting path traversal, symlinks and zip bombs."""
    if archive.stat().st_size > max_archive_bytes:
        raise ArchiveError(f"archive is larger than {max_archive_bytes} bytes")
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a valid zip file: {exc}") from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > max_files:
            raise ArchiveError(f"archive contains more than {max_files} entries")
        entries = [(info, _entry_parts(info)) for info in infos]
        root = _common_root(entries)
        dest.mkdir(parents=True, exist_ok=True)
        dest_resolved = dest.resolve()
        budget = max_extracted_bytes

        for info, parts in entries:
            if root is not None:
                parts = parts[1:]
            if not parts:
                continue
            if parts[-1] in _BLOCKED_NAMES:
                log.warning("skipping blocked file in archive: %s", "/".join(parts))
                continue
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(dest_resolved):
                raise ArchiveError(f"archive entry escapes the target folder: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Count real bytes while copying: the sizes declared in the zip header can lie.
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(_CHUNK):
                    budget -= len(chunk)
                    if budget < 0:
                        raise ArchiveError(
                            f"archive expands to more than {max_extracted_bytes} bytes"
                        )
                    out.write(chunk)
