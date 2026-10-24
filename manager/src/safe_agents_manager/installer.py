"""Installing an agent from a zip archive: a sequence of steps with full rollback on failure."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .archive import safe_extract, verify_sha256
from .config import Settings
from .envbuilder import EnvBuilder
from .errors import (
    AgentAlreadyInstalledError,
    AgentRunningError,
    InstallError,
    ManagerError,
    ManifestError,
)
from .fsutil import remove_tree
from .layout import AgentLayout, write_langgraph_config
from .manifest import AgentManifest, load_manifest, validate_layout
from .store import AgentStore, InstalledAgent

log = logging.getLogger(__name__)

STAGING_PREFIX = ".staging-"


def _remove(path: Path) -> None:
    if path.is_dir():
        remove_tree(path)
    else:
        path.unlink(missing_ok=True)


class InstallStep(StrEnum):
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    CREATING_ENV = "creating_env"
    GENERATING_CONFIG = "generating_config"
    SMOKE_TEST = "smoke_test"
    ACTIVATING = "activating"
    INSTALLED = "installed"
    FAILED = "failed"


@dataclass(frozen=True)
class InstallEvent:
    step: InstallStep
    message: str
    # Detail events carry raw tool output (e.g. uv); the UI may show them in a log pane only.
    detail: bool = False
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step.value,
            "message": self.message,
            "detail": self.detail,
            "timestamp": self.timestamp,
        }


EventSink = Callable[[InstallEvent], None]


class SmokeTester(Protocol):
    def __call__(self, layout: AgentLayout, manifest: AgentManifest) -> None: ...


class Installer:
    def __init__(
        self,
        settings: Settings,
        store: AgentStore,
        env_builder: EnvBuilder,
        smoke_test: SmokeTester,
        is_running: Callable[[str], bool],
    ) -> None:
        self._settings = settings
        self._store = store
        self._env_builder = env_builder
        self._smoke_test = smoke_test
        self._is_running = is_running

    def install_archive(
        self,
        archive: Path,
        *,
        expected_sha256: str | None = None,
        expected_agent_id: str | None = None,
        expected_version: str | None = None,
        on_event: EventSink | None = None,
    ) -> InstalledAgent:
        def emit(step: InstallStep, message: str, detail: bool = False) -> None:
            if on_event is not None:
                on_event(InstallEvent(step, message, detail))

        agents_dir = self._settings.agents_dir
        agents_dir.mkdir(parents=True, exist_ok=True)
        staging = agents_dir / f"{STAGING_PREFIX}{uuid4().hex}"
        target: Path | None = None
        try:
            if expected_sha256:
                emit(InstallStep.VERIFYING, "verifying sha256 checksum")
                verify_sha256(archive, expected_sha256)
            else:
                emit(InstallStep.VERIFYING, "no checksum given (local install), skipping check")

            emit(InstallStep.EXTRACTING, f"extracting {archive.name}")
            safe_extract(
                archive,
                staging / "src",
                max_archive_bytes=self._settings.max_archive_bytes,
                max_extracted_bytes=self._settings.max_extracted_bytes,
                max_files=self._settings.max_archive_files,
            )

            emit(InstallStep.VALIDATING, "validating manifest.json and folder layout")
            manifest = load_manifest(staging / "src")
            validate_layout(manifest, staging / "src")
            self._check_expected(manifest, expected_agent_id, expected_version)
            previous = self._store.get(manifest.agent_id)
            if previous is not None and previous.version == manifest.version:
                raise AgentAlreadyInstalledError(
                    f"{manifest.agent_id} {manifest.version} is already installed"
                )
            if self._is_running(manifest.agent_id):
                raise AgentRunningError(f"stop {manifest.agent_id} before updating it")

            # A venv cannot be moved after creation, so the final folder is used from here on.
            target = agents_dir / manifest.agent_id / manifest.version
            remove_tree(target)  # leftover of an earlier crash, never referenced by the store
            target.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(target)
            layout = AgentLayout(target)
            layout.inbox.mkdir(exist_ok=True)

            emit(InstallStep.CREATING_ENV, f"creating Python environment ({manifest.python_spec})")
            self._env_builder.build(
                layout,
                manifest.python_spec,
                lambda line: emit(InstallStep.CREATING_ENV, line, detail=True),
            )

            if previous is not None and previous.layout.state_dir.is_dir():
                # Carry the conversations over; the old version is only removed on success.
                shutil.copytree(previous.layout.state_dir, layout.state_dir)

            emit(InstallStep.GENERATING_CONFIG, "writing langgraph.json")
            write_langgraph_config(layout, manifest)

            emit(InstallStep.SMOKE_TEST, "starting the agent server once to check the graph loads")
            self._smoke_test(layout, manifest)

            emit(InstallStep.ACTIVATING, "registering the agent")
            agent = self._store.save(manifest, target)
            target = None  # committed: nothing to roll back from here on
            if previous is not None and previous.install_dir != agent.install_dir:
                remove_tree(previous.install_dir)

            emit(InstallStep.INSTALLED, f"{manifest.name} {manifest.version} installed")
            return agent
        except ManagerError as exc:
            emit(InstallStep.FAILED, str(exc))
            raise
        except Exception as exc:
            log.exception("unexpected install failure")
            emit(InstallStep.FAILED, f"unexpected error: {exc}")
            raise InstallError(f"unexpected error: {exc}") from exc
        finally:
            remove_tree(staging)
            if target is not None:
                remove_tree(target)
                if target.parent.is_dir() and not any(target.parent.iterdir()):
                    target.parent.rmdir()

    @staticmethod
    def _check_expected(manifest: AgentManifest, agent_id: str | None, version: str | None) -> None:
        if agent_id is not None and manifest.agent_id != agent_id:
            raise ManifestError(
                f"archive contains agent '{manifest.agent_id}', expected '{agent_id}'"
            )
        if version is not None and manifest.version != version:
            raise ManifestError(f"archive contains version {manifest.version}, expected {version}")

    def cleanup_orphans(self) -> list[Path]:
        """Remove staging folders, unknown agents, and versions the store does not reference."""
        agents_dir = self._settings.agents_dir
        if not agents_dir.is_dir():
            return []
        installed = {agent.id: agent.install_dir.resolve() for agent in self._store.list()}
        removed: list[Path] = []
        for entry in agents_dir.iterdir():
            if not entry.is_dir() or entry.name not in installed:
                _remove(entry)
                removed.append(entry)
                continue
            for child in entry.iterdir():
                if child.resolve() == installed[entry.name]:
                    continue
                _remove(child)
                removed.append(child)
        return removed
