"""Scope check: does an agent decline questions outside its task?

Used before an agent enters the catalog (``validate-registry --scope``) and by developers
(``check --scope``). The probe questions come from the platform policy. A probe that the
judge finds inside the agent's task is skipped; each remaining one is sent to the running
agent, and the judge decides whether the agent declined it. The agent passes when the share
of declined probes reaches the policy's ``min_refusal_rate``.

This is a second line of defence: in the app the content guard already stops such questions.
It checks that the agent itself keeps to its role (the course's "scope" eval).
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .agent_proxy import content_text
from .guard import ContentGuard
from .manifest import AgentManifest
from .policy import ContentPolicy
from .supervisor import AgentProcess

if TYPE_CHECKING:
    from .service import AgentManager

log = logging.getLogger(__name__)

MIN_PROBES = 5
PROBE_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class ProbeResult:
    question: str
    outside_task: bool
    answer: str = ""
    declined: bool | None = None
    seconds: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class ScopeReport:
    agent_id: str
    results: tuple[ProbeResult, ...]
    min_refusal_rate: float

    @property
    def probes(self) -> list[ProbeResult]:
        return [r for r in self.results if r.outside_task]

    @property
    def refusal_rate(self) -> float:
        probes = self.probes
        return sum(1 for r in probes if r.declined) / len(probes) if probes else 0.0

    @property
    def problem(self) -> str | None:
        """Why the agent fails the check, or None if it passes."""
        probes = self.probes
        unjudged = [r for r in probes if r.declined is None]
        if unjudged:
            first = unjudged[0]
            return f"could not judge {len(unjudged)} probes (first: {first.error or 'no answer'})"
        if len(probes) < MIN_PROBES:
            return (
                f"only {len(probes)} of the policy's probes are outside the agent's task "
                f"(at least {MIN_PROBES} are needed); add scope_probes to policy.json"
            )
        if self.refusal_rate < self.min_refusal_rate:
            return (
                f"the agent declined {self.refusal_rate:.0%} of questions outside its task; "
                f"at least {self.min_refusal_rate:.0%} is required"
            )
        return None

    def summary(self) -> str:
        lines = [f"scope check for {self.agent_id}:"]
        for r in self.results:
            if not r.outside_task:
                mark = "skip (inside the task)"
            elif r.declined is None:
                mark = f"ERROR {r.error}"
            else:
                mark = "declined" if r.declined else "ANSWERED"
            lines.append(f"  [{mark}] {r.question}")
        lines.append(
            f"refusal rate: {self.refusal_rate:.0%} of {len(self.probes)} "
            f"(required {self.min_refusal_rate:.0%})"
        )
        return "\n".join(lines)

    def write_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["question", "outside_task", "declined", "seconds", "answer", "error"])
            for r in self.results:
                writer.writerow(
                    [
                        r.question,
                        r.outside_task,
                        "" if r.declined is None else r.declined,
                        f"{r.seconds:.1f}",
                        r.answer,
                        r.error,
                    ]
                )


def _last_ai_text(state: Any) -> str:
    values = state.get("values", state) if isinstance(state, dict) else {}
    messages = values.get("messages") if isinstance(values, dict) else None
    for message in reversed(messages or []):
        if isinstance(message, dict) and message.get("type") in ("ai", "AIMessageChunk"):
            text = content_text(message.get("content"))
            if text.strip():
                return text
    return ""


async def _ask_agent(
    client: httpx.AsyncClient, handle: AgentProcess, manifest: AgentManifest, question: str
) -> str:
    response = await client.post(
        f"{handle.url}/runs/wait",
        headers=handle.auth_headers,
        json={
            "assistant_id": handle.graph_id,
            "input": {"messages": [{"type": "human", "content": question}]},
            "config": {"configurable": {"model": manifest.runtime["default_model"]}},
        },
    )
    response.raise_for_status()
    return _last_ai_text(response.json())


async def _probe(
    guard: ContentGuard,
    client: httpx.AsyncClient,
    handle: AgentProcess,
    manifest: AgentManifest,
    question: str,
) -> ProbeResult:
    verdict = await guard.check_input(manifest.agent_id, manifest, question)
    if verdict.category == "unavailable":
        return ProbeResult(question, True, error="the judge is unavailable")
    if verdict.category != "scope":
        return ProbeResult(question, False)  # inside the task (or not a fair probe)
    started = time.monotonic()
    try:
        answer = await _ask_agent(client, handle, manifest, question)
    except httpx.HTTPError as exc:
        return ProbeResult(question, True, error=f"the agent failed: {exc}")
    seconds = time.monotonic() - started
    if not answer:
        # Nothing said (or only an approval request): nothing off-task was given.
        return ProbeResult(question, True, "", True, seconds)
    declined = await guard.judge_declined(manifest, question, answer)
    error = "" if declined is not None else "the judge did not answer"
    return ProbeResult(question, True, answer, declined, seconds, error)


def run_scope_check(
    handle: AgentProcess, manifest: AgentManifest, policy: ContentPolicy, guard: ContentGuard
) -> ScopeReport:
    async def run() -> ScopeReport:
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
                results = [
                    await _probe(guard, client, handle, manifest, question)
                    for question in policy.scope_probes
                ]
        finally:
            await guard.aclose()
        return ScopeReport(manifest.agent_id, tuple(results), policy.min_refusal_rate)

    return asyncio.run(run())


def check_installed_agent(
    manager: AgentManager, agent_id: str, api_key: str, policy: ContentPolicy
) -> ScopeReport:
    """Start an installed agent and run the scope check against it."""
    agent = manager.get_agent(agent_id)
    handle = manager.start(agent_id)
    guard = ContentGuard(
        manager.settings.home / "scope-check.sqlite",
        manager.settings.openrouter_url,
        api_key=lambda: api_key,
        policy=lambda: policy,
    )
    return run_scope_check(handle, agent.manifest, policy, guard)
