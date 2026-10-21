"""Files that cross the boundary between the user and an agent: uploads and exports."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from .errors import InvalidRequestError

_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)
_CHUNK = 1024 * 1024
MAX_README_BYTES = 200 * 1024


def safe_filename(name: str, *, max_length: int = 100) -> str:
    """A plain file name: no folders, no control or shell characters, never empty or hidden."""
    base = Path(name.replace("\\", "/")).name
    base = unicodedata.normalize("NFC", base)
    base = _UNSAFE.sub("_", base).strip(" .")
    if not base:
        base = "file"
    stem, dot, ext = base.rpartition(".")
    if dot and stem:
        ext = ext[:16]
        return f"{stem[: max_length - len(ext) - 1]}.{ext}"
    return base[:max_length]


def save_upload(
    stream: BinaryIO,
    filename: str,
    inbox: Path,
    *,
    allowed_extensions: list[str],
    max_bytes: int,
) -> Path:
    name = safe_filename(filename)
    ext = Path(name).suffix.lower()
    if not allowed_extensions:
        raise InvalidRequestError("this agent does not accept files")
    if ext not in {e.lower() for e in allowed_extensions}:
        allowed = ", ".join(allowed_extensions)
        raise InvalidRequestError(f"file type '{ext or '?'}' is not accepted (allowed: {allowed})")
    inbox.mkdir(parents=True, exist_ok=True)
    # A unique prefix keeps two uploads with the same name from overwriting each other.
    target = inbox / f"{uuid4().hex[:8]}-{name}"
    written = 0
    try:
        with target.open("wb") as out:
            while chunk := stream.read(_CHUNK):
                written += len(chunk)
                if written > max_bytes:
                    raise InvalidRequestError(
                        f"file is larger than {max_bytes // (1024 * 1024)} MB"
                    )
                out.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target.resolve()


def write_export(directory: Path, filename: str, content: str) -> Path:
    name = safe_filename(filename)
    if Path(name).suffix.lower() not in (".md", ".txt"):
        name += ".md"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    stem, suffix = target.stem, target.suffix
    counter = 1
    while target.exists():
        target = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    target.write_text(content, encoding="utf-8")
    return target


def read_readme(src: Path) -> str | None:
    for name in ("README.md", "readme.md", "README.txt"):
        path = src / name
        if path.is_file():
            data = path.read_bytes()[:MAX_README_BYTES]
            return data.decode("utf-8", errors="replace")
    return None
