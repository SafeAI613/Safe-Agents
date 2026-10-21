from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    CODELOAD_URL,
    MANIFEST_URL,
    FakeGitHub,
    registry_doc,
    registry_entry,
)

from safe_agents_manager.cli import main
from safe_agents_manager.config import Settings
from safe_agents_manager.errors import InstallError
from safe_agents_manager.registry import RegistryEntry, RegistryVersion
from safe_agents_manager.registry_validate import RegistryValidator


def write(path: Path, doc: dict[str, Any]) -> Path:
    path.write_text(json.dumps(doc))
    return path


def validator(settings: Settings, github: FakeGitHub, install_check: Any = None) -> Any:
    return RegistryValidator(settings, github.client(), install_check)


def test_valid_registry_passes_deep_check(
    tmp_path: Path, settings: Settings, github: FakeGitHub
) -> None:
    calls: list[str] = []

    def install_check(archive: Path, entry: RegistryEntry, item: RegistryVersion) -> None:
        assert archive.is_file()
        calls.append(f"{entry.id}@{item.version}")

    reg = write(tmp_path / "r.json", registry_doc([registry_entry(github.sha256)]))
    assert validator(settings, github, install_check).validate(reg, deep=True) == []
    assert calls == ["foo_agent@1.0.0"]


def test_published_versions_are_immutable(
    tmp_path: Path, settings: Settings, github: FakeGitHub
) -> None:
    base = write(tmp_path / "base.json", registry_doc([registry_entry(github.sha256)]))
    changed = write(tmp_path / "new.json", registry_doc([registry_entry("b" * 64)]))
    findings = validator(settings, github).validate(changed, base=base)
    assert len(findings) == 1 and "never change" in findings[0].message


def test_unchanged_versions_are_not_downloaded_again(
    tmp_path: Path, settings: Settings, github: FakeGitHub
) -> None:
    doc = registry_doc([registry_entry(github.sha256)])
    base, reg = write(tmp_path / "base.json", doc), write(tmp_path / "r.json", doc)
    assert validator(settings, github).validate(reg, base=base, deep=True) == []
    assert github.requests == []


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (lambda e, g: e.update(description="Something else"), "description differs"),
        (lambda e, g: e.update(name="Other Name"), "name differs"),
        (lambda e, g: e["versions"][0].update(sha256="c" * 64), "sha256 mismatch"),
        (lambda e, g: g.add(MANIFEST_URL, 200, b'{"name": "decoy"}'), "manifest_url content"),
        (lambda e, g: g.add(MANIFEST_URL, 404, b""), "HTTP 404"),
        (lambda e, g: g.add(CODELOAD_URL, 200, b"not a zip"), "sha256 mismatch"),
    ],
)
def test_deep_check_findings(
    tmp_path: Path, settings: Settings, github: FakeGitHub, tamper: Any, message: str
) -> None:
    entry = registry_entry(github.sha256)
    tamper(entry, github)
    reg = write(tmp_path / "r.json", registry_doc([entry]))
    findings = validator(settings, github).validate(reg, deep=True)
    assert findings and any(message in f.message for f in findings), findings


def test_install_check_failure_is_reported(
    tmp_path: Path, settings: Settings, github: FakeGitHub
) -> None:
    def failing(*_: Any) -> None:
        raise InstallError("graph failed to load")

    reg = write(tmp_path / "r.json", registry_doc([registry_entry(github.sha256)]))
    findings = validator(settings, github, failing).validate(reg, deep=True)
    assert "install check failed: graph failed to load" in findings[0].message


def test_cli_reports_github_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    bad = write(tmp_path / "r.json", {"registry_version": 1, "agents": [{"id": "x"}]})
    assert main(["--home", str(tmp_path / "h"), "validate-registry", str(bad)]) == 1
    assert capsys.readouterr().out.startswith("::error title=registry::")

    good = write(tmp_path / "g.json", registry_doc([]))
    assert main(["--home", str(tmp_path / "h"), "validate-registry", str(good)]) == 0
