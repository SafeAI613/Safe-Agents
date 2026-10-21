"""Runtime settings and the on-disk layout of the manager's data directory."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir, user_downloads_dir

APP_NAME = "SafeAgents"
DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/SafeAI613/safe-agents-registry/main/registry.json"
)
# Agents and the catalog are published on GitHub; downloads from anywhere else are refused.
DEFAULT_ALLOWED_HOSTS = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)


# Origins of the Tauri webview (macOS/Linux use tauri://, Windows uses http(s)://tauri.localhost).
DEFAULT_CORS_ORIGINS = ("tauri://localhost", "http://tauri.localhost", "https://tauri.localhost")


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
    # An https URL, or a local file path (handy while developing a catalog).
    registry_url: str = DEFAULT_REGISTRY_URL
    registry_cache_ttl_s: float = 300.0
    allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_HOSTS
    http_timeout_s: float = 30.0
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # Agents nobody has used for this long are stopped to free memory (0 disables).
    idle_timeout_s: float = 900.0
    max_upload_bytes: int = 50 * 1024 * 1024
    # Where "export conversation" writes; None means the user's Downloads folder.
    export_dir: Path | None = None

    @property
    def exports_dir(self) -> Path:
        return self.export_dir or Path(user_downloads_dir())

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
    def registry_cache_dir(self) -> Path:
        return self.home / "cache" / "registry"

    @property
    def downloads_dir(self) -> Path:
        return self.home / "downloads"

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
            self.registry_cache_dir,
            self.downloads_dir,
            self.db_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from ``SAFE_AGENTS_HOME`` / ``SAFE_AGENTS_UV`` or platform defaults."""
        home = Path(os.environ.get("SAFE_AGENTS_HOME") or user_data_dir(APP_NAME, appauthor=False))
        registry_url = os.environ.get("SAFE_AGENTS_REGISTRY_URL") or DEFAULT_REGISTRY_URL
        # Extra origins, e.g. the Vite dev server (http://localhost:1420) during development.
        extra = os.environ.get("SAFE_AGENTS_CORS_ORIGINS", "")
        origins = DEFAULT_CORS_ORIGINS + tuple(o.strip() for o in extra.split(",") if o.strip())
        return cls(home=home, uv_path=find_uv(), registry_url=registry_url, cors_origins=origins)


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
