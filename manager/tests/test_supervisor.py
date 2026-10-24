from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from conftest import FakeEnvBuilder, fake_server_command, manifest

from safe_agents_manager.config import Settings
from safe_agents_manager.errors import AgentStartError, MissingSecretError
from safe_agents_manager.layout import AgentLayout
from safe_agents_manager.manifest import parse_manifest
from safe_agents_manager.ports import PortAllocator
from safe_agents_manager.secret_store import StaticSecretProvider
from safe_agents_manager.service import AgentManager
from safe_agents_manager.supervisor import Supervisor, build_agent_env

MakeZip = Callable[..., Path]


def test_env_contains_only_base_vars_and_declared_secrets() -> None:
    parsed = parse_manifest(manifest(env_required=["BAR_KEY"], env_optional=["BAZ_KEY"]))
    secrets = StaticSecretProvider(
        {"OPENROUTER_API_KEY": "sk", "BAR_KEY": "b", "BAZ_KEY": "z", "OTHER_SECRET": "x"}
    )
    env = build_agent_env(
        parsed, secrets, strict=True, base_environ={"PATH": "/bin", "AWS_SECRET": "leak"}
    )
    assert env["PATH"] == "/bin"
    assert env["OPENROUTER_API_KEY"] == "sk"
    assert env["BAR_KEY"] == "b"
    assert env["BAZ_KEY"] == "z"
    assert "OTHER_SECRET" not in env
    assert "AWS_SECRET" not in env
    assert env["LANGGRAPH_CLI_NO_ANALYTICS"] == "1"


def test_missing_required_secret() -> None:
    parsed = parse_manifest(manifest())
    with pytest.raises(MissingSecretError, match="OPENROUTER_API_KEY"):
        build_agent_env(parsed, StaticSecretProvider({}), strict=True, base_environ={})
    env = build_agent_env(parsed, StaticSecretProvider({}), strict=False, base_environ={})
    assert "OPENROUTER_API_KEY" not in env


def test_start_stop_lifecycle(manager: AgentManager, make_agent_zip: MakeZip) -> None:
    agent = manager.install_archive(make_agent_zip())
    handle = manager.start(agent.id)
    assert httpx.get(f"{handle.url}/ok").status_code == 200
    assert manager.start(agent.id) is handle, "start is idempotent"
    assert manager.describe(agent)["status"] == "running"

    child_env = json.loads((agent.install_dir / "env.json").read_text())
    assert child_env["OPENROUTER_API_KEY"] == "sk-test"
    assert "UNRELATED_SECRET" not in child_env

    assert manager.stop(agent.id) is True
    assert handle.process.poll() is not None
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"{handle.url}/ok", timeout=1)
    assert manager.stop(agent.id) is False
    assert manager.describe(agent)["status"] == "stopped"


def test_two_agents_get_different_ports(manager: AgentManager, make_agent_zip: MakeZip) -> None:
    first = manager.install_archive(make_agent_zip())
    second = manager.install_archive(make_agent_zip(manifest(graph_id="bar_agent")))
    a, b = manager.start(first.id), manager.start(second.id)
    assert a.port != b.port


def test_crash_on_startup_reports_log_tail(
    settings: Settings, secrets: StaticSecretProvider, make_agent_zip: MakeZip
) -> None:
    def crashing(_layout: AgentLayout, _port: int) -> list[str]:
        return [sys.executable, "-c", "print('boom: graph import failed'); raise SystemExit(3)"]

    mgr = AgentManager(
        settings,
        secrets,
        env_builder=FakeEnvBuilder(),
        command_factory=crashing,
        smoke_test=lambda *_: None,
    )
    agent = mgr.install_archive(make_agent_zip())
    with pytest.raises(AgentStartError, match="boom: graph import failed"):
        mgr.start(agent.id)
    assert mgr.supervisor.get(agent.id) is None


def test_health_timeout(tmp_path: Path, settings: Settings) -> None:
    def sleeper(_layout: AgentLayout, _port: int) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    fast = Settings(**{**settings.__dict__, "startup_timeout_s": 1.0})
    supervisor = Supervisor(fast, PortAllocator(23300, 23301), StaticSecretProvider({}), sleeper)
    layout = AgentLayout(tmp_path)
    with pytest.raises(AgentStartError, match="did not become healthy"):
        supervisor.probe(layout, parse_manifest(manifest()))


def test_probe_detects_missing_graph(
    tmp_path: Path, settings: Settings, secrets: StaticSecretProvider
) -> None:
    def without_graphs(layout: AgentLayout, port: int) -> list[str]:
        return [*fake_server_command(layout, port), "--no-graphs"]

    supervisor = Supervisor(settings, PortAllocator(23310, 23311), secrets, without_graphs)
    with pytest.raises(AgentStartError, match="missing"):
        supervisor.probe(AgentLayout(tmp_path), parse_manifest(manifest()))


def test_agent_server_requires_the_launch_token(
    manager: AgentManager, make_agent_zip: MakeZip
) -> None:
    agent = manager.install_archive(make_agent_zip())
    handle = manager.start(agent.id)
    search = f"{handle.url}/assistants/search"
    assert httpx.post(search, json={"graph_id": "foo_agent"}).status_code == 401
    ok = httpx.post(search, json={"graph_id": "foo_agent"}, headers=handle.auth_headers)
    assert ok.status_code == 200
    child_env = json.loads((agent.install_dir / "env.json").read_text())
    assert child_env["CORS_ALLOW_ORIGINS"] == ",".join(manager.settings.cors_origins)
    assert "*" not in child_env["CORS_ALLOW_ORIGINS"]


def test_idle_agents_are_stopped(manager: AgentManager, make_agent_zip: MakeZip) -> None:
    agent = manager.install_archive(make_agent_zip())
    manager.start(agent.id)
    assert manager.supervisor.stop_idle(max_idle_s=60) == []
    assert manager.supervisor.touch(agent.id)
    assert manager.supervisor.stop_idle(max_idle_s=0) == [agent.id]
    assert manager.supervisor.get(agent.id) is None
    assert not manager.supervisor.touch(agent.id)


def test_unexpected_exit_is_detected(manager: AgentManager, make_agent_zip: MakeZip) -> None:
    agent = manager.install_archive(make_agent_zip())
    handle = manager.start(agent.id)
    handle.process.kill()
    handle.process.wait()
    assert manager.supervisor.get(agent.id) is None


def test_uninstall_stops_and_removes(manager: AgentManager, make_agent_zip: MakeZip) -> None:
    agent = manager.install_archive(make_agent_zip())
    handle = manager.start(agent.id)
    manager.uninstall(agent.id)
    assert handle.process.poll() is not None
    assert not (manager.settings.agents_dir / agent.id).exists()
    assert manager.list_agents() == []


def test_next_manager_stops_agents_of_a_killed_one(
    settings: Settings, secrets: StaticSecretProvider, make_agent_zip: MakeZip
) -> None:
    def with_root(layout: AgentLayout, port: int) -> list[str]:
        # Like the real command, the command line names the agent's folder.
        return fake_server_command(layout, port)

    crashed = AgentManager(
        settings, secrets, env_builder=FakeEnvBuilder(), command_factory=with_root
    )
    agent = crashed.install_archive(make_agent_zip())
    process = crashed.start(agent.id).process
    # No shutdown(): the manager "dies" and its agent keeps running.
    successor = AgentManager(settings, secrets, env_builder=FakeEnvBuilder())
    try:
        assert process.wait(timeout=10) is not None
    finally:
        successor.shutdown()


def test_reaper_ignores_unrelated_processes(tmp_path: Path) -> None:
    import subprocess

    from safe_agents_manager.orphans import PidRegistry

    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        registry = PidRegistry(tmp_path / "agents.json")
        registry.add("foo_agent", unrelated.pid, tmp_path / "agents" / "foo_agent")
        assert registry.reap() == []
        assert unrelated.poll() is None
        assert registry.reap() == [], "the registry is cleared after reaping"
    finally:
        unrelated.kill()
        unrelated.wait()


def test_agent_stops_by_itself_when_the_manager_goes_away(
    manager: AgentManager, make_agent_zip: MakeZip
) -> None:
    agent = manager.install_archive(make_agent_zip())
    process = manager.start(agent.id).process
    assert process.stdin is not None
    process.stdin.close()  # what the OS does when the manager process dies
    assert process.wait(timeout=10) is not None


def test_unresponsive_agent_is_still_stopped(
    settings: Settings, secrets: StaticSecretProvider, make_agent_zip: MakeZip
) -> None:
    def stubborn(layout: AgentLayout, port: int) -> list[str]:
        return [*fake_server_command(layout, port), "--ignore-stdin"]

    quick = Settings(**{**settings.__dict__, "stop_timeout_s": 1.0})
    mgr = AgentManager(quick, secrets, env_builder=FakeEnvBuilder(), command_factory=stubborn)
    try:
        agent = mgr.install_archive(make_agent_zip())
        process = mgr.start(agent.id).process
        assert mgr.stop(agent.id)
        assert process.poll() is not None
    finally:
        mgr.shutdown()
