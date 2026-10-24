from __future__ import annotations

import copy
import json
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from safe_agents_manager.config import Settings
from safe_agents_manager.layout import AgentLayout
from safe_agents_manager.secret_store import StaticSecretProvider
from safe_agents_manager.service import AgentManager

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_AGENT = REPO_ROOT / "examples" / "foo-agent"
FAKE_SERVER = Path(__file__).parent / "fake_server.py"

BASE_MANIFEST: dict[str, Any] = json.loads((EXAMPLE_AGENT / "manifest.json").read_text("utf-8"))


def manifest(version: str = "1.0.0", **runtime: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE_MANIFEST)
    data["version"] = version
    data["runtime"].update(runtime)
    return data


def write_zip(path: Path, files: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def agent_files(
    data: dict[str, Any] | None = None, root: str = "foo-agent-1.0.0/"
) -> dict[str, str]:
    return {
        f"{root}manifest.json": json.dumps(data or manifest()),
        f"{root}requirements.txt": "langgraph\n",
        f"{root}agent/__init__.py": "",
        f"{root}agent/graph.py": "graph = object()\n",
    }


@pytest.fixture
def make_agent_zip(tmp_path: Path) -> Callable[..., Path]:
    counter = iter(range(1000))

    def _make(data: dict[str, Any] | None = None, extra: dict[str, str] | None = None) -> Path:
        files: dict[str, str | bytes] = {**agent_files(data), **(extra or {})}
        return write_zip(tmp_path / f"agent-{next(counter)}.zip", files)

    return _make


class FakeEnvBuilder:
    """Pretends to create a venv: just creates the langgraph executable path."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[tuple[Path, str]] = []
        self.fail_with = fail_with

    def build(self, layout: AgentLayout, python_spec: str, log: Callable[[str], None]) -> None:
        self.calls.append((layout.root, python_spec))
        log("fake uv output")
        if self.fail_with:
            raise self.fail_with
        exe = layout.venv_executable("langgraph")
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("", encoding="utf-8")


def fake_server_command(layout: AgentLayout, port: int) -> list[str]:
    # Like the real server: config passed by path, state written to the working folder.
    return [sys.executable, str(FAKE_SERVER), str(port), "--config", str(layout.langgraph_config)]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / "home",
        uv_path=Path("uv"),
        port_range=(23100, 23199),
        startup_timeout_s=15,
        stop_timeout_s=5,
    )


@pytest.fixture
def secrets() -> StaticSecretProvider:
    return StaticSecretProvider({"OPENROUTER_API_KEY": "sk-test", "UNRELATED_SECRET": "nope"})


@pytest.fixture
def manager(settings: Settings, secrets: StaticSecretProvider) -> Any:
    mgr = AgentManager(
        settings,
        secrets,
        env_builder=FakeEnvBuilder(),
        command_factory=fake_server_command,
    )
    yield mgr
    mgr.shutdown()


# -- fake GitHub for catalog / download tests ---------------------------------------------

SOURCE_URL = BASE_MANIFEST["runtime"]["source_url"]
CODELOAD_URL = (
    "https://release-assets.githubusercontent.com/github-production-release-asset/1/foo-agent.zip"
)
MANIFEST_URL = "https://raw.githubusercontent.com/foo/foo-agent/v1.0.0/manifest.json"
REGISTRY_URL = "https://raw.githubusercontent.com/foo/registry/main/registry.json"


def zip_bytes(files: dict[str, str]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def registry_doc(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"registry_version": 1, "agents": entries}


def registry_entry(
    sha256: str,
    *,
    version: str = "1.0.0",
    agent_id: str = "foo_agent",
    source_url: str = SOURCE_URL,
    manifest_url: str = MANIFEST_URL,
) -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": BASE_MANIFEST["name"],
        "description": BASE_MANIFEST["description"],
        "latest": version,
        "versions": [
            {
                "version": version,
                "manifest_url": manifest_url,
                "source_url": source_url,
                "sha256": sha256,
            }
        ],
    }


class FakeGitHub:
    """Routes ``url -> (status, body, headers)``; records every requested URL."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes, dict[str, str]]] = {}
        self.requests: list[str] = []
        self.fail_all = False
        self.archive = zip_bytes(agent_files())
        self.sha256 = __import__("hashlib").sha256(self.archive).hexdigest()
        # github.com/.../releases/download/... redirects to the asset host, like the real thing.
        self.add(SOURCE_URL, 302, b"", {"location": CODELOAD_URL})
        self.add(CODELOAD_URL, 200, self.archive)
        self.add(MANIFEST_URL, 200, json.dumps(manifest()).encode())
        self.set_registry(registry_doc([registry_entry(self.sha256)]))

    def add(
        self, url: str, status: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.routes[url] = (status, body, headers or {})

    def set_registry(self, doc: dict[str, Any], etag: str = '"v1"') -> None:
        self.add(REGISTRY_URL, 200, json.dumps(doc).encode(), {"etag": etag})

    def handler(self, request: Any) -> Any:
        import httpx

        url = str(request.url)
        self.requests.append(url)
        if self.fail_all:
            raise httpx.ConnectError("network down", request=request)
        if url not in self.routes:
            return httpx.Response(404)
        status, body, headers = self.routes[url]
        if url == REGISTRY_URL and request.headers.get("if-none-match") == headers.get("etag"):
            return httpx.Response(304, headers=headers)
        return httpx.Response(status, content=body, headers=headers)

    def client(self) -> Any:
        import httpx

        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def registry_manager(settings: Settings, secrets: StaticSecretProvider, github: FakeGitHub) -> Any:
    import dataclasses

    mgr = AgentManager(
        dataclasses.replace(settings, registry_url=REGISTRY_URL),
        secrets,
        env_builder=FakeEnvBuilder(),
        command_factory=fake_server_command,
        http_client=github.client(),
    )
    yield mgr
    mgr.shutdown()
