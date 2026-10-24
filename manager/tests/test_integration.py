"""End to end: real uv, real ``langgraph dev``, the example agent. Opt-in (needs network).

Run with: ``uv run pytest -m integration``
"""

from __future__ import annotations

import json
import shutil
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from conftest import EXAMPLE_AGENT

from safe_agents_manager.config import Settings, find_uv
from safe_agents_manager.secret_store import StaticSecretProvider
from safe_agents_manager.service import AgentManager

pytestmark = pytest.mark.integration


def zip_example(dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w") as zf:
        for path in EXAMPLE_AGENT.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, Path("foo-agent-1.0.0") / path.relative_to(EXAMPLE_AGENT))
    return dest


@pytest.fixture
def real_manager(tmp_path: Path) -> Iterator[AgentManager]:
    if shutil.which(str(find_uv())) is None:
        pytest.skip("uv is not installed")
    settings = Settings(home=tmp_path / "home", uv_path=find_uv(), startup_timeout_s=180)
    secrets = StaticSecretProvider({"OPENROUTER_API_KEY": "sk-unused", "FOO_AGENT_FAKE_LLM": "1"})
    manager = AgentManager(settings, secrets)
    yield manager
    manager.shutdown()


def test_install_run_interrupt_and_resume(real_manager: AgentManager, tmp_path: Path) -> None:
    agent = real_manager.install_archive(zip_example(tmp_path / "foo-agent.zip"))
    assert agent.layout.venv_executable("langgraph").is_file()

    handle = real_manager.start(agent.id)
    with httpx.Client(base_url=handle.url, timeout=30) as anonymous:
        assert anonymous.post("/threads", json={}).status_code == 401
    with httpx.Client(base_url=handle.url, headers=handle.auth_headers, timeout=30) as client:
        thread_id = client.post("/threads", json={}).raise_for_status().json()["thread_id"]
        client.post(
            f"/threads/{thread_id}/runs/wait",
            json={
                "assistant_id": agent.manifest.graph_id,
                "input": {"messages": [{"role": "user", "content": "foo"}]},
            },
        ).raise_for_status()

        state = client.get(f"/threads/{thread_id}/state").raise_for_status().json()
        interrupts = [i for task in state["tasks"] for i in task.get("interrupts", [])]
        assert interrupts and interrupts[0]["value"]["type"] == "approval"
        assert state["values"]["messages"][-1]["content"] == "Lorem ipsum dolor sit amet."

        client.post(
            f"/threads/{thread_id}/runs/wait",
            json={
                "assistant_id": agent.manifest.graph_id,
                "command": {"resume": {"approved": True}},
            },
        ).raise_for_status()
        state = client.get(f"/threads/{thread_id}/state").raise_for_status().json()
        assert state["values"]["messages"][-1]["content"] == "Approved."

    started = time.monotonic()
    assert real_manager.stop(agent.id)
    assert time.monotonic() - started < 8, "graceful stop through the launcher, not a timeout"
    log_text = (real_manager.settings.logs_dir / f"{agent.id}.log").read_text()
    assert "Stopping dev persistence flush loop" in log_text, "state was flushed on stop"

    # Conversation history survives a restart of the agent server.
    handle = real_manager.start(agent.id)
    with httpx.Client(base_url=handle.url, headers=handle.auth_headers, timeout=30) as client:
        found = client.post("/threads/search", json={"limit": 10}).raise_for_status().json()
        assert thread_id in [thread["thread_id"] for thread in found]
    assert real_manager.stop(agent.id)

    # ...and an update to a new version.
    data = json.loads((EXAMPLE_AGENT / "manifest.json").read_text())
    data["version"] = "1.0.1"
    updated = tmp_path / "foo-agent-1.0.1.zip"
    with (
        zipfile.ZipFile(zip_example(tmp_path / "base.zip")) as src,
        zipfile.ZipFile(updated, "w") as dst,
    ):
        for item in src.infolist():
            body = src.read(item)
            if item.filename.endswith("/manifest.json"):
                body = json.dumps(data).encode()
            dst.writestr(item, body)
    assert real_manager.install_archive(updated).version == "1.0.1"
    handle = real_manager.start(agent.id)
    with httpx.Client(base_url=handle.url, headers=handle.auth_headers, timeout=30) as client:
        found = client.post("/threads/search", json={"limit": 10}).raise_for_status().json()
        assert thread_id in [thread["thread_id"] for thread in found]
    assert real_manager.stop(agent.id)
    real_manager.uninstall(agent.id)
    assert not (real_manager.settings.agents_dir / agent.id).exists()


def test_registry_validation_with_real_install(tmp_path: Path) -> None:
    import hashlib
    import json

    from conftest import CODELOAD_URL, MANIFEST_URL, FakeGitHub, registry_doc, registry_entry

    from safe_agents_manager.registry_validate import RegistryValidator, real_install_check

    if shutil.which(str(find_uv())) is None:
        pytest.skip("uv is not installed")
    archive = zip_example(tmp_path / "foo-agent.zip").read_bytes()
    github = FakeGitHub()
    github.add(CODELOAD_URL, 200, archive)
    github.add(MANIFEST_URL, 200, (EXAMPLE_AGENT / "manifest.json").read_bytes())
    entry = registry_entry(hashlib.sha256(archive).hexdigest())
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(registry_doc([entry])))

    settings = Settings(home=tmp_path / "home", uv_path=find_uv(), startup_timeout_s=180)
    with github.client() as client:
        validator = RegistryValidator(settings, client, real_install_check(settings))
        assert validator.validate(registry, deep=True) == []


def test_scaffolded_agent_installs_and_loads(tmp_path: Path) -> None:
    from safe_agents_manager.devtools import install_check, new_agent

    if shutil.which(str(find_uv())) is None:
        pytest.skip("uv is not installed")
    directory = new_agent(tmp_path / "scaffold", "scaffold_agent", "Scaffold Agent")
    settings = Settings(home=tmp_path / "home", uv_path=find_uv(), startup_timeout_s=180)
    install_check(directory, settings)  # raises if the graph does not load in langgraph dev
