from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import FakeEnvBuilder, manifest

from safe_agents_manager.config import Settings
from safe_agents_manager.errors import (
    AgentAlreadyInstalledError,
    AgentRunningError,
    ArchiveError,
    InstallError,
    ManifestError,
)
from safe_agents_manager.installer import Installer, InstallEvent, InstallStep
from safe_agents_manager.layout import AgentLayout
from safe_agents_manager.manifest import AgentManifest
from safe_agents_manager.store import AgentStore

MakeZip = Callable[..., Path]


class SmokeRecorder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[Path] = []
        self.fail = fail

    def __call__(self, layout: AgentLayout, manifest: AgentManifest) -> None:
        self.calls.append(layout.root)
        assert layout.langgraph_config.is_file(), "config must exist before the smoke test"
        if self.fail:
            raise InstallError("graph failed to load")


def make_installer(
    settings: Settings,
    env_builder: FakeEnvBuilder | None = None,
    smoke: SmokeRecorder | None = None,
    running: bool = False,
) -> tuple[Installer, AgentStore]:
    settings.ensure_dirs()
    store = AgentStore(settings.db_path)
    installer = Installer(
        settings,
        store,
        env_builder or FakeEnvBuilder(),
        smoke or SmokeRecorder(),
        is_running=lambda _id: running,
    )
    return installer, store


def leftovers(settings: Settings) -> list[Path]:
    return sorted(p for p in settings.agents_dir.rglob("*"))


def test_successful_install_emits_steps_in_order(
    settings: Settings, make_agent_zip: MakeZip
) -> None:
    installer, store = make_installer(settings)
    events: list[InstallEvent] = []
    agent = installer.install_archive(make_agent_zip(), on_event=events.append)

    assert agent.install_dir == settings.agents_dir / "foo_agent" / "1.0.0"
    assert (agent.install_dir / "src" / "agent" / "graph.py").is_file()
    assert (agent.install_dir / "langgraph.json").is_file()
    assert (agent.install_dir / "inbox").is_dir()
    assert store.get("foo_agent") is not None
    steps = [e.step for e in events if not e.detail]
    assert steps == [
        InstallStep.VERIFYING,
        InstallStep.EXTRACTING,
        InstallStep.VALIDATING,
        InstallStep.CREATING_ENV,
        InstallStep.GENERATING_CONFIG,
        InstallStep.SMOKE_TEST,
        InstallStep.ACTIVATING,
        InstallStep.INSTALLED,
    ]
    assert any(e.detail and e.message == "fake uv output" for e in events)
    assert not list(settings.agents_dir.glob(".staging-*"))


def test_checksum_is_verified(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, store = make_installer(settings)
    archive = make_agent_zip()
    with pytest.raises(ArchiveError, match="mismatch"):
        installer.install_archive(archive, expected_sha256="0" * 64)
    assert store.list() == []
    good = hashlib.sha256(archive.read_bytes()).hexdigest()
    installer.install_archive(archive, expected_sha256=good)


@pytest.mark.parametrize(
    ("env_builder", "smoke", "error"),
    [
        (FakeEnvBuilder(fail_with=InstallError("uv failed")), None, InstallError),
        (None, SmokeRecorder(fail=True), InstallError),
        (FakeEnvBuilder(fail_with=RuntimeError("bug")), None, InstallError),
    ],
)
def test_failure_rolls_back_everything(
    settings: Settings,
    make_agent_zip: MakeZip,
    env_builder: FakeEnvBuilder | None,
    smoke: SmokeRecorder | None,
    error: type[Exception],
) -> None:
    installer, store = make_installer(settings, env_builder, smoke)
    events: list[InstallEvent] = []
    with pytest.raises(error):
        installer.install_archive(make_agent_zip(), on_event=events.append)
    assert events[-1].step is InstallStep.FAILED
    assert store.list() == []
    assert leftovers(settings) == []


def test_invalid_manifest_leaves_nothing(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, _ = make_installer(settings)
    with pytest.raises(ManifestError):
        installer.install_archive(make_agent_zip(manifest(graph_id="X")))
    assert leftovers(settings) == []


def test_same_version_twice_is_rejected_and_keeps_existing(
    settings: Settings, make_agent_zip: MakeZip
) -> None:
    installer, store = make_installer(settings)
    first = installer.install_archive(make_agent_zip())
    with pytest.raises(AgentAlreadyInstalledError):
        installer.install_archive(make_agent_zip())
    assert (first.install_dir / "langgraph.json").is_file()
    assert store.get("foo_agent") is not None


def test_update_replaces_previous_version(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, store = make_installer(settings)
    old = installer.install_archive(make_agent_zip(manifest("1.0.0")))
    new = installer.install_archive(make_agent_zip(manifest("1.1.0")))
    assert not old.install_dir.exists()
    assert new.install_dir.is_dir()
    stored = store.get("foo_agent")
    assert stored is not None and stored.version == "1.1.0"


def test_failed_update_keeps_previous_version(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, store = make_installer(settings)
    old = installer.install_archive(make_agent_zip(manifest("1.0.0")))
    failing, _ = make_installer(settings, smoke=SmokeRecorder(fail=True))
    with pytest.raises(InstallError):
        failing.install_archive(make_agent_zip(manifest("1.1.0")))
    assert old.install_dir.is_dir()
    stored = store.get("foo_agent")
    assert stored is not None and stored.version == "1.0.0"


def test_update_refused_while_running(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, _ = make_installer(settings, running=True)
    with pytest.raises(AgentRunningError):
        installer.install_archive(make_agent_zip())


def test_expected_identity_is_enforced(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, _ = make_installer(settings)
    with pytest.raises(ManifestError, match="expected 'bar_agent'"):
        installer.install_archive(make_agent_zip(), expected_agent_id="bar_agent")
    with pytest.raises(ManifestError, match="expected 2.0.0"):
        installer.install_archive(make_agent_zip(), expected_version="2.0.0")


def test_cleanup_orphans(settings: Settings, make_agent_zip: MakeZip) -> None:
    installer, _ = make_installer(settings)
    agent = installer.install_archive(make_agent_zip())
    (settings.agents_dir / ".staging-dead").mkdir()
    (settings.agents_dir / "foo_agent" / "9.9.9").mkdir()
    (settings.agents_dir / "ghost" / "1.0.0").mkdir(parents=True)
    removed = installer.cleanup_orphans()
    assert len(removed) == 3
    assert agent.install_dir.is_dir()
    assert not (settings.agents_dir / "ghost").exists()
