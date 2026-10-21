"""Loading and validating an agent's ``manifest.json``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import ManifestError

MANIFEST_FILENAME = "manifest.json"
REQUIREMENTS_FILENAME = "requirements.txt"
# Always provided by the platform, whether or not the agent declares it.
OPENROUTER_KEY = "OPENROUTER_API_KEY"


@cache
def _validator() -> Draft202012Validator:
    text = resources.files(__package__).joinpath("schemas/manifest.schema.json").read_text("utf-8")
    return Draft202012Validator(json.loads(text), format_checker=FormatChecker())


@dataclass(frozen=True)
class AgentManifest:
    raw: dict[str, Any]

    @property
    def runtime(self) -> dict[str, Any]:
        runtime: dict[str, Any] = self.raw["runtime"]
        return runtime

    @property
    def agent_id(self) -> str:
        return str(self.runtime["graph_id"])

    @property
    def graph_id(self) -> str:
        return str(self.runtime["graph_id"])

    @property
    def name(self) -> str:
        return str(self.raw["name"])

    @property
    def version(self) -> str:
        return str(self.raw["version"])

    @property
    def python_spec(self) -> str:
        return str(self.runtime["python"])

    @property
    def graph_file(self) -> PurePosixPath:
        return PurePosixPath(self.runtime["graph_entry"].rsplit(":", 1)[0])

    @property
    def graph_entry(self) -> str:
        return str(self.runtime["graph_entry"])

    @property
    def required_env(self) -> list[str]:
        keys = [OPENROUTER_KEY, *self.runtime.get("env_required", [])]
        return list(dict.fromkeys(keys))

    @property
    def optional_env(self) -> list[str]:
        keys = list(self.runtime.get("env_optional", []))
        for server in self.runtime.get("mcp_servers", []):
            keys.extend(field["key"] for field in server.get("auth", []))
        required = set(self.required_env)
        return [key for key in dict.fromkeys(keys) if key not in required]

    def secret_fields(self) -> list[dict[str, Any]]:
        """Every secret the agent can receive, with a label for the settings form."""
        fields: dict[str, dict[str, Any]] = {}
        for key in self.required_env:
            fields[key] = {"key": key, "label": key, "required": True, "source": "env"}
        for key in self.runtime.get("env_optional", []):
            fields.setdefault(key, {"key": key, "label": key, "required": False, "source": "env"})
        for server in self.runtime.get("mcp_servers", []):
            for item in server.get("auth", []):
                fields[item["key"]] = {
                    "key": item["key"],
                    "label": item["label"],
                    "required": False,
                    "source": f"mcp:{server['name']}",
                }
        return list(fields.values())

    @property
    def supported_models(self) -> list[str]:
        models = [self.runtime["default_model"]]
        models += self.raw.get("technical_specifications", {}).get("supported_models", [])
        return list(dict.fromkeys(models))


def parse_manifest(data: Any) -> AgentManifest:
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors[:10]
        )
        raise ManifestError(f"manifest.json is invalid: {details}")
    manifest = AgentManifest(raw=data)
    if ".." in manifest.graph_file.parts or manifest.graph_file.is_absolute():
        raise ManifestError("runtime.graph_entry must be a relative path inside the agent folder")
    return manifest


def load_manifest(source_dir: Path) -> AgentManifest:
    path = source_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise ManifestError(f"{MANIFEST_FILENAME} was not found in the agent root folder")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_FILENAME} is not valid JSON: {exc}") from exc
    return parse_manifest(data)


def validate_layout(manifest: AgentManifest, source_dir: Path) -> None:
    """Check that the files the manifest points to actually exist inside ``source_dir``."""
    graph_path = (source_dir / manifest.graph_file).resolve()
    if not graph_path.is_relative_to(source_dir.resolve()):
        raise ManifestError("runtime.graph_entry points outside the agent folder")
    if not graph_path.is_file():
        raise ManifestError(f"graph file '{manifest.graph_file}' does not exist")
    if not (source_dir / REQUIREMENTS_FILENAME).is_file():
        raise ManifestError(f"{REQUIREMENTS_FILENAME} was not found in the agent root folder")
