"""Token usage and cost ledger (SQLite). One row per AI message, so reporting twice is harmless."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .openrouter import Pricing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    message_id    TEXT PRIMARY KEY,
    agent_id      TEXT NOT NULL,
    thread_id     TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd      REAL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_agent_thread ON usage (agent_id, thread_id);
"""


@dataclass(frozen=True)
class UsageItem:
    message_id: str
    model: str
    input_tokens: int
    output_tokens: int


def price_for(model: str, pricing: Pricing) -> dict[str, float] | None:
    if model in pricing:
        return pricing[model]
    # Providers sometimes report a dated variant ("foo/bar-20240101"); try the base id.
    base = model.rsplit("-", 1)[0]
    return pricing.get(base)


def cost_of(item: UsageItem, pricing: Pricing) -> float | None:
    price = price_for(item.model, pricing)
    if price is None:
        return None
    return item.input_tokens * price["prompt"] + item.output_tokens * price["completion"]


class UsageStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def record(
        self, agent_id: str, thread_id: str, items: list[UsageItem], pricing: Pricing
    ) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = [
            (
                i.message_id,
                agent_id,
                thread_id,
                i.model,
                i.input_tokens,
                i.output_tokens,
                cost_of(i, pricing),
                now,
            )
            for i in items
        ]
        with self._connect() as conn:
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO usage VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            return conn.total_changes - before

    def summary(self, agent_id: str | None = None, thread_id: str | None = None) -> dict[str, Any]:
        where, params = [], []
        if agent_id:
            where.append("agent_id = ?")
            params.append(agent_id)
        if thread_id:
            where.append("thread_id = ?")
            params.append(thread_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd),"
                f" SUM(cost_usd IS NULL) FROM usage {clause} GROUP BY model ORDER BY model",
                params,
            ).fetchall()
        by_model = [
            {
                "model": model,
                "calls": calls,
                "input_tokens": tin,
                "output_tokens": tout,
                "cost_usd": cost or 0.0,
                "priced": unpriced == 0,
            }
            for model, calls, tin, tout, cost, unpriced in rows
        ]
        return {
            "input_tokens": sum(m["input_tokens"] for m in by_model),
            "output_tokens": sum(m["output_tokens"] for m in by_model),
            "cost_usd": sum(m["cost_usd"] for m in by_model),
            "unpriced_models": [m["model"] for m in by_model if not m["priced"]],
            "by_model": by_model,
        }
