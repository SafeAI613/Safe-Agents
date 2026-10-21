from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from safe_agents_manager.api import create_app
from safe_agents_manager.service import AgentManager

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MakeZip = Callable[..., Path]


@pytest.fixture
def client(manager: AgentManager) -> Iterator[TestClient]:
    with TestClient(create_app(manager, TOKEN)) as test_client:
        yield test_client


def read_sse(client: TestClient, url: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", url, headers=AUTH) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event:
                events.append((event, json.loads(line.removeprefix("data: "))))
    return events


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_endpoints_require_token(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/agents", headers=headers).status_code == 401


def test_install_job_streams_progress(client: TestClient, make_agent_zip: MakeZip) -> None:
    response = client.post(
        "/agents/install", headers=AUTH, json={"archive_path": str(make_agent_zip())}
    )
    assert response.status_code == 202
    job_id = response.json()["id"]

    events = read_sse(client, f"/jobs/{job_id}/events")
    steps = [data["step"] for name, data in events if name == "progress" and not data["detail"]]
    assert steps[0] == "verifying" and steps[-1] == "installed"
    name, final = events[-1]
    assert name == "done" and final["status"] == "succeeded"
    assert final["result"]["id"] == "foo_agent"

    agents = client.get("/agents", headers=AUTH).json()
    assert [a["id"] for a in agents] == ["foo_agent"]
    assert agents[0]["status"] == "stopped"


def test_failed_install_reports_error(client: TestClient, tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    job_id = client.post("/agents/install", headers=AUTH, json={"archive_path": str(bad)}).json()[
        "id"
    ]
    name, final = read_sse(client, f"/jobs/{job_id}/events")[-1]
    assert name == "done"
    assert final["status"] == "failed"
    assert final["error"]["code"] == "invalid_archive"


def test_install_missing_file_is_422(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/agents/install", headers=AUTH, json={"archive_path": str(tmp_path / "missing.zip")}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_archive"


def test_start_stop_logs_uninstall(
    client: TestClient, manager: AgentManager, make_agent_zip: MakeZip
) -> None:
    manager.install_archive(make_agent_zip())
    started = client.post("/agents/foo_agent/start", headers=AUTH).json()
    assert started["graph_id"] == "foo_agent"
    assert started["url"].startswith("http://127.0.0.1:")
    assert client.get("/agents/foo_agent", headers=AUTH).json()["status"] == "running"

    logs = client.get("/agents/foo_agent/logs", params={"tail": 5}, headers=AUTH).json()
    assert any("starting foo_agent" in line for line in logs["lines"])

    assert client.post("/agents/foo_agent/stop", headers=AUTH).json()["stopped"] is True
    assert client.delete("/agents/foo_agent", headers=AUTH).status_code == 204
    assert client.get("/agents/foo_agent", headers=AUTH).status_code == 404


def test_unknown_agent_and_invalid_id(client: TestClient) -> None:
    response = client.post("/agents/nobody_here/start", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_not_found"
    assert client.post("/agents/BAD..ID/start", headers=AUTH).status_code == 422


def test_missing_secret_is_412(settings: Any, make_agent_zip: MakeZip) -> None:
    from conftest import FakeEnvBuilder, fake_server_command

    from safe_agents_manager.secret_store import StaticSecretProvider

    mgr = AgentManager(
        settings,
        StaticSecretProvider({}),
        env_builder=FakeEnvBuilder(),
        command_factory=fake_server_command,
    )
    mgr.install_archive(make_agent_zip())
    with TestClient(create_app(mgr, TOKEN)) as client:
        response = client.post("/agents/foo_agent/start", headers=AUTH)
    assert response.status_code == 412
    assert "OPENROUTER_API_KEY" in response.json()["error"]["message"]
