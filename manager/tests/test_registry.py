from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import REGISTRY_URL, REPO_ROOT, FakeGitHub, registry_doc, registry_entry

from safe_agents_manager.config import DEFAULT_ALLOWED_HOSTS
from safe_agents_manager.errors import AgentNotFoundError, RegistryError, RegistryUnavailableError
from safe_agents_manager.registry import RegistryClient, parse_registry

SHA = "a" * 64
PACKAGED = Path(__file__).parents[1] / "src/safe_agents_manager/schemas/registry.schema.json"


def make_client(github: FakeGitHub, tmp_path: Path, url: str = REGISTRY_URL) -> RegistryClient:
    return RegistryClient(url, tmp_path / "cache", github.client(), DEFAULT_ALLOWED_HOSTS, 300)


def two_versions(latest: str) -> dict[str, Any]:
    entry = registry_entry(SHA, version="1.0.0")
    entry["versions"].append({**entry["versions"][0], "version": "1.2.0"})
    entry["latest"] = latest
    return registry_doc([entry])


def test_packaged_schema_matches_repository_schema() -> None:
    canonical = json.loads((REPO_ROOT / "schemas" / "registry.schema.json").read_text("utf-8"))
    assert json.loads(PACKAGED.read_text("utf-8")) == canonical


def test_parse_valid_registry() -> None:
    entries = parse_registry(two_versions("1.2.0"))
    assert entries[0].get_version().version == "1.2.0"
    assert entries[0].get_version("1.0.0").version == "1.0.0"
    with pytest.raises(AgentNotFoundError):
        entries[0].get_version("9.9.9")


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        (registry_doc([registry_entry(SHA), registry_entry(SHA)]), "more than once"),
        (two_versions("1.0.0"), "highest version"),
        (two_versions("2.0.0"), "not in versions"),
        (registry_doc([{**registry_entry(SHA), "surprise": 1}]), "invalid"),
        (registry_doc([registry_entry("not-a-sha")]), "invalid"),
        (registry_doc([registry_entry(SHA, source_url="http://github.com/x.zip")]), "invalid"),
    ],
)
def test_parse_rejects(doc: dict[str, Any], message: str) -> None:
    with pytest.raises(RegistryError, match=message):
        parse_registry(doc)


def test_same_version_twice() -> None:
    entry = registry_entry(SHA)
    entry["versions"].append(dict(entry["versions"][0]))
    with pytest.raises(RegistryError, match="same version twice"):
        parse_registry(registry_doc([entry]))


def test_fetch_caches_and_uses_etag(github: FakeGitHub, tmp_path: Path) -> None:
    client = make_client(github, tmp_path)
    first = client.fetch()
    assert [e.id for e in first.entries] == ["foo_agent"] and not first.stale
    client.fetch()
    assert github.requests.count(REGISTRY_URL) == 1, "second fetch within the TTL is served locally"
    again = client.fetch(refresh=True)
    assert github.requests.count(REGISTRY_URL) == 2
    assert [e.id for e in again.entries] == ["foo_agent"], "304 reuses the cached copy"


def test_offline_falls_back_to_stale_cache(github: FakeGitHub, tmp_path: Path) -> None:
    client = make_client(github, tmp_path)
    client.fetch()
    github.fail_all = True
    catalog = client.fetch(refresh=True)
    assert catalog.stale and catalog.entries


def test_offline_without_cache(github: FakeGitHub, tmp_path: Path) -> None:
    github.fail_all = True
    with pytest.raises(RegistryUnavailableError):
        make_client(github, tmp_path).fetch()


def test_invalid_remote_registry_keeps_last_good_copy(github: FakeGitHub, tmp_path: Path) -> None:
    client = make_client(github, tmp_path)
    client.fetch()
    github.set_registry({"registry_version": 1, "agents": "broken"}, etag='"v2"')
    with pytest.raises(RegistryError):
        client.fetch(refresh=True)
    github.fail_all = True
    assert client.fetch(refresh=True).entries[0].id == "foo_agent"


def test_local_registry_file(github: FakeGitHub, tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry_doc([registry_entry(SHA)])))
    catalog = make_client(github, tmp_path, url=str(path)).fetch()
    assert catalog.get("foo_agent").name
    assert github.requests == []
