"""Persistent record of installed agents (SQLite)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .layout import AgentLayout
from .manifest import AgentManifest, parse_manifest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id            TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    install_dir   TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    installed_at  TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class InstalledAgent:
    id: str
    version: str
    install_dir: Path
    manifest: AgentManifest
    installed_at: str

    @property
    def layout(self) -> AgentLayout:
        return AgentLayout(self.install_dir)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.manifest.name,
            "version": self.version,
            "graph_id": self.manifest.graph_id,
            "description": self.manifest.raw.get("description", ""),
            "installed_at": self.installed_at,
            "accepts_files": self.manifest.runtime.get("accepts_files", []),
            "hitl": bool(self.manifest.runtime.get("hitl", False)),
            "required_env": self.manifest.required_env,
            "optional_env": self.manifest.optional_env,
        }


class AgentStore:
    """One short-lived connection per call keeps this safe to use from several threads."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            with conn:  # commits on success, rolls back on error
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _row_to_agent(row: tuple[str, str, str, str, str]) -> InstalledAgent:
        agent_id, version, install_dir, manifest_json, installed_at = row
        return InstalledAgent(
            id=agent_id,
            version=version,
            install_dir=Path(install_dir),
            manifest=parse_manifest(json.loads(manifest_json)),
            installed_at=installed_at,
        )

    def save(self, manifest: AgentManifest, install_dir: Path) -> InstalledAgent:
        agent = InstalledAgent(
            id=manifest.agent_id,
            version=manifest.version,
            install_dir=install_dir,
            manifest=manifest,
            installed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agents (id, version, install_dir, manifest_json, installed_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET version=excluded.version,"
                " install_dir=excluded.install_dir, manifest_json=excluded.manifest_json,"
                " installed_at=excluded.installed_at",
                (
                    agent.id,
                    agent.version,
                    str(install_dir),
                    json.dumps(manifest.raw),
                    agent.installed_at,
                ),
            )
        return agent

    def get(self, agent_id: str) -> InstalledAgent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, version, install_dir, manifest_json, installed_at"
                " FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
        return self._row_to_agent(row) if row else None

    def list(self) -> list[InstalledAgent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, version, install_dir, manifest_json, installed_at"
                " FROM agents ORDER BY id"
            ).fetchall()
        return [self._row_to_agent(row) for row in rows]

    def delete(self, agent_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
