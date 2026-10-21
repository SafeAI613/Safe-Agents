"""Validation of a ``registry.json`` change, run by the catalog repository's CI on each PR.

Levels of checking:

* always: JSON Schema, unique ids/versions, ``latest`` is the highest version, and published
  versions are immutable (compared with ``--base``, the registry on the target branch);
* ``deep``: for each new version, download the archive, check its sha256, extract it and
  check that its manifest matches the catalog entry and the published ``manifest_url``;
* ``install``: additionally run the real installer (uv + langgraph smoke test) in a
  throw-away folder, proving the graph loads.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .archive import safe_extract
from .config import Settings
from .download import download_file, fetch_bytes
from .errors import ManagerError
from .manifest import AgentManifest, load_manifest, validate_layout
from .registry import RegistryEntry, RegistryVersion, parse_registry
from .secret_store import StaticSecretProvider

MAX_MANIFEST_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Finding:
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}"


InstallCheck = Callable[[Path, RegistryEntry, RegistryVersion], None]


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _published(base: Path | None) -> dict[tuple[str, str], RegistryVersion]:
    if base is None or not base.is_file():
        return {}
    entries = parse_registry(_load(base))
    return {(e.id, v.version): v for e in entries for v in e.versions}


def _compare_manifest(
    manifest: AgentManifest, entry: RegistryEntry, item: RegistryVersion
) -> list[str]:
    problems = []
    if manifest.agent_id != entry.id:
        problems.append(f"manifest graph_id is '{manifest.agent_id}', catalog id is '{entry.id}'")
    if manifest.version != item.version:
        problems.append(f"manifest version is {manifest.version}, catalog says {item.version}")
    if manifest.name != entry.name:
        problems.append("manifest name differs from the catalog name")
    if manifest.raw.get("description") != entry.description:
        problems.append("manifest description differs from the catalog description")
    if manifest.runtime.get("source_url") != item.source_url:
        problems.append("manifest runtime.source_url differs from the catalog source_url")
    return problems


class RegistryValidator:
    def __init__(
        self,
        settings: Settings,
        client: httpx.Client,
        install_check: InstallCheck | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._install_check = install_check

    def validate(
        self, registry: Path, *, base: Path | None = None, deep: bool = False
    ) -> list[Finding]:
        try:
            entries = parse_registry(_load(registry))
            published = _published(base)
        except (ManagerError, ValueError) as exc:
            return [Finding(registry.name, str(exc))]

        findings: list[Finding] = []
        current = {(e.id, v.version): v for e in entries for v in e.versions}
        for key, old in published.items():
            new = current.get(key)
            if new is not None and new != old:
                findings.append(
                    Finding(
                        f"{key[0]}@{key[1]}",
                        "a published version must never change; release a new version instead",
                    )
                )

        if deep:
            for entry in entries:
                for item in entry.versions:
                    if published.get((entry.id, item.version)) == item:
                        continue  # unchanged and already verified when it was added
                    findings.extend(self._check_version(entry, item))
        return findings

    def _check_version(self, entry: RegistryEntry, item: RegistryVersion) -> list[Finding]:
        where = f"{entry.id}@{item.version}"
        with tempfile.TemporaryDirectory(prefix="registry-check-") as tmp:
            tmp_dir = Path(tmp)
            archive = tmp_dir / "agent.zip"
            try:
                download_file(
                    self._client,
                    item.source_url,
                    archive,
                    allowed_hosts=self._settings.allowed_hosts,
                    max_bytes=self._settings.max_archive_bytes,
                    expected_sha256=item.sha256,
                )
                src = tmp_dir / "src"
                safe_extract(
                    archive,
                    src,
                    max_archive_bytes=self._settings.max_archive_bytes,
                    max_extracted_bytes=self._settings.max_extracted_bytes,
                    max_files=self._settings.max_archive_files,
                )
                manifest = load_manifest(src)
                validate_layout(manifest, src)
            except ManagerError as exc:
                return [Finding(where, str(exc))]

            problems = _compare_manifest(manifest, entry, item)
            problems.extend(self._check_published_manifest(manifest, item))
            if problems:
                return [Finding(where, p) for p in problems]
            if self._install_check is not None:
                try:
                    self._install_check(archive, entry, item)
                except ManagerError as exc:
                    return [Finding(where, f"install check failed: {exc}")]
        return []

    def _check_published_manifest(
        self, manifest: AgentManifest, item: RegistryVersion
    ) -> list[str]:
        # The store displays manifest_url before download; it must match what gets installed.
        try:
            status, body, _ = fetch_bytes(
                self._client,
                item.manifest_url,
                allowed_hosts=self._settings.allowed_hosts,
                max_bytes=MAX_MANIFEST_BYTES,
            )
        except ManagerError as exc:
            return [f"manifest_url: {exc}"]
        if status != 200:
            return [f"manifest_url returned HTTP {status}"]
        try:
            published = json.loads(body)
        except ValueError:
            return ["manifest_url is not valid JSON"]
        if published != manifest.raw:
            return ["manifest_url content differs from manifest.json inside the archive"]
        return []


def real_install_check(settings: Settings) -> InstallCheck:
    """Install into a temporary home with the real uv environment and smoke test."""
    from .service import AgentManager

    def check(archive: Path, entry: RegistryEntry, item: RegistryVersion) -> None:
        with tempfile.TemporaryDirectory(prefix="registry-install-") as home:
            scratch = dataclasses.replace(settings, home=Path(home))
            manager = AgentManager(scratch, StaticSecretProvider({}))
            try:
                manager.installer.install_archive(
                    archive,
                    expected_sha256=item.sha256,
                    expected_agent_id=entry.id,
                    expected_version=item.version,
                )
            finally:
                manager.shutdown()

    return check
