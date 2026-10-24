from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from conftest import (
    CODELOAD_URL,
    MANIFEST_URL,
    SOURCE_URL,
    FakeGitHub,
    agent_files,
    manifest,
    registry_doc,
    registry_entry,
    zip_bytes,
)
from fastapi.testclient import TestClient

from safe_agents_manager.api import create_app
from safe_agents_manager.errors import AgentAlreadyInstalledError, ArchiveError, ManifestError
from safe_agents_manager.installer import InstallEvent, InstallStep
from safe_agents_manager.service import AgentManager

AUTH = {"Authorization": "Bearer t"}


def publish_v110(github: FakeGitHub) -> None:
    """Add version 1.1.0 to the fake catalog."""
    import hashlib

    data = manifest("1.1.0")
    url = "https://release-assets.githubusercontent.com/github-production-release-asset/2/foo-agent.zip"
    archive = zip_bytes(agent_files(data))
    github.add(url, 200, archive)
    entry = registry_entry(github.sha256)
    entry["versions"].append(
        {
            "version": "1.1.0",
            "manifest_url": MANIFEST_URL,
            "source_url": url,
            "sha256": hashlib.sha256(archive).hexdigest(),
        }
    )
    entry["latest"] = "1.1.0"
    github.set_registry(registry_doc([entry]), etag='"v2"')


def test_install_from_registry(registry_manager: AgentManager, github: FakeGitHub) -> None:
    events: list[InstallEvent] = []
    agent = registry_manager.install_from_registry("foo_agent", on_event=events.append)
    assert agent.version == "1.0.0"
    steps = [e.step for e in events]
    assert steps[0] is InstallStep.DOWNLOADING and steps[-1] is InstallStep.INSTALLED
    assert any(e.message == "100%" for e in events)
    assert SOURCE_URL in github.requests and CODELOAD_URL in github.requests
    assert list(registry_manager.settings.downloads_dir.iterdir()) == []


def test_tampered_archive_is_rejected(registry_manager: AgentManager, github: FakeGitHub) -> None:
    github.add(CODELOAD_URL, 200, zip_bytes({**agent_files(), "foo-agent-1.0.0/evil.py": "x"}))
    events: list[InstallEvent] = []
    with pytest.raises(ArchiveError, match="sha256 mismatch"):
        registry_manager.install_from_registry("foo_agent", on_event=events.append)
    assert events[-1].step is InstallStep.FAILED
    assert registry_manager.list_agents() == []
    assert list(registry_manager.settings.downloads_dir.iterdir()) == []


def test_archive_must_contain_the_catalog_agent(
    registry_manager: AgentManager, github: FakeGitHub
) -> None:
    import hashlib

    other = zip_bytes(agent_files(manifest(graph_id="bar_agent")))
    github.add(CODELOAD_URL, 200, other)
    github.set_registry(registry_doc([registry_entry(hashlib.sha256(other).hexdigest())]), '"x"')
    with pytest.raises(ManifestError, match="expected 'foo_agent'"):
        registry_manager.install_from_registry("foo_agent")


def test_already_installed_skips_download(
    registry_manager: AgentManager, github: FakeGitHub
) -> None:
    registry_manager.install_from_registry("foo_agent")
    github.requests.clear()
    with pytest.raises(AgentAlreadyInstalledError):
        registry_manager.install_from_registry("foo_agent", refresh=False)
    assert github.requests == []


def test_catalog_reports_updates(registry_manager: AgentManager, github: FakeGitHub) -> None:
    _, rows = registry_manager.catalog()
    assert rows[0]["installed_version"] is None and not rows[0]["update_available"]
    registry_manager.install_from_registry("foo_agent")
    publish_v110(github)
    _, rows = registry_manager.catalog(refresh=True)
    assert rows[0]["installed_version"] == "1.0.0"
    assert rows[0]["update_available"] is True
    updated = registry_manager.install_from_registry("foo_agent")
    assert updated.version == "1.1.0"


@pytest.fixture
def client(registry_manager: AgentManager) -> Iterator[TestClient]:
    with TestClient(create_app(registry_manager, "t")) as test_client:
        yield test_client


def _final_sse_event(client: TestClient, job_id: str) -> dict[str, Any]:
    final: dict[str, Any] = {}
    with client.stream("GET", f"/jobs/{job_id}/events", headers=AUTH) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                final = json.loads(line.removeprefix("data: "))
    return final


def test_api_registry_listing_and_install(client: TestClient) -> None:
    listing = client.get("/registry", headers=AUTH).json()
    assert listing["stale"] is False
    assert listing["agents"][0]["id"] == "foo_agent"

    job = client.post("/registry/foo_agent/install", headers=AUTH).json()
    final = _final_sse_event(client, job["id"])
    assert final["status"] == "succeeded"
    assert client.get("/registry", headers=AUTH).json()["agents"][0]["installed_version"] == "1.0.0"


def test_api_unknown_catalog_agent(client: TestClient) -> None:
    job = client.post("/registry/nobody_here/install", headers=AUTH, json={}).json()
    final = _final_sse_event(client, job["id"])
    assert final["status"] == "failed"
    assert final["error"]["code"] == "agent_not_found"


def test_api_registry_unavailable(client: TestClient, github: FakeGitHub) -> None:
    github.fail_all = True
    response = client.get("/registry", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "registry_unavailable"


def test_disk_error_during_download(
    registry_manager: AgentManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    import safe_agents_manager.service as service
    from safe_agents_manager.errors import DownloadError

    def broken(*_: object, **__: object) -> str:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(service, "download_file", broken)
    events: list[InstallEvent] = []
    with pytest.raises(DownloadError, match="No space left"):
        registry_manager.install_from_registry("foo_agent", on_event=events.append)
    assert events[-1].step is InstallStep.FAILED


def test_api_registry_manifest_preview(client: TestClient, github: FakeGitHub) -> None:
    preview = client.get("/registry/foo_agent/manifest", headers=AUTH).json()
    assert preview["version"] == "1.0.0"
    assert preview["permissions"] == []
    assert preview["secrets"][0]["key"] == "OPENROUTER_API_KEY"
    github.add(MANIFEST_URL, 200, b"{not json")
    assert client.get("/registry/foo_agent/manifest", headers=AUTH).status_code == 502


def test_update_stops_the_running_version(
    registry_manager: AgentManager, github: FakeGitHub
) -> None:
    registry_manager.install_from_registry("foo_agent")
    process = registry_manager.start("foo_agent").process
    publish_v110(github)
    updated = registry_manager.install_from_registry("foo_agent")
    assert updated.version == "1.1.0"
    assert process.poll() is not None, "old version was stopped"
    assert registry_manager.supervisor.get("foo_agent") is None


def test_failed_update_restarts_the_previous_version(
    registry_manager: AgentManager, github: FakeGitHub
) -> None:
    import hashlib

    registry_manager.install_from_registry("foo_agent")
    registry_manager.start("foo_agent")
    broken = zip_bytes(agent_files(manifest("1.1.0", graph_entry="agent/missing.py:graph")))
    url = "https://release-assets.githubusercontent.com/github-production-release-asset/3/b.zip"
    github.add(url, 200, broken)
    entry = registry_entry(github.sha256)
    entry["versions"].append(
        {
            "version": "1.1.0",
            "manifest_url": MANIFEST_URL,
            "source_url": url,
            "sha256": hashlib.sha256(broken).hexdigest(),
        }
    )
    entry["latest"] = "1.1.0"
    github.set_registry(registry_doc([entry]), etag='"v3"')
    with pytest.raises(ManifestError):
        registry_manager.install_from_registry("foo_agent")
    # Validation failed before the agent was touched: it kept running.
    assert registry_manager.supervisor.get("foo_agent") is not None

    from safe_agents_manager.errors import InstallError

    good_but_failing = zip_bytes(agent_files(manifest("1.2.0")))
    url2 = "https://release-assets.githubusercontent.com/github-production-release-asset/4/c.zip"
    github.add(url2, 200, good_but_failing)
    entry["versions"].append(
        {
            "version": "1.2.0",
            "manifest_url": MANIFEST_URL,
            "source_url": url2,
            "sha256": hashlib.sha256(good_but_failing).hexdigest(),
        }
    )
    entry["latest"] = "1.2.0"
    github.set_registry(registry_doc([entry]), etag='"v4"')

    def failing_smoke(*_: object) -> None:
        raise InstallError("graph failed to load")

    registry_manager.installer._smoke_test = failing_smoke
    with pytest.raises(InstallError):
        registry_manager.install_from_registry("foo_agent")
    restarted = registry_manager.supervisor.get("foo_agent")
    assert restarted is not None, "the old version runs again after the failed update"
    assert registry_manager.get_agent("foo_agent").version == "1.0.0"


def test_agent_cannot_start_while_it_is_being_installed(
    registry_manager: AgentManager, github: FakeGitHub
) -> None:
    import threading

    from safe_agents_manager.errors import AgentUpdatingError

    registry_manager.install_from_registry("foo_agent")
    publish_v110(github)
    inside = threading.Event()
    release = threading.Event()

    def slow_smoke(*_: object) -> None:
        inside.set()
        release.wait(10)

    registry_manager.installer._smoke_test = slow_smoke
    worker = threading.Thread(target=registry_manager.install_from_registry, args=("foo_agent",))
    worker.start()
    assert inside.wait(10)
    with pytest.raises(AgentUpdatingError):
        registry_manager.start("foo_agent")
    release.set()
    worker.join(10)
    assert registry_manager.start("foo_agent").port
