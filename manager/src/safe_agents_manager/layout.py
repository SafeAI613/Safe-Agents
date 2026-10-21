"""Paths inside one installed agent version and the generated ``langgraph.json``."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import AgentManifest


@dataclass(frozen=True)
class AgentLayout:
    """``agents/<agent_id>/<version>/`` with ``src/``, ``.venv/``, ``inbox/`` and the config."""

    root: Path

    @property
    def src(self) -> Path:
        return self.root / "src"

    @property
    def venv(self) -> Path:
        return self.root / ".venv"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def langgraph_config(self) -> Path:
        return self.root / "langgraph.json"

    def venv_executable(self, name: str) -> Path:
        if os.name == "nt":
            return self.venv / "Scripts" / f"{name}.exe"
        return self.venv / "bin" / name


def build_langgraph_config(manifest: AgentManifest) -> dict[str, Any]:
    # Secrets are injected as process environment variables, never written to disk.
    return {
        "dependencies": ["./src"],
        "graphs": {manifest.graph_id: f"./src/{manifest.graph_entry}"},
        "env": {},
    }


def write_langgraph_config(layout: AgentLayout, manifest: AgentManifest) -> Path:
    path = layout.langgraph_config
    path.write_text(json.dumps(build_langgraph_config(manifest), indent=2), encoding="utf-8")
    return path
