from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from conftest import FakeEnvBuilder

from safe_agents_manager.cli import main
from safe_agents_manager.config import Settings
from safe_agents_manager.devtools import check_agent, new_agent, package_agent, registry_entry
from safe_agents_manager.installer import Installer
from safe_agents_manager.manifest import load_manifest
from safe_agents_manager.registry import parse_registry
from safe_agents_manager.store import AgentStore


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    return new_agent(tmp_path / "my-agent", "foo_agent", "Foo Agent")


def errors(directory: Path) -> list[str]:
    return [f.message for f in check_agent(directory)[1] if f.level == "error"]


def test_scaffold_follows_the_course_structure(agent_dir: Path) -> None:
    for path in (
        "agent/__init__.py",
        "agent/graph.py",
        "agent/nodes.py",
        "agent/tools.py",
        "agent/cli.py",
        "evals/rubric.md",
        "evals/results/.gitkeep",
        "manifest.json",
        "requirements.txt",
        "README.md",
        ".env.example",
        ".gitignore",
    ):
        assert (agent_dir / path).is_file(), path
    manifest = load_manifest(agent_dir)
    assert manifest.agent_id == "foo_agent" and manifest.name == "Foo Agent"
    assert "__AGENT" not in (agent_dir / "README.md").read_text()
    assert ".env" in (agent_dir / ".gitignore").read_text()


def test_scaffold_rejects_bad_input(tmp_path: Path, agent_dir: Path) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        new_agent(tmp_path / "x", "Bad-Id", "x")
    with pytest.raises(ValueError, match="not empty"):
        new_agent(agent_dir, "foo_agent", "x")


def test_fresh_scaffold_only_warns_about_placeholders(agent_dir: Path) -> None:
    manifest, findings = check_agent(agent_dir)
    assert manifest is not None
    assert [f.level for f in findings] == ["warning"]
    assert "OWNER" in findings[0].message


def test_check_catches_common_mistakes(agent_dir: Path) -> None:
    (agent_dir / "requirements.txt").write_text("requests\n")
    (agent_dir / "agent" / "keys.py").write_text('KEY = "sk-or-v1-0123456789abcdef0123"\n')
    data = json.loads((agent_dir / "manifest.json").read_text())
    data["runtime"]["source_url"] = "https://github.com/foo/bar/archive/refs/tags/v0.1.0.zip"
    (agent_dir / "manifest.json").write_text(json.dumps(data))
    found = " | ".join(errors(agent_dir))
    assert "must include langgraph" in found
    assert "hard-coded API key" in found
    assert "Release asset" in found


def test_check_reports_a_broken_manifest(tmp_path: Path) -> None:
    assert errors(tmp_path) == ["manifest.json was not found in the agent root folder"]


def test_package_is_clean_and_reproducible(agent_dir: Path, tmp_path: Path) -> None:
    (agent_dir / ".env").write_text("OPENROUTER_API_KEY=secret")
    (agent_dir / ".venv" / "lib").mkdir(parents=True)
    (agent_dir / ".venv" / "lib" / "x.py").write_text("")
    (agent_dir / "agent" / "__pycache__").mkdir()
    (agent_dir / "agent" / "__pycache__" / "graph.cpython-312.pyc").write_bytes(b"\0")

    first, digest = package_agent(agent_dir)
    names = zipfile.ZipFile(first).namelist()
    assert all(name.startswith("foo_agent/") for name in names)
    assert "foo_agent/manifest.json" in names and "foo_agent/.env.example" in names
    assert not any(part in name for name in names for part in (".venv", "__pycache__", "dist/"))
    assert "foo_agent/.env" not in names

    again, digest_again = package_agent(agent_dir, tmp_path / "again.zip")
    assert digest_again == digest, "same files must give the same sha256"
    assert again != first


def test_package_installs_and_entry_passes_catalog_validation(
    agent_dir: Path, settings: Settings
) -> None:
    archive, digest = package_agent(agent_dir)
    settings.ensure_dirs()
    installer = Installer(settings, AgentStore(settings.db_path), FakeEnvBuilder(), lambda *_: None)
    agent = installer.install_archive(archive, expected_sha256=digest)
    assert (agent.install_dir / "src" / "agent" / "cli.py").is_file()

    entry = registry_entry(load_manifest(agent_dir), digest)
    parsed = parse_registry({"registry_version": 1, "agents": [entry]})
    assert parsed[0].get_version().sha256 == digest
    assert entry["versions"][0]["manifest_url"].endswith("/v0.1.0/manifest.json")


def test_cli_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(tmp_path / "home")]
    target = tmp_path / "bar"
    assert main([*home, "new-agent", str(target), "--id", "bar_agent", "--name", "Bar"]) == 0
    assert main([*home, "check", str(target)]) == 0
    assert main([*home, "package", str(target)]) == 0
    out = capsys.readouterr().out
    assert "sha256:" in out and '"id": "bar_agent"' in out
    (target / "requirements.txt").write_text("")
    assert main([*home, "check", str(target)]) == 1


def test_nested_build_folder_is_kept_but_top_level_build_is_not(agent_dir: Path) -> None:
    (agent_dir / "agent" / "build").mkdir()
    (agent_dir / "agent" / "build" / "steps.py").write_text("STEPS = []\n")
    (agent_dir / "build").mkdir()
    (agent_dir / "build" / "junk.txt").write_text("x")
    archive, _ = package_agent(agent_dir)
    names = zipfile.ZipFile(archive).namelist()
    assert "foo_agent/agent/build/steps.py" in names
    assert "foo_agent/build/junk.txt" not in names


def test_template_copy_skips_compiled_files(tmp_path: Path) -> None:
    from safe_agents_manager.devtools import _copy_template

    template = tmp_path / "template"
    (template / "agent" / "__pycache__").mkdir(parents=True)
    (template / "agent" / "graph.py").write_text("ID = '__AGENT_ID__'\n")
    (template / "agent" / "__pycache__" / "graph.cpython-312.pyc").write_bytes(b"\x89\xff\x00")
    target = tmp_path / "out"
    target.mkdir()
    _copy_template(template, target, {"__AGENT_ID__": "foo_agent"})
    assert (target / "agent" / "graph.py").read_text() == "ID = 'foo_agent'\n"
    assert not (target / "agent" / "__pycache__").exists()


def test_repository_url_must_be_a_github_repo(agent_dir: Path) -> None:
    data = json.loads((agent_dir / "manifest.json").read_text())
    del data["repository_url"]
    (agent_dir / "manifest.json").write_text(json.dumps(data))
    # repository_url is required by the schema, so the manifest itself is rejected.
    assert errors(agent_dir)
    data["repository_url"] = "https://gitlab.com/foo"
    (agent_dir / "manifest.json").write_text(json.dumps(data))
    assert any("repository_url" in message for message in errors(agent_dir))
