"""``AgentManager``: the single entry point used by both the HTTP API and the CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from . import __version__
from .config import Settings
from .download import download_file
from .envbuilder import EnvBuilder, UvEnvBuilder
from .errors import (
    AgentAlreadyInstalledError,
    AgentNotFoundError,
    ArchiveError,
    DownloadError,
    ManagerError,
)
from .fsutil import remove_tree
from .installer import EventSink, Installer, InstallEvent, InstallStep, SmokeTester
from .jobs import Job, JobRunner
from .ports import PortAllocator
from .registry import Catalog, RegistryClient, parse_version
from .secret_store import SecretProvider
from .store import AgentStore, InstalledAgent
from .supervisor import AgentProcess, CommandFactory, Supervisor, langgraph_dev_command, tail_lines

log = logging.getLogger(__name__)


class AgentManager:
    def __init__(
        self,
        settings: Settings,
        secrets: SecretProvider,
        *,
        env_builder: EnvBuilder | None = None,
        command_factory: CommandFactory = langgraph_dev_command,
        smoke_test: SmokeTester | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        settings.ensure_dirs()
        self.settings = settings
        self.http = http_client or httpx.Client(
            timeout=settings.http_timeout_s,
            headers={"User-Agent": f"safe-agents-manager/{__version__}"},
        )
        self.registry = RegistryClient(
            settings.registry_url,
            settings.registry_cache_dir,
            self.http,
            settings.allowed_hosts,
            settings.registry_cache_ttl_s,
        )
        self.store = AgentStore(settings.db_path)
        self.supervisor = Supervisor(
            settings, PortAllocator(*settings.port_range), secrets, command_factory
        )
        self.installer = Installer(
            settings,
            self.store,
            env_builder or UvEnvBuilder(settings),
            smoke_test or self.supervisor.probe,
            is_running=lambda agent_id: self.supervisor.get(agent_id) is not None,
        )
        self.jobs = JobRunner()
        removed = self.installer.cleanup_orphans()
        if removed:
            log.info("removed %d leftover folders from interrupted installs", len(removed))

    # -- queries ----------------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> InstalledAgent:
        agent = self.store.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"agent '{agent_id}' is not installed")
        return agent

    def describe(self, agent: InstalledAgent) -> dict[str, Any]:
        info = agent.to_dict()
        handle = self.supervisor.get(agent.id)
        info["status"] = "running" if handle else "stopped"
        info["url"] = handle.url if handle else None
        return info

    def list_agents(self) -> list[dict[str, Any]]:
        return [self.describe(agent) for agent in self.store.list()]

    def catalog(self, *, refresh: bool = False) -> tuple[Catalog, list[dict[str, Any]]]:
        """The catalog plus, for each entry, what is installed and whether an update exists."""
        catalog = self.registry.fetch(refresh=refresh)
        installed = {agent.id: agent.version for agent in self.store.list()}
        rows = []
        for entry in catalog.entries:
            row = entry.to_dict()
            current = installed.get(entry.id)
            row["installed_version"] = current
            row["update_available"] = bool(
                current and parse_version(entry.latest) > parse_version(current)
            )
            rows.append(row)
        return catalog, rows

    def logs(self, agent_id: str, tail: int = 200) -> list[str]:
        self.get_agent(agent_id)
        return tail_lines(self.settings.logs_dir / f"{agent_id}.log", tail)

    # -- commands ---------------------------------------------------------------------------

    def install_archive(
        self,
        archive: Path,
        *,
        expected_sha256: str | None = None,
        on_event: EventSink | None = None,
    ) -> InstalledAgent:
        if not archive.is_file():
            raise ArchiveError(f"archive not found: {archive}")
        return self.installer.install_archive(
            archive, expected_sha256=expected_sha256, on_event=on_event
        )

    def submit_install(self, archive: Path, *, expected_sha256: str | None = None) -> Job:
        if not archive.is_file():
            raise ArchiveError(f"archive not found: {archive}")

        def work(on_event: EventSink) -> dict[str, Any]:
            agent = self.installer.install_archive(
                archive, expected_sha256=expected_sha256, on_event=on_event
            )
            return agent.to_dict()

        return self.jobs.submit("install", work)

    def install_from_registry(
        self,
        agent_id: str,
        version: str | None = None,
        *,
        refresh: bool = True,
        on_event: EventSink | None = None,
    ) -> InstalledAgent:
        def emit(step: InstallStep, message: str) -> None:
            if on_event is not None:
                on_event(InstallEvent(step, message))

        try:
            entry = self.registry.fetch(refresh=refresh).get(agent_id)
            item = entry.get_version(version)
            current = self.store.get(agent_id)
            if current is not None and current.version == item.version:
                raise AgentAlreadyInstalledError(f"{agent_id} {item.version} is already installed")
        except ManagerError as exc:
            emit(InstallStep.FAILED, str(exc))
            raise

        archive = self.settings.downloads_dir / f"{agent_id}-{item.version}-{uuid4().hex}.zip"
        last_reported = -1

        def progress(received: int, total: int | None) -> None:
            nonlocal last_reported
            if total:
                percent = received * 100 // total
                if percent >= last_reported + 10 or percent == 100:
                    last_reported = percent
                    emit(InstallStep.DOWNLOADING, f"{percent}%")

        try:
            emit(InstallStep.DOWNLOADING, f"downloading {entry.name} {item.version}")
            download_file(
                self.http,
                item.source_url,
                archive,
                allowed_hosts=self.settings.allowed_hosts,
                max_bytes=self.settings.max_archive_bytes,
                expected_sha256=item.sha256,
                on_progress=progress,
            )
        except ManagerError as exc:
            emit(InstallStep.FAILED, str(exc))
            archive.unlink(missing_ok=True)
            raise
        except OSError as exc:
            emit(InstallStep.FAILED, f"could not save the download: {exc}")
            archive.unlink(missing_ok=True)
            raise DownloadError(f"could not save the download: {exc}") from exc
        try:
            return self.installer.install_archive(
                archive,
                expected_sha256=item.sha256,
                expected_agent_id=agent_id,
                expected_version=item.version,
                on_event=on_event,
            )
        finally:
            archive.unlink(missing_ok=True)

    def submit_registry_install(self, agent_id: str, version: str | None = None) -> Job:
        def work(on_event: EventSink) -> dict[str, Any]:
            return self.install_from_registry(agent_id, version, on_event=on_event).to_dict()

        return self.jobs.submit("install", work)

    def uninstall(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        self.supervisor.stop(agent_id)
        self.store.delete(agent_id)
        remove_tree(self.settings.agents_dir / agent_id)

    def start(self, agent_id: str) -> AgentProcess:
        return self.supervisor.start(self.get_agent(agent_id))

    def stop(self, agent_id: str) -> bool:
        self.get_agent(agent_id)
        return self.supervisor.stop(agent_id)

    def shutdown(self) -> None:
        self.supervisor.stop_all()
        self.jobs.shutdown()
        self.http.close()
