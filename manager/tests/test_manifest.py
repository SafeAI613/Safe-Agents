from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import EXAMPLE_AGENT, REPO_ROOT, manifest

from safe_agents_manager.errors import ManifestError
from safe_agents_manager.manifest import load_manifest, parse_manifest, validate_layout

PACKAGED_SCHEMA = Path(__file__).parents[1] / "src/safe_agents_manager/schemas/manifest.schema.json"


def test_packaged_schema_matches_repository_schema() -> None:
    canonical = json.loads((REPO_ROOT / "schemas" / "manifest.schema.json").read_text("utf-8"))
    assert json.loads(PACKAGED_SCHEMA.read_text("utf-8")) == canonical


def test_example_agent_is_valid() -> None:
    parsed = load_manifest(EXAMPLE_AGENT)
    validate_layout(parsed, EXAMPLE_AGENT)
    assert parsed.agent_id == "foo_agent"


def test_openrouter_key_is_always_required() -> None:
    parsed = parse_manifest(manifest(env_required=["BAR_KEY"], env_optional=["OPENROUTER_API_KEY"]))
    assert parsed.required_env == ["OPENROUTER_API_KEY", "BAR_KEY"]
    assert "OPENROUTER_API_KEY" not in parsed.optional_env


def test_mcp_auth_keys_are_optional_env() -> None:
    parsed = parse_manifest(
        manifest(mcp_servers=[{"name": "foo", "auth": [{"key": "FOO_TOKEN", "label": "Token"}]}])
    )
    assert "FOO_TOKEN" in parsed.optional_env


@pytest.mark.parametrize(
    "runtime",
    [
        {"graph_id": "Bad-Id"},
        {"graph_entry": "agent/graph.py"},
        {"source_url": "http://insecure.example/x.zip"},
        {"default_model": "no-slash"},
        {"unexpected": True},
    ],
)
def test_invalid_runtime_fields(runtime: dict[str, object]) -> None:
    with pytest.raises(ManifestError):
        parse_manifest(manifest(**runtime))


def test_missing_runtime_block() -> None:
    data = manifest()
    del data["runtime"]
    with pytest.raises(ManifestError, match="runtime"):
        parse_manifest(data)


def test_graph_entry_cannot_escape_folder() -> None:
    with pytest.raises(ManifestError, match="relative path"):
        parse_manifest(manifest(graph_entry="../outside.py:graph"))


def test_layout_requires_graph_file_and_requirements(tmp_path: Path) -> None:
    parsed = parse_manifest(manifest())
    with pytest.raises(ManifestError, match="graph file"):
        validate_layout(parsed, tmp_path)
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "graph.py").write_text("")
    with pytest.raises(ManifestError, match="requirements"):
        validate_layout(parsed, tmp_path)
    (tmp_path / "requirements.txt").write_text("langgraph")
    validate_layout(parsed, tmp_path)


def test_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{nope")
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(tmp_path)
