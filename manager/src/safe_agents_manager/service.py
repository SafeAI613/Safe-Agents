"""``AgentManager``: the single entry point used by both the HTTP API and the CLI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import Settings
from .envbuilder import EnvBuilder, UvEnvBuilder
from .errors import AgentNotFoundError, ArchiveError
from .fsutil import remove_tree
from .installer import EventSink, Installer, SmokeTester
from .jobs import Job, JobRunner
from .ports import PortAllocator
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
    ) -> None:
        settings.ensure_dirs()
        self.settings = settings
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
