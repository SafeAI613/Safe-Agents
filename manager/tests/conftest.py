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
    return [sys.executable, str(FAKE_SERVER), str(port)]


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
