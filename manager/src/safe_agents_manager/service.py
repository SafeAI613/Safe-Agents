"""``AgentManager``: the single entry point used by both the HTTP API and the CLI."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import httpx

from . import __version__
from .config import Settings
from .download import download_file, fetch_bytes
from .envbuilder import EnvBuilder, UvEnvBuilder
from .errors import (
    AgentAlreadyInstalledError,
    AgentNotFoundError,
    ArchiveError,
    DownloadError,
    InvalidRequestError,
    ManagerError,
    SecretStoreError,
)
from .files import read_readme, save_upload, write_export
from .fsutil import remove_tree
from .installer import EventSink, Installer, InstallEvent, InstallStep, SmokeTester
from .jobs import Job, JobRunner
from .manifest import parse_manifest
from .openrouter import OpenRouterClient, Pricing
from .ports import PortAllocator
from .registry import Catalog, RegistryClient, parse_version
from .secret_store import GLOBAL_KEYS, SecretProvider, SecretStore
from .store import AgentStore, InstalledAgent
from .supervisor import AgentProcess, CommandFactory, Supervisor, langgraph_dev_command, tail_lines
from .usage import UsageItem, UsageStore

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
        openrouter: OpenRouterClient | None = None,
    ) -> None:
        settings.ensure_dirs()
        self.settings = settings
        self.secrets = secrets
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
        self.usage = UsageStore(settings.db_path)
        self.openrouter = openrouter or OpenRouterClient(
            self.http, settings.home / "cache", base_url=settings.openrouter_url
        )
        self._stop_background = threading.Event()
        self.supervisor = Supervisor(
            settings, PortAllocator(*settings.port_range), secrets, command_factory
        )
        self.installer = Installer(
            settings,
            self.store,
            env_builder or UvEnvBuilder(settings),
            smoke_test or self.supervisor.probe,
            reserve=self.supervisor.reserved_for_install,
        )
        self.jobs = JobRunner()
        orphans = self.supervisor.reap_orphans()
        if orphans:
            log.warning("stopped %d agent servers left over from a previous run", len(orphans))
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
        info["token"] = handle.token if handle else None
        manifest = agent.manifest.raw
        info["examples"] = manifest.get("examples_of_suitable_questions", [])
        info["tasks"] = manifest.get("tasks_capable_of_performing", [])
        info["permissions"] = manifest.get("technical_specifications", {}).get(
            "required_permissions", []
        )
        info["models"] = agent.manifest.supported_models
        info["default_model"] = agent.manifest.runtime["default_model"]
        info["mcp_servers"] = [s["name"] for s in agent.manifest.runtime.get("mcp_servers", [])]
        info["missing_secrets"] = [
            key for key in agent.manifest.required_env if not self.secrets.get(key, agent.id)
        ]
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

    def registry_manifest(self, agent_id: str, version: str | None = None) -> dict[str, Any]:
        """The published manifest, shown before install (permissions, examples, features)."""
        item = self.registry.fetch().get(agent_id).get_version(version)
        status, body, _ = fetch_bytes(
            self.http,
            item.manifest_url,
            allowed_hosts=self.settings.allowed_hosts,
            max_bytes=1024 * 1024,
        )
        if status != 200:
            raise DownloadError(f"manifest download failed with HTTP {status}")
        try:
            manifest = parse_manifest(json.loads(body))
        except ValueError as exc:
            raise DownloadError(f"manifest is not valid JSON: {exc}") from exc
        specs = manifest.raw.get("technical_specifications", {})
        return {
            "id": agent_id,
            "version": manifest.version,
            "name": manifest.name,
            "description": manifest.raw.get("description"),
            "creator_name": manifest.raw.get("creator_name"),
            "contact_information": manifest.raw.get("contact_information"),
            "repository_url": manifest.raw.get("repository_url"),
            "examples": manifest.raw.get("examples_of_suitable_questions", []),
            "tasks": manifest.raw.get("tasks_capable_of_performing", []),
            "permissions": specs.get("required_permissions", []),
            "features": specs.get("features_enabled", {}),
            "models": manifest.supported_models,
            "secrets": manifest.secret_fields(),
        }

    def about(self) -> dict[str, Any]:
        settings = self.settings
        return {
            "version": __version__,
            "data_dir": str(settings.home),
            "agents_dir": str(settings.agents_dir),
            "logs_dir": str(settings.logs_dir),
            "exports_dir": str(settings.exports_dir),
            "registry_url": settings.registry_url,
        }

    def manager_log(self, tail: int = 200) -> list[str]:
        return tail_lines(self.settings.logs_dir / "manager.log", tail)

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

    def submit_install(
        self, archive: Path, *, expected_sha256: str | None = None, delete_after: bool = False
    ) -> Job:
        if not archive.is_file():
            raise ArchiveError(f"archive not found: {archive}")

        def work(on_event: EventSink) -> dict[str, Any]:
            try:
                agent = self.installer.install_archive(
                    archive, expected_sha256=expected_sha256, on_event=on_event
                )
                return agent.to_dict()
            finally:
                if delete_after:
                    archive.unlink(missing_ok=True)

        return self.jobs.submit("install", work)

    def submit_uploaded_install(self, filename: str, stream: BinaryIO) -> Job:
        """Install a zip the user picked in the app (not reviewed by the catalog CI)."""
        if not filename.lower().endswith(".zip"):
            raise InvalidRequestError("choose an agent .zip file")
        target = self.settings.downloads_dir / f"upload-{uuid4().hex}.zip"
        written = 0
        try:
            with target.open("wb") as out:
                while chunk := stream.read(1024 * 1024):
                    written += len(chunk)
                    if written > self.settings.max_archive_bytes:
                        raise ArchiveError(
                            f"archive is larger than {self.settings.max_archive_bytes} bytes"
                        )
                    out.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return self.submit_install(target, delete_after=True)

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
        agent = self.get_agent(agent_id)
        self.supervisor.stop(agent_id)
        self.store.delete(agent_id)
        remove_tree(self.settings.agents_dir / agent_id)
        if isinstance(self.secrets, SecretStore):
            keys = [field["key"] for field in agent.manifest.secret_fields()]
            try:
                self.secrets.delete_agent(agent_id, keys)
            except SecretStoreError as exc:
                log.warning("could not remove secrets of %s: %s", agent_id, exc)

    def start(self, agent_id: str) -> AgentProcess:
        return self.supervisor.start(self.get_agent(agent_id))

    def stop(self, agent_id: str) -> bool:
        self.get_agent(agent_id)
        return self.supervisor.stop(agent_id)

    def heartbeat(self, agent_id: str) -> bool:
        self.get_agent(agent_id)
        return self.supervisor.touch(agent_id)

    # -- settings and secrets ---------------------------------------------------------------

    def _writable_secrets(self) -> SecretStore:
        if not isinstance(self.secrets, SecretStore):
            raise SecretStoreError("secrets are read-only in this mode")
        return self.secrets

    def settings_status(self) -> dict[str, Any]:
        return {
            "openrouter_key_set": bool(self.secrets.get("OPENROUTER_API_KEY")),
            "langsmith_key_set": bool(self.secrets.get("LANGSMITH_API_KEY")),
            "idle_timeout_s": self.settings.idle_timeout_s,
            "exports_dir": str(self.settings.exports_dir),
        }

    def set_openrouter_key(self, value: str) -> dict[str, Any]:
        value = value.strip()
        info = self.openrouter.key_info(value)  # raises InvalidApiKeyError before storing
        self._writable_secrets().set("OPENROUTER_API_KEY", value)
        return info

    def openrouter_status(self) -> dict[str, Any]:
        key = self.secrets.get("OPENROUTER_API_KEY")
        if not key:
            raise InvalidRequestError("no OpenRouter key is set")
        return self.openrouter.key_info(key)

    def set_global_secret(self, key: str, value: str) -> None:
        if key not in GLOBAL_KEYS:
            raise InvalidRequestError(f"'{key}' is not a global setting")
        store = self._writable_secrets()
        if value.strip():
            store.set(key, value.strip())
        else:
            store.delete(key)

    def agent_secrets(self, agent_id: str) -> list[dict[str, Any]]:
        agent = self.get_agent(agent_id)
        fields = []
        for field in agent.manifest.secret_fields():
            key = field["key"]
            scope = "global" if key in GLOBAL_KEYS else "agent"
            fields.append({**field, "scope": scope, "set": bool(self.secrets.get(key, agent_id))})
        return fields

    def set_agent_secrets(self, agent_id: str, values: dict[str, str]) -> None:
        agent = self.get_agent(agent_id)
        declared = {f["key"] for f in agent.manifest.secret_fields()} - GLOBAL_KEYS
        unknown = set(values) - declared
        if unknown:
            raise InvalidRequestError(f"not declared by the agent: {', '.join(sorted(unknown))}")
        store = self._writable_secrets()
        for key, value in values.items():
            if value.strip():
                store.set(key, value.strip(), agent_id)
            else:
                store.delete(key, agent_id)

    # -- files ------------------------------------------------------------------------------

    def upload(self, agent_id: str, filename: str, stream: BinaryIO) -> Path:
        agent = self.get_agent(agent_id)
        return save_upload(
            stream,
            filename,
            agent.layout.inbox,
            allowed_extensions=agent.manifest.runtime.get("accepts_files", []),
            max_bytes=self.settings.max_upload_bytes,
        )

    def readme(self, agent_id: str) -> str | None:
        return read_readme(self.get_agent(agent_id).layout.src)

    def export(self, filename: str, content: str) -> Path:
        if len(content.encode("utf-8")) > 20 * 1024 * 1024:
            raise InvalidRequestError("export is too large")
        return write_export(self.settings.exports_dir, filename, content)

    # -- usage and cost ---------------------------------------------------------------------

    def pricing(self, *, refresh: bool = False) -> Pricing:
        return self.openrouter.pricing(refresh=refresh)

    def record_usage(self, agent_id: str, thread_id: str, items: list[UsageItem]) -> int:
        self.get_agent(agent_id)
        try:
            pricing = self.pricing()
        except ManagerError as exc:
            log.warning("recording usage without prices: %s", exc)
            pricing = {}
        return self.usage.record(agent_id, thread_id, items, pricing)

    # -- background work --------------------------------------------------------------------

    def start_background(self, interval_s: float = 30.0) -> None:
        """Stop agents that nobody used for ``idle_timeout_s`` (desktop mode only)."""
        if self.settings.idle_timeout_s <= 0:
            return

        def run() -> None:
            while not self._stop_background.wait(interval_s):
                self.supervisor.stop_idle(self.settings.idle_timeout_s)

        threading.Thread(target=run, name="idle-monitor", daemon=True).start()

    def shutdown(self) -> None:
        self._stop_background.set()
        self.supervisor.stop_all()
        self.jobs.shutdown()
        self.http.close()
