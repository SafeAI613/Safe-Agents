from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from conftest import manifest

from safe_agents_manager.errors import NoFreePortError
from safe_agents_manager.layout import AgentLayout, build_langgraph_config, write_langgraph_config
from safe_agents_manager.manifest import parse_manifest
from safe_agents_manager.ports import PortAllocator
from safe_agents_manager.store import AgentStore


def test_port_allocator_skips_reserved_and_busy_ports() -> None:
    allocator = PortAllocator(23200, 23202)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 23200))
        busy.listen()
        first = allocator.allocate()
        second = allocator.allocate()
        assert {first, second} == {23201, 23202}
        with pytest.raises(NoFreePortError):
            allocator.allocate()
        allocator.release(first)
        assert allocator.allocate() == first


def test_langgraph_config_points_into_src(tmp_path: Path) -> None:
    parsed = parse_manifest(manifest())
    assert build_langgraph_config(parsed) == {
        "dependencies": ["./src"],
        "graphs": {"foo_agent": "./src/agent/graph.py:graph"},
        "env": {},
        "auth": {"path": "./platform_auth.py:auth"},
    }
    path = write_langgraph_config(AgentLayout(tmp_path), parsed)
    assert json.loads(path.read_text())["graphs"]["foo_agent"]
    auth_source = AgentLayout(tmp_path).platform_auth.read_text()
    assert "SAFE_AGENTS_AGENT_TOKEN" in auth_source and "compare_digest" in auth_source
    compile(auth_source, "platform_auth.py", "exec")


def test_store_roundtrip_and_upsert(tmp_path: Path) -> None:
    store = AgentStore(tmp_path / "db.sqlite")
    assert store.get("foo_agent") is None
    store.save(parse_manifest(manifest("1.0.0")), tmp_path / "v1")
    store.save(parse_manifest(manifest("1.1.0")), tmp_path / "v2")
    agent = store.get("foo_agent")
    assert agent is not None
    assert (agent.version, agent.install_dir) == ("1.1.0", tmp_path / "v2")
    assert [a.id for a in store.list()] == ["foo_agent"]
    store.delete("foo_agent")
    assert store.list() == []
