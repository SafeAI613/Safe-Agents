"""The central agent catalog (``registry.json``): parsing, validation and cached fetching."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator, FormatChecker

from .download import fetch_bytes
from .errors import (
    AgentNotFoundError,
    DownloadError,
    RegistryError,
    RegistryUnavailableError,
)

log = logging.getLogger(__name__)

MAX_REGISTRY_BYTES = 5 * 1024 * 1024


def parse_version(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


@cache
def _validator() -> Draft202012Validator:
    text = resources.files(__package__).joinpath("schemas/registry.schema.json").read_text("utf-8")
    return Draft202012Validator(json.loads(text), format_checker=FormatChecker())


@dataclass(frozen=True)
class RegistryVersion:
    version: str
    manifest_url: str
    source_url: str
    sha256: str


@dataclass(frozen=True)
class RegistryEntry:
    id: str
    name: str
    description: str
    creator_name: str | None
    latest: str
    versions: tuple[RegistryVersion, ...]

    def get_version(self, version: str | None = None) -> RegistryVersion:
        wanted = version or self.latest
        for item in self.versions:
            if item.version == wanted:
                return item
        raise AgentNotFoundError(f"version {wanted} of '{self.id}' is not in the catalog")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "creator_name": self.creator_name,
            "latest": self.latest,
            "versions": [v.version for v in self.versions],
            "manifest_url": self.get_version().manifest_url,
        }


@dataclass(frozen=True)
class Catalog:
    entries: tuple[RegistryEntry, ...]
    fetched_at: str
    stale: bool = False

    def get(self, agent_id: str) -> RegistryEntry:
        for entry in self.entries:
            if entry.id == agent_id:
                return entry
        raise AgentNotFoundError(f"agent '{agent_id}' is not in the catalog")


def parse_registry(data: Any) -> tuple[RegistryEntry, ...]:
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors[:10]
        )
        raise RegistryError(f"registry.json is invalid: {details}")

    entries: list[RegistryEntry] = []
    seen_ids: set[str] = set()
    for raw in data["agents"]:
        agent_id = raw["id"]
        if agent_id in seen_ids:
            raise RegistryError(f"agent '{agent_id}' appears more than once")
        seen_ids.add(agent_id)
        versions = tuple(
            RegistryVersion(v["version"], v["manifest_url"], v["source_url"], v["sha256"])
            for v in raw["versions"]
        )
        numbers = [v.version for v in versions]
        if len(set(numbers)) != len(numbers):
            raise RegistryError(f"agent '{agent_id}' lists the same version twice")
        if raw["latest"] not in numbers:
            raise RegistryError(f"agent '{agent_id}': latest {raw['latest']} is not in versions")
        if raw["latest"] != max(numbers, key=parse_version):
            raise RegistryError(f"agent '{agent_id}': latest must be the highest version")
        entries.append(
            RegistryEntry(
                id=agent_id,
                name=raw["name"],
                description=raw["description"],
                creator_name=raw.get("creator_name"),
                latest=raw["latest"],
                versions=versions,
            )
        )
    return tuple(entries)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RegistryClient:
    """Fetches the catalog with ``If-None-Match`` and keeps a validated copy on disk.

    If the network is down the last good copy is returned and marked ``stale``, so the
    desktop app still shows the store offline.
    """

    def __init__(
        self,
        url: str,
        cache_dir: Path,
        client: httpx.Client,
        allowed_hosts: frozenset[str],
        ttl_s: float,
    ) -> None:
        self._url = url
        self._cache_file = cache_dir / "registry.json"
        self._meta_file = cache_dir / "registry.meta.json"
        self._client = client
        self._allowed_hosts = allowed_hosts
        self._ttl_s = ttl_s

    @property
    def is_local(self) -> bool:
        return not self._url.startswith(("https://", "http://"))

    def fetch(self, *, refresh: bool = False) -> Catalog:
        if self.is_local:
            return self._load_local()
        meta = self._read_meta()
        cached = self._read_cache()
        if (
            cached is not None
            and not refresh
            and time.time() - meta.get("checked", 0) < self._ttl_s
        ):
            return Catalog(cached, meta.get("fetched_at", _now()))

        headers = {"If-None-Match": meta["etag"]} if cached is not None and meta.get("etag") else {}
        try:
            status, body, response_headers = fetch_bytes(
                self._client,
                self._url,
                allowed_hosts=self._allowed_hosts,
                max_bytes=MAX_REGISTRY_BYTES,
                headers=headers,
            )
        except DownloadError as exc:
            return self._fallback(cached, meta, str(exc))

        if status == 304 and cached is not None:
            self._write_meta({**meta, "checked": time.time()})
            return Catalog(cached, meta.get("fetched_at", _now()))
        if status != 200:
            return self._fallback(cached, meta, f"HTTP {status}")
        try:
            entries = parse_registry(json.loads(body))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RegistryError(f"registry.json is not valid JSON: {exc}") from exc
        # Only a catalog that passed validation replaces the cached copy.
        fetched_at = _now()
        self._write_cache(body)
        self._write_meta(
            {"etag": response_headers.get("etag"), "fetched_at": fetched_at, "checked": time.time()}
        )
        return Catalog(entries, fetched_at)

    def _load_local(self) -> Catalog:
        path = Path(self._url)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RegistryUnavailableError(f"cannot read local registry {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RegistryError(f"registry.json is not valid JSON: {exc}") from exc
        return Catalog(parse_registry(data), _now())

    def _fallback(
        self, cached: tuple[RegistryEntry, ...] | None, meta: dict[str, Any], reason: str
    ) -> Catalog:
        if cached is None:
            raise RegistryUnavailableError(f"the agent catalog is unavailable ({reason})")
        log.warning("using cached catalog: %s", reason)
        return Catalog(cached, meta.get("fetched_at", _now()), stale=True)

    def _read_cache(self) -> tuple[RegistryEntry, ...] | None:
        try:
            return parse_registry(json.loads(self._cache_file.read_text(encoding="utf-8")))
        except (OSError, ValueError, RegistryError):
            return None

    def _read_meta(self) -> dict[str, Any]:
        try:
            meta: dict[str, Any] = json.loads(self._meta_file.read_text(encoding="utf-8"))
            return meta
        except (OSError, ValueError):
            return {}

    def _write_cache(self, body: bytes) -> None:
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(self._cache_file)

    def _write_meta(self, meta: dict[str, Any]) -> None:
        self._meta_file.parent.mkdir(parents=True, exist_ok=True)
        self._meta_file.write_text(json.dumps(meta), encoding="utf-8")
