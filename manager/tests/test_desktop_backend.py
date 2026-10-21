"""Backend features used by the desktop app (M3–M7)."""

from __future__ import annotations

import dataclasses
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from conftest import FakeEnvBuilder, fake_server_command, manifest
from fastapi.testclient import TestClient

from safe_agents_manager.api import create_app
from safe_agents_manager.config import Settings
from safe_agents_manager.errors import InvalidApiKeyError, InvalidRequestError
from safe_agents_manager.files import safe_filename, write_export
from safe_agents_manager.lifecycle import pid_alive
from safe_agents_manager.openrouter import OpenRouterClient, parse_pricing
from safe_agents_manager.secret_store import MemoryBackend, SecretStore
from safe_agents_manager.service import AgentManager
from safe_agents_manager.usage import UsageItem

MakeZip = Callable[..., Path]
AUTH = {"Authorization": "Bearer t"}
GOOD_KEY = "sk-or-good"
MODELS = {
    "data": [
        {"id": "foo/bar-model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}},
        {"id": "foo/free-model:free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "broken", "pricing": {"prompt": "n/a"}},
    ]
}
DESKTOP_MANIFEST = manifest(
    accepts_files=[".txt", ".pdf"],
    mcp_servers=[{"name": "foo_mcp", "auth": [{"key": "FOO_TOKEN", "label": "Foo token"}]}],
)


class FakeOpenRouter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        if request.url.path.endswith("/key"):
            if request.headers.get("authorization") != f"Bearer {GOOD_KEY}":
                return httpx.Response(401, json={"error": "bad key"})
            return httpx.Response(200, json={"data": {"label": "foo", "usage": 1.5, "limit": None}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        return httpx.Response(404)


@pytest.fixture
def openrouter() -> FakeOpenRouter:
    return FakeOpenRouter()


@pytest.fixture
def store() -> SecretStore:
    return SecretStore(MemoryBackend())


@pytest.fixture
def desktop(
    settings: Settings, tmp_path: Path, store: SecretStore, openrouter: FakeOpenRouter
) -> Iterator[AgentManager]:
    http = httpx.Client(transport=httpx.MockTransport(openrouter.handler))
    mgr = AgentManager(
        dataclasses.replace(settings, export_dir=tmp_path / "exports"),
        store,
        env_builder=FakeEnvBuilder(),
        command_factory=fake_server_command,
        openrouter=OpenRouterClient(http, tmp_path / "or-cache"),
    )
    yield mgr
    mgr.shutdown()


@pytest.fixture
def client(desktop: AgentManager, make_agent_zip: MakeZip) -> Iterator[TestClient]:
    desktop.install_archive(make_agent_zip(DESKTOP_MANIFEST))
    with TestClient(create_app(desktop, "t")) as test_client:
        yield test_client


# -- secrets ------------------------------------------------------------------------------


def test_secret_scopes(store: SecretStore) -> None:
    store.set("OPENROUTER_API_KEY", "global-key", agent_id="foo_agent")  # global regardless
    store.set("FOO_TOKEN", "for-foo", agent_id="foo_agent")
    store.set("SHARED", "shared-value")
    assert store.get("OPENROUTER_API_KEY", "bar_agent") == "global-key"
    assert store.get("FOO_TOKEN", "foo_agent") == "for-foo"
    assert store.get("FOO_TOKEN", "bar_agent") is None
    assert store.get("SHARED", "bar_agent") == "shared-value"
    store.delete_agent("foo_agent", ["FOO_TOKEN", "OPENROUTER_API_KEY"])
    assert store.get("FOO_TOKEN", "foo_agent") is None
    assert store.get("OPENROUTER_API_KEY") == "global-key", "global keys survive uninstall"


def test_openrouter_key_is_validated_before_storing(client: TestClient, store: SecretStore) -> None:
    bad = client.put("/settings/openrouter-key", json={"value": "sk-wrong"}, headers=AUTH)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_api_key"
    assert store.get("OPENROUTER_API_KEY") is None

    good = client.put("/settings/openrouter-key", json={"value": f" {GOOD_KEY} "}, headers=AUTH)
    assert good.status_code == 200 and good.json()["usage_usd"] == 1.5
    assert store.get("OPENROUTER_API_KEY") == GOOD_KEY
    assert client.get("/settings", headers=AUTH).json()["openrouter_key_set"] is True
    assert client.get("/settings/openrouter-key/status", headers=AUTH).json()["label"] == "foo"

    assert client.delete("/settings/openrouter-key", headers=AUTH).status_code == 204
    assert client.get("/settings", headers=AUTH).json()["openrouter_key_set"] is False


def test_openrouter_unreachable(client: TestClient, openrouter: FakeOpenRouter) -> None:
    openrouter.down = True
    response = client.put("/settings/openrouter-key", json={"value": GOOD_KEY}, headers=AUTH)
    assert response.status_code == 502


def test_agent_secrets_listing_and_update(client: TestClient, desktop: AgentManager) -> None:
    fields = {f["key"]: f for f in client.get("/agents/foo_agent/secrets", headers=AUTH).json()}
    assert fields["OPENROUTER_API_KEY"]["scope"] == "global"
    assert fields["FOO_TOKEN"] == {
        "key": "FOO_TOKEN",
        "label": "Foo token",
        "required": False,
        "source": "mcp:foo_mcp",
        "scope": "agent",
        "set": False,
    }
    ok = client.put("/agents/foo_agent/secrets", json={"values": {"FOO_TOKEN": "x"}}, headers=AUTH)
    assert ok.status_code == 204
    assert desktop.secrets.get("FOO_TOKEN", "foo_agent") == "x"

    for values in ({"OTHER": "x"}, {"OPENROUTER_API_KEY": "x"}):
        bad = client.put("/agents/foo_agent/secrets", json={"values": values}, headers=AUTH)
        assert bad.status_code == 422


def test_agent_receives_its_scoped_secrets_and_tracing(
    client: TestClient, desktop: AgentManager, store: SecretStore
) -> None:
    store.set("OPENROUTER_API_KEY", GOOD_KEY)
    store.set("FOO_TOKEN", "foo-secret", agent_id="foo_agent")
    store.set("LANGSMITH_API_KEY", "ls-key")
    info = client.post("/agents/foo_agent/start", headers=AUTH).json()
    assert info["token"]
    env = json.loads((desktop.get_agent("foo_agent").install_dir / "env.json").read_text())
    assert env["FOO_TOKEN"] == "foo-secret"
    assert env["LANGSMITH_TRACING"] == "true"
    assert env["LANGSMITH_PROJECT"] == "safe-agents-foo_agent"
    assert env["SAFE_AGENTS_AGENT_TOKEN"] == info["token"]


def test_missing_secrets_are_reported(client: TestClient) -> None:
    agent = client.get("/agents/foo_agent", headers=AUTH).json()
    assert agent["missing_secrets"] == ["OPENROUTER_API_KEY"]
    assert agent["models"][0] == agent["default_model"]


# -- files ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\\\Users\\\\foo\\\\report.txt", "report.txt"),
        ("..", "file"),
        (".env", "env"),
        ("שלום עולם.txt", "שלום עולם.txt"),
        ("a;rm -rf $(x).txt", "a_rm -rf _x_.txt"),
    ],
)
def test_safe_filename(raw: str, expected: str) -> None:
    assert safe_filename(raw) == expected


def test_upload_to_inbox(client: TestClient, desktop: AgentManager) -> None:
    files = {"file": ("../notes.txt", io.BytesIO(b"lorem ipsum"), "text/plain")}
    response = client.post("/agents/foo_agent/files", files=files, headers=AUTH)
    assert response.status_code == 201
    saved = Path(response.json()["path"])
    inbox = desktop.get_agent("foo_agent").layout.inbox.resolve()
    assert saved.parent == inbox and saved.read_bytes() == b"lorem ipsum"
    assert saved.name.endswith("-notes.txt")
    upper = {"file": ("X.PDF", io.BytesIO(b"%PDF"), "application/pdf")}
    assert client.post("/agents/foo_agent/files", files=upper, headers=AUTH).status_code == 201


def test_upload_rejections(client: TestClient, desktop: AgentManager) -> None:
    exe = {"file": ("run.exe", io.BytesIO(b"MZ"), "application/octet-stream")}
    response = client.post("/agents/foo_agent/files", files=exe, headers=AUTH)
    assert response.status_code == 422 and "not accepted" in response.json()["error"]["message"]
    small = dataclasses.replace(desktop.settings, max_upload_bytes=4)
    desktop.settings = small
    big = {"file": ("big.txt", io.BytesIO(b"0123456789"), "text/plain")}
    assert client.post("/agents/foo_agent/files", files=big, headers=AUTH).status_code == 422
    assert list(desktop.get_agent("foo_agent").layout.inbox.iterdir()) == []


def test_agent_without_files_refuses_uploads(
    desktop: AgentManager, make_agent_zip: MakeZip
) -> None:
    desktop.install_archive(make_agent_zip(manifest(graph_id="bar_agent", accepts_files=[])))
    with pytest.raises(InvalidRequestError, match="does not accept files"):
        desktop.upload("bar_agent", "a.txt", io.BytesIO(b"x"))


def test_export_never_overwrites(tmp_path: Path) -> None:
    first = write_export(tmp_path, "../conversation", "a")
    second = write_export(tmp_path, "conversation.md", "b")
    assert first.name == "conversation.md" and second.name == "conversation (1).md"
    assert first.parent == tmp_path and second.read_text() == "b"


def test_export_and_readme_api(client: TestClient, desktop: AgentManager) -> None:
    response = client.post(
        "/exports", json={"filename": "chat.md", "content": "# hi"}, headers=AUTH
    )
    assert Path(response.json()["path"]).read_text() == "# hi"
    assert client.get("/agents/foo_agent/readme", headers=AUTH).json()["readme"] is None
    (desktop.get_agent("foo_agent").layout.src / "README.md").write_text("# Foo")
    assert client.get("/agents/foo_agent/readme", headers=AUTH).json()["readme"] == "# Foo"


# -- usage and pricing -------------------------------------------------------------------


def test_parse_pricing_skips_malformed_entries() -> None:
    prices = parse_pricing(MODELS)
    assert prices["foo/bar-model"] == {"prompt": 1e-6, "completion": 2e-6}
    assert "broken" not in prices


def test_usage_is_priced_and_deduplicated(client: TestClient, openrouter: FakeOpenRouter) -> None:
    body = {
        "agent_id": "foo_agent",
        "thread_id": "t1",
        "items": [
            {
                "message_id": "m1",
                "model": "foo/bar-model",
                "input_tokens": 1000,
                "output_tokens": 500,
            },
            {
                "message_id": "m2",
                "model": "foo/free-model:free",
                "input_tokens": 10,
                "output_tokens": 10,
            },
            {"message_id": "m3", "model": "unknown/model", "input_tokens": 1, "output_tokens": 1},
        ],
    }
    first = client.post("/usage", json=body, headers=AUTH).json()
    assert first["recorded"] == 3
    assert first["cost_usd"] == pytest.approx(1000 * 1e-6 + 500 * 2e-6)
    assert first["unpriced_models"] == ["unknown/model"]
    again = client.post("/usage", json=body, headers=AUTH).json()
    assert again["recorded"] == 0 and again["input_tokens"] == first["input_tokens"]
    assert openrouter.calls.count("/api/v1/models") == 1, "prices are cached"
    total = client.get("/usage", params={"agent_id": "foo_agent"}, headers=AUTH).json()
    assert total["output_tokens"] == 511


def test_usage_without_prices_still_records(
    desktop: AgentManager, client: TestClient, openrouter: FakeOpenRouter
) -> None:
    openrouter.down = True
    item = UsageItem("m9", "foo/bar-model", 1, 1)
    assert desktop.record_usage("foo_agent", "t", [item]) == 1
    assert desktop.usage.summary("foo_agent")["unpriced_models"] == ["foo/bar-model"]


def test_key_info_errors(openrouter: FakeOpenRouter, tmp_path: Path) -> None:
    client = OpenRouterClient(
        httpx.Client(transport=httpx.MockTransport(openrouter.handler)), tmp_path
    )
    with pytest.raises(InvalidApiKeyError):
        client.key_info("nope")


# -- api plumbing ------------------------------------------------------------------------


def test_cors_allows_only_the_webview(client: TestClient) -> None:
    def preflight(origin: str) -> httpx.Response:
        return client.options(
            "/agents",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert preflight("tauri://localhost").headers["access-control-allow-origin"] == (
        "tauri://localhost"
    )
    assert "access-control-allow-origin" not in preflight("https://evil.example").headers


def test_heartbeat(client: TestClient, store: SecretStore) -> None:
    assert client.post("/agents/foo_agent/heartbeat", headers=AUTH).json()["running"] is False
    store.set("OPENROUTER_API_KEY", GOOD_KEY)
    assert client.post("/agents/foo_agent/start", headers=AUTH).status_code == 200
    assert client.post("/agents/foo_agent/heartbeat", headers=AUTH).json()["running"] is True


# -- process lifecycle -------------------------------------------------------------------


def test_pid_alive() -> None:
    assert pid_alive(os.getpid())
    assert not pid_alive(2**22 + 12345)


def test_serve_exits_when_stdin_closes(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "SAFE_AGENTS_SECRETS": "memory",
        "SAFE_AGENTS_TOKEN": "tok",
        "SAFE_AGENTS_HOME": str(tmp_path / "home"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "safe_agents_manager", "serve", "--exit-on-stdin-close"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
    )
    try:
        assert proc.stdout is not None and proc.stdin is not None
        ready = json.loads(proc.stdout.readline())
        assert ready["event"] == "ready" and "token" not in ready
        url = f"http://127.0.0.1:{ready['port']}"
        assert httpx.get(f"{url}/agents", headers={"Authorization": "Bearer tok"}).json() == []
        proc.stdin.close()
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
