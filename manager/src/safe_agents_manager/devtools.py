"""Tools for agent developers: scaffold an agent, check it, package it for the catalog."""

from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import Settings
from .errors import ManagerError
from .manifest import (
    MANIFEST_FILENAME,
    REQUIREMENTS_FILENAME,
    AgentManifest,
    load_manifest,
    validate_layout,
)
from .secret_store import StaticSecretProvider

AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
# Never shipped in a package: secrets, environments, caches, build output.
EXCLUDED = (
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "*.pyc",
    ".env",
    ".langgraph_api",
    "node_modules",
    "dist",
    "build",
    "*.spec",
    ".DS_Store",
    ".idea",
    ".vscode",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)
# Fixed timestamp so packaging the same files twice gives the same sha256.
ZIP_DATE = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.message}"


# -- new-agent -----------------------------------------------------------------------------


def _copy_template(node: Traversable, target: Path, replacements: dict[str, str]) -> None:
    for item in node.iterdir():
        name = ".gitignore" if item.name == "gitignore.txt" else item.name
        if item.is_dir():
            (target / name).mkdir(exist_ok=True)
            _copy_template(item, target / name, replacements)
            continue
        text = item.read_text(encoding="utf-8")
        for token, value in replacements.items():
            text = text.replace(token, value)
        (target / name).write_text(text, encoding="utf-8")


def new_agent(directory: Path, agent_id: str, name: str) -> Path:
    if not AGENT_ID.match(agent_id):
        raise ValueError("agent id must be snake_case: a-z, 0-9 and _, starting with a letter")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError(f"{directory} is not empty")
    directory.mkdir(parents=True, exist_ok=True)
    template = resources.files(__package__).joinpath("templates/agent")
    _copy_template(
        template,
        directory,
        {"__AGENT_ID__": agent_id, "__AGENT_NAME__": name, "__TODAY__": date.today().isoformat()},
    )
    return directory


# -- check ---------------------------------------------------------------------------------


def _is_excluded(relative: Path) -> bool:
    return any(fnmatch.fnmatch(part, pattern) for part in relative.parts for pattern in EXCLUDED)


def check_agent(directory: Path) -> tuple[AgentManifest | None, list[Finding]]:
    findings: list[Finding] = []
    try:
        manifest = load_manifest(directory)
        validate_layout(manifest, directory)
    except ManagerError as exc:
        return None, [Finding("error", str(exc))]

    requirements = (directory / REQUIREMENTS_FILENAME).read_text(encoding="utf-8")
    if not re.search(r"^\s*langgraph\b", requirements, re.MULTILINE | re.IGNORECASE):
        findings.append(Finding("error", "requirements.txt must include langgraph"))
    if "OWNER/" in json.dumps(manifest.raw):
        findings.append(Finding("warning", "manifest.json still contains the OWNER placeholder"))
    source = urlsplit(manifest.runtime["source_url"])
    if source.hostname == "github.com" and "/releases/download/" not in source.path:
        findings.append(
            Finding(
                "error",
                "runtime.source_url must be a GitHub Release asset "
                "(…/releases/download/<tag>/<file>.zip)",
            )
        )
    if f"v{manifest.version}" not in source.path and manifest.version not in source.path:
        findings.append(
            Finding("warning", f"runtime.source_url does not mention version {manifest.version}")
        )
    if (directory / ".env").exists():
        findings.append(
            Finding("warning", ".env exists: it is never packaged, make sure it is not committed")
        )
    for path in directory.rglob("*.py"):
        relative = path.relative_to(directory)
        if _is_excluded(relative):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"""(sk-or-v1-|lsv2_)[A-Za-z0-9_-]{16,}""", text):
            findings.append(Finding("error", f"{relative}: looks like a hard-coded API key"))
    return manifest, findings


def install_check(directory: Path, settings: Settings) -> None:
    """Package and install the agent into a throw-away folder with uv + smoke test."""
    from .service import AgentManager

    with tempfile.TemporaryDirectory(prefix="agent-check-") as tmp:
        archive, _ = package_agent(directory, Path(tmp) / "agent.zip")
        scratch = dataclasses.replace(settings, home=Path(tmp) / "home")
        manager = AgentManager(scratch, StaticSecretProvider({}))
        try:
            manager.installer.install_archive(archive)
        finally:
            manager.shutdown()


# -- package -------------------------------------------------------------------------------


def package_agent(directory: Path, output: Path | None = None) -> tuple[Path, str]:
    """Deterministic zip with a single top folder named after the agent id."""
    manifest = load_manifest(directory)
    validate_layout(manifest, directory)
    output = output or directory / "dist" / f"{manifest.agent_id}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file()
        and not _is_excluded(path.relative_to(directory))
        and path.resolve() != output.resolve()
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            relative = path.relative_to(directory).as_posix()
            info = zipfile.ZipInfo(f"{manifest.agent_id}/{relative}", date_time=ZIP_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return output, digest


def registry_entry(manifest: AgentManifest, sha256: str) -> dict[str, Any]:
    """The catalog entry to paste into registry.json (manifest_url points at the tag)."""
    repo = urlsplit(str(manifest.raw.get("repository_url", ""))).path.strip("/")
    manifest_url = (
        f"https://raw.githubusercontent.com/{repo}/v{manifest.version}/{MANIFEST_FILENAME}"
    )
    entry: dict[str, Any] = {
        "id": manifest.agent_id,
        "name": manifest.name,
        "description": manifest.raw.get("description", ""),
        "latest": manifest.version,
        "versions": [
            {
                "version": manifest.version,
                "manifest_url": manifest_url,
                "source_url": manifest.runtime["source_url"],
                "sha256": sha256,
            }
        ],
    }
    if manifest.raw.get("creator_name"):
        entry["creator_name"] = manifest.raw["creator_name"]
    return entry
