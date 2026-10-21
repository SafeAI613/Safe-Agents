"""Runtime settings and the on-disk layout of the manager's data directory."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "SafeAgents"


@dataclass(frozen=True)
class Settings:
    home: Path
    uv_path: Path
    port_range: tuple[int, int] = (2100, 2199)
    startup_timeout_s: float = 120.0
    stop_timeout_s: float = 10.0
    max_archive_bytes: int = 100 * 1024 * 1024
    max_extracted_bytes: int = 300 * 1024 * 1024
    max_archive_files: int = 10_000

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def python_dir(self) -> Path:
        return self.home / "runtime" / "python"

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache" / "uv"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def db_path(self) -> Path:
        return self.home / "db" / "manager.sqlite"

    def ensure_dirs(self) -> None:
        for path in (
            self.agents_dir,
            self.python_dir,
            self.cache_dir,
            self.logs_dir,
            self.db_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from ``SAFE_AGENTS_HOME`` / ``SAFE_AGENTS_UV`` or platform defaults."""
        home = Path(os.environ.get("SAFE_AGENTS_HOME") or user_data_dir(APP_NAME, appauthor=False))
        return cls(home=home, uv_path=find_uv())


def find_uv() -> Path:
    """Locate ``uv``: explicit env var, then a copy bundled next to the executable, then PATH."""
    explicit = os.environ.get("SAFE_AGENTS_UV")
    if explicit:
        return Path(explicit)
    exe_name = "uv.exe" if os.name == "nt" else "uv"
    bundled = Path(sys.executable).resolve().parent / exe_name
    if bundled.is_file():
        return bundled
    found = shutil.which("uv")
    if found:
        return Path(found)
    # Return the bare name; the env builder raises a clear error when it fails to run.
    return Path(exe_name)
