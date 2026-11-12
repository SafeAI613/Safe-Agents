"""The only way the desktop UI reaches an agent server: a reverse proxy with the content guard.

The UI talks LangGraph's v2 protocol to ``/agents/{id}/lg/...``. The agent's own token never
leaves the manager, so the UI cannot bypass the guard. What the proxy does:

- ``POST threads/{id}/commands``: a new run's user message is checked against the agent's
  task and the platform policy before it is forwarded; a blocked one never reaches the agent.
- ``POST threads/{id}/stream/events`` (SSE): text of an AI message is held until the message
  is complete and has been checked; blocked text is replaced before it is sent. Messages in
  ``values`` events and approval requests are checked too.
- Any other JSON answer (thread state, history, search) has its messages checked the same
  way, so reopening a conversation shows nothing that would be blocked live.
- Other event streams (the older run streaming API) are refused: they cannot be filtered.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .guard import BLOCKED_OUTPUT, ContentGuard, Verdict
from .layout import AGENT_TOKEN_HEADER
from .service import AgentManager

log = logging.getLogger(__name__)

CheckText = Callable[[str], Awaitable[Verdict]]

_COMMANDS = re.compile(r"^threads/(?P<thread>[^/]+)/commands$")
_EVENTS = re.compile(r"^threads/(?P<thread>[^/]+)/stream/events$")
_THREAD = re.compile(r"^threads/(?P<thread>[^/]+)(/|$)")
_SEARCH = "threads/search"

# Request headers that belong to the UI's connection to the manager, not to the agent.
_DROP_REQUEST = {"host", "authorization", "content-length", "cookie", "origin", "referer"}
_DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}

_SYSTEM_TYPES = {"system", "SystemMessage", "developer"}
# The event stream stays open across runs: a run that ends mid-message (stopped, failed)
# must release what is held, or the UI would wait forever.
_RUN_ENDED = {"completed", "failed", "interrupted", "cancelled", "error"}


# -- messages -------------------------------------------------------------------------------


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _is_message(value: Any) -> bool:
    return isinstance(value, dict) and "content" in value and ("type" in value or "role" in value)


def _message_type(message: dict[str, Any]) -> str:
    return str(message.get("type") or message.get("role") or "")


async def sanitize(value: Any, check: CheckText, *, humans_only: bool = False) -> Any:
    """Return ``value`` with every blocked message's text replaced (or dropped).

    Walks any JSON: every ``messages`` list found is checked, wherever it is nested.
    With ``humans_only`` only user messages are kept (thread search needs just the titles).
    """
    if isinstance(value, list):
        return [await sanitize(item, check, humans_only=humans_only) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key == "messages" and isinstance(item, list):
            result[key] = await _sanitize_messages(item, check, humans_only)
        else:
            result[key] = await sanitize(item, check, humans_only=humans_only)
    return result


async def _sanitize_messages(messages: list[Any], check: CheckText, humans_only: bool) -> list[Any]:
    kept = []
    for message in messages:
        if not _is_message(message):
            kept.append(message)
            continue
        kind = _message_type(message)
        if humans_only and kind not in ("human", "user", "HumanMessage"):
            continue
        if kind in _SYSTEM_TYPES:
            kept.append(message)  # never shown to the user
            continue
        verdict = await check(content_text(message["content"]))
        if verdict.allowed:
            kept.append(message)
        else:
            kept.append({**message, "content": BLOCKED_OUTPUT})
    return kept


# -- the SSE filter -------------------------------------------------------------------------


@dataclass
class SseEvent:
    lines: list[str]

    @property
    def data(self) -> Any:
        payload = "\n".join(line[5:].lstrip() for line in self.lines if line.startswith("data:"))
        if not payload:
            return None
        try:
            return json.loads(payload)
        except ValueError:
            return None

    def with_data(self, data: Any) -> SseEvent:
        kept = [line for line in self.lines if not line.startswith("data:")]
        return SseEvent([*kept, "data: " + json.dumps(data, ensure_ascii=False)])

    def render(self) -> str:
        return "\n".join(self.lines) + "\n\n"


@dataclass
class _Message:
    """One AI message being streamed: its text so far, per content block."""

    deltas: dict[int, list[str]] = field(default_factory=dict)
    finished: dict[int, str] = field(default_factory=dict)
    text_index: int | None = None  # the first text block; a blocked message keeps only it
    verdict: Verdict | None = None
    notice_sent: bool = False

    def add(self, data: dict[str, Any]) -> None:
        kind = data.get("event")
        index = data.get("index", 0)
        if kind in ("content-block-start", "content-block-finish"):
            block = data.get("content") or {}
            if block.get("type") != "text":
                return
            if self.text_index is None:
                self.text_index = index
            if kind == "content-block-finish":
                self.finished[index] = str(block.get("text") or "")
            else:
                self.deltas.setdefault(index, []).append(str(block.get("text") or ""))
        elif kind == "content-block-delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text-delta":
                if self.text_index is None:
                    self.text_index = index
                self.deltas.setdefault(index, []).append(str(delta.get("text") or ""))

    @property
    def text(self) -> str:
        indexes = sorted(set(self.deltas) | set(self.finished))
        return "".join(self.finished.get(i, "".join(self.deltas.get(i, []))) for i in indexes)


class StreamFilter:
    """Holds back AI message events until the message is complete and checked."""

    def __init__(self, check: CheckText) -> None:
        self._check = check
        self._open: dict[str, _Message] = {}
        self._buffer: list[tuple[SseEvent, _Message | None]] = []

    async def feed(self, event: SseEvent) -> list[str]:
        data = event.data
        method = data.get("method") if isinstance(data, dict) else None
        if method == "messages":
            params = data.get("params") or {}
            namespace = json.dumps(params.get("namespace") or [])
            body = params.get("data") or {}
            if body.get("event") == "message-start":
                self._open[namespace] = _Message()
            message = self._open.get(namespace)
            if message is not None:
                message.add(body)
                if body.get("event") == "message-finish":
                    del self._open[namespace]
                    message.verdict = await self._check(message.text)
            self._buffer.append((event, message))
        else:
            self._buffer.append((await self._filter_other(event, data), None))
            if method == "lifecycle" and _run_ended(data):
                return await self.finish()
        return self._flush() if not self._open else []

    async def finish(self) -> list[str]:
        """The upstream ended: check what was never finished and release everything."""
        for message in self._open.values():
            message.verdict = await self._check(message.text)
        self._open.clear()
        return self._flush()

    async def _filter_other(self, event: SseEvent, data: Any) -> SseEvent:
        if not isinstance(data, dict):
            return event
        if data.get("method") == "input.requested":
            body = (data.get("params") or {}).get("data") or {}
            payload = body.get("payload")
            verdict = await self._check(json.dumps(payload, ensure_ascii=False))
            if verdict.allowed:
                return event
            hidden = {"type": "approval", "action": "", "details": {"note": BLOCKED_OUTPUT}}
            if isinstance(payload, dict) and isinstance(payload.get("action"), str):
                hidden["action"] = payload["action"]
            params = {**data["params"], "data": {**body, "payload": hidden}}
            return event.with_data({**data, "params": params})
        cleaned = await sanitize(data, self._check)
        return event if cleaned == data else event.with_data(cleaned)

    def _flush(self) -> list[str]:
        out = []
        for event, message in self._buffer:
            if message is None or message.verdict is None or message.verdict.allowed:
                out.append(event.render())
                continue
            replaced = _blocked_event(event, message)
            if replaced is not None:
                out.append(replaced.render())
        self._buffer.clear()
        return out


def _run_ended(data: dict[str, Any]) -> bool:
    body = (data.get("params") or {}).get("data") or {}
    return body.get("event") in _RUN_ENDED


def _blocked_event(event: SseEvent, message: _Message) -> SseEvent | None:
    """The event as it is sent for a blocked message: only the notice is left as its text."""
    data = event.data
    params = data["params"]
    body = params["data"]
    kind = body.get("event")
    if kind in ("message-start", "message-finish"):
        return event  # keeps the message id and its token usage
    if body.get("index", 0) != message.text_index:
        return None
    if kind == "content-block-start":
        body = {**body, "content": {"type": "text", "text": ""}}
    elif kind == "content-block-finish":
        body = {**body, "content": {"type": "text", "text": BLOCKED_OUTPUT}}
    elif kind == "content-block-delta":
        if message.notice_sent:
            return None
        message.notice_sent = True
        body = {**body, "delta": {"type": "text-delta", "text": BLOCKED_OUTPUT}}
    else:
        return None
    return event.with_data({**data, "params": {**params, "data": body}})


async def filter_sse(lines: AsyncIterator[str], stream_filter: StreamFilter) -> AsyncIterator[str]:
    current: list[str] = []
    async for line in lines:
        if line:
            current.append(line)
            continue
        if current:
            for chunk in await stream_filter.feed(SseEvent(current)):
                yield chunk
            current = []
    if current:
        for chunk in await stream_filter.feed(SseEvent(current)):
            yield chunk
    for chunk in await stream_filter.finish():
        yield chunk


# -- the proxy ------------------------------------------------------------------------------


def _human_text(command: dict[str, Any]) -> str:
    """The user text of a ``run.start`` command (the whole input if it has no messages)."""
    run_input = (command.get("params") or {}).get("input")
    if not isinstance(run_input, dict):
        return "" if run_input is None else json.dumps(run_input, ensure_ascii=False)
    messages = run_input.get("messages")
    if not isinstance(messages, list):
        return json.dumps(run_input, ensure_ascii=False)
    texts = [
        content_text(m.get("content"))
        for m in messages
        if isinstance(m, dict) and _message_type(m) in ("human", "user")
    ]
    return "\n".join(t for t in texts if t)


class AgentProxy:
    def __init__(self, manager: AgentManager, guard: ContentGuard) -> None:
        self._manager = manager
        self._guard = guard
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            # No read timeout: an event stream stays open while the agent works or waits.
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def handle(self, request: Request, agent_id: str, path: str) -> Response:
        agent = await run_in_threadpool(self._manager.get_agent, agent_id)
        # The UI starts the agent before a chat; an idle stop in between is undone here.
        handle = await run_in_threadpool(self._manager.start, agent_id)
        self._manager.supervisor.touch(agent_id)
        thread = _THREAD.match(path)
        thread_id = thread.group("thread") if thread and path != _SEARCH else ""

        async def check_output(text: str) -> Verdict:
            return await self._guard.check_output(agent_id, text, thread_id)

        body = await request.body()
        if request.method == "POST" and _COMMANDS.match(path):
            blocked = await self._check_command(agent_id, agent.manifest, thread_id, body)
            if blocked is not None:
                return blocked

        headers = {
            key: value for key, value in request.headers.items() if key.lower() not in _DROP_REQUEST
        }
        headers["accept-encoding"] = "identity"  # the filter reads the stream as text
        headers[AGENT_TOKEN_HEADER] = handle.token
        upstream = self.client.build_request(
            request.method,
            f"{handle.url}/{path}",
            params=request.query_params,
            headers=headers,
            content=body,
        )
        try:
            response = await self.client.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            log.warning("agent %s did not answer: %s", agent_id, exc)
            return JSONResponse(
                status_code=502,
                content={"error": {"code": "agent_unreachable", "message": str(exc)}},
            )
        response_headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in _DROP_RESPONSE and not key.lower().startswith("access-control-")
        }
        content_type = response.headers.get("content-type", "")

        if "text/event-stream" in content_type:
            if not _EVENTS.match(path):
                await response.aclose()
                return JSONResponse(
                    status_code=501,
                    content={
                        "error": {
                            "code": "stream_not_supported",
                            "message": "only the v2 event stream can pass the content guard",
                        }
                    },
                )

            async def events() -> AsyncIterator[str]:
                stream_filter = StreamFilter(check_output)
                try:
                    async for chunk in filter_sse(response.aiter_lines(), stream_filter):
                        self._manager.supervisor.touch(agent_id)
                        yield chunk
                finally:
                    await response.aclose()

            return StreamingResponse(
                events(), status_code=response.status_code, headers=response_headers
            )

        raw = await response.aread()
        await response.aclose()
        if "application/json" not in content_type or not raw:
            return Response(raw, status_code=response.status_code, headers=response_headers)
        try:
            payload = json.loads(raw)
        except ValueError:
            return Response(raw, status_code=response.status_code, headers=response_headers)
        cleaned = await sanitize(payload, check_output, humans_only=path == _SEARCH)
        return JSONResponse(cleaned, status_code=response.status_code, headers=response_headers)

    async def _check_command(
        self, agent_id: str, manifest: Any, thread_id: str, body: bytes
    ) -> Response | None:
        """A refusal for a command whose user text the guard blocks; None to forward it."""
        try:
            command = json.loads(body)
        except ValueError:
            return None
        if not isinstance(command, dict):
            return None
        method = command.get("method")
        if method == "run.start":
            text = _human_text(command)
            verdict = await self._guard.check_input(agent_id, manifest, text, thread_id)
            if verdict.allowed:
                # Passed the stricter input check: no need to judge it again when shown.
                await self._guard.remember_allowed_output(text)
        elif method == "input.respond":
            response = (command.get("params") or {}).get("response")
            if not isinstance(response, dict) or set(response) <= {"approved"}:
                return None
            text = json.dumps(response, ensure_ascii=False)
            verdict = await self._guard.check_output(agent_id, text, thread_id)
        else:
            return None
        if verdict.allowed:
            return None
        return JSONResponse(
            {
                "type": "error",
                "id": command.get("id"),
                "error": "guard_unavailable"
                if verdict.category == "unavailable"
                else "content_blocked",
                "message": verdict.user_message,
            }
        )
