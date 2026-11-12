"""Content guard: the judge, the policy, the stream filter and the guarded agent proxy."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import BASE_MANIFEST, manifest
from fastapi.testclient import TestClient

from safe_agents_manager.agent_proxy import (
    SseEvent,
    StreamFilter,
    _human_text,
    filter_sse,
    sanitize,
)
from safe_agents_manager.api import create_app
from safe_agents_manager.guard import (
    ALLOWED,
    BLOCKED_OUTPUT,
    CHUNK_CHARS,
    ContentGuard,
    Verdict,
    agent_scope,
)
from safe_agents_manager.manifest import parse_manifest
from safe_agents_manager.policy import (
    PolicyClient,
    bundled_policy,
    parse_policy,
    policy_url_for,
)
from safe_agents_manager.service import AgentManager

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
REPO_ROOT = Path(__file__).parents[2]


# -- a fake judge ---------------------------------------------------------------------------


class Judge:
    """Answers like an OpenRouter chat model: "bar" breaks the policy, "baz" is off-task."""

    def __init__(self, status: int = 200, answer: str | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = status
        self.answer = answer

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        text = body["messages"][1]["content"]
        if self.answer is not None:
            answer = self.answer
        elif "bar" in text:
            answer = '{"allowed": false, "category": "policy"}'
        elif "baz" in text and text.startswith("USER MESSAGE"):
            answer = '```json\n{"allowed": false, "category": "scope"}\n```'
        else:
            answer = '{"allowed": true, "category": "ok"}'
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": answer}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 7},
            },
        )


def make_guard(
    tmp_path: Path, judge: Judge, key: str | None = "sk-test"
) -> tuple[ContentGuard, list[tuple[Any, ...]]]:
    usage: list[tuple[Any, ...]] = []
    guard = ContentGuard(
        tmp_path / "guard.sqlite",
        "https://openrouter.test/api/v1",
        api_key=lambda: key,
        policy=bundled_policy,
        on_usage=lambda *args: usage.append(args),
        http=httpx.AsyncClient(transport=httpx.MockTransport(judge.handler)),
    )
    return guard, usage


FOO_MANIFEST = parse_manifest(BASE_MANIFEST)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# -- the judge ------------------------------------------------------------------------------


def test_input_is_judged_against_the_task_and_the_policy(tmp_path: Path) -> None:
    judge = Judge()
    guard, usage = make_guard(tmp_path, judge)
    assert run(guard.check_input("foo_agent", FOO_MANIFEST, "lorem ipsum")) == ALLOWED
    assert run(guard.check_input("foo_agent", FOO_MANIFEST, "baz?")) == Verdict(False, "scope")
    assert run(guard.check_input("foo_agent", FOO_MANIFEST, "bar!")) == Verdict(False, "policy")

    system = judge.requests[0]["messages"][0]["content"]
    assert "Foo Agent" in system  # the agent's declared task
    assert bundled_policy().blocked_topics[0] in system
    assert judge.requests[0]["model"] == bundled_policy().classifier_model
    assert judge.requests[0]["temperature"] == 0
    assert usage[0] == ("foo_agent", "", bundled_policy().classifier_model, 50, 7)


def test_output_is_judged_against_the_policy_only(tmp_path: Path) -> None:
    judge = Judge()
    guard, _ = make_guard(tmp_path, judge)
    assert run(guard.check_output("foo_agent", "baz")) == ALLOWED  # off-task is not a reason
    assert run(guard.check_output("foo_agent", "bar")) == Verdict(False, "policy")
    assert "Foo Agent" not in judge.requests[0]["messages"][0]["content"]


def test_verdicts_are_remembered_across_restarts(tmp_path: Path) -> None:
    judge = Judge()
    guard, _ = make_guard(tmp_path, judge)
    run(guard.check_output("foo_agent", "lorem"))
    again, _ = make_guard(tmp_path, judge)
    run(again.check_output("foo_agent", "lorem"))
    assert len(judge.requests) == 1


def test_empty_text_needs_no_judge(tmp_path: Path) -> None:
    judge = Judge()
    guard, _ = make_guard(tmp_path, judge)
    assert run(guard.check_output("foo_agent", "  \n")) == ALLOWED
    assert judge.requests == []


@pytest.mark.parametrize(
    ("judge", "key"),
    [
        (Judge(status=402), "sk-test"),
        (Judge(answer="I think it is fine"), "sk-test"),
        (Judge(), None),
    ],
)
def test_the_guard_fails_closed(tmp_path: Path, judge: Judge, key: str | None) -> None:
    guard, _ = make_guard(tmp_path, judge, key=key)
    verdict = run(guard.check_input("foo_agent", FOO_MANIFEST, "lorem"))
    assert verdict == Verdict(False, "unavailable")
    assert "אינה זמינה" in verdict.user_message
    # Not remembered: the next try asks again.
    judge.status, judge.answer = 200, None
    if key is not None:
        assert run(guard.check_input("foo_agent", FOO_MANIFEST, "lorem")) == ALLOWED


def test_long_text_is_judged_in_parts(tmp_path: Path) -> None:
    judge = Judge()
    guard, _ = make_guard(tmp_path, judge)
    text = "lorem " * (CHUNK_CHARS // 6) + "x" * CHUNK_CHARS + " bar"
    assert run(guard.check_output("foo_agent", text)) == Verdict(False, "policy")
    assert len(judge.requests) == 3


def test_allowed_input_is_not_judged_again_when_shown(tmp_path: Path) -> None:
    judge = Judge()
    guard, _ = make_guard(tmp_path, judge)
    run(guard.remember_allowed_output("lorem ipsum"))
    assert run(guard.check_output("foo_agent", "lorem ipsum")) == ALLOWED
    assert judge.requests == []


def test_agent_scope_comes_from_the_manifest() -> None:
    scope = agent_scope(FOO_MANIFEST)
    assert "Foo Agent" in scope
    for task in BASE_MANIFEST["tasks_capable_of_performing"]:
        assert task in scope


# -- the policy -----------------------------------------------------------------------------


def test_bundled_policy_matches_the_schema_copy() -> None:
    packaged = Path(__file__).parents[1] / "src/safe_agents_manager/schemas/policy.schema.json"
    canonical = REPO_ROOT / "schemas" / "policy.schema.json"
    assert json.loads(packaged.read_text("utf-8")) == json.loads(canonical.read_text("utf-8"))
    policy = bundled_policy()
    assert len(policy.scope_probes) >= 5 and 0 < policy.min_refusal_rate <= 1


def test_policy_sits_next_to_the_catalog() -> None:
    assert (
        policy_url_for("https://raw.githubusercontent.com/foo/registry/main/registry.json")
        == "https://raw.githubusercontent.com/foo/registry/main/policy.json"
    )
    assert policy_url_for("/tmp/foo/registry.json") == "/tmp/foo/policy.json"
    assert policy_url_for("C:\\foo\\registry.json") == "C:\\foo\\policy.json"


def test_invalid_policy_is_rejected() -> None:
    data = bundled_policy().to_dict()
    data.pop("source")
    data["scope_probes"] = ["lorem"]
    with pytest.raises(Exception, match="scope_probes"):
        parse_policy({"policy_version": 1, **data}, "catalog")


POLICY_URL = "https://raw.githubusercontent.com/foo/registry/main/policy.json"


def policy_doc(model: str = "foo/bar-judge") -> dict[str, Any]:
    data = bundled_policy().to_dict()
    data.pop("source")
    return {"policy_version": 1, **data, "classifier_model": model}


def test_policy_client_uses_catalog_then_cache_then_bundled(tmp_path: Path) -> None:
    answers: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hosts = frozenset({"raw.githubusercontent.com"})

    def fetch(refresh: bool = True) -> Any:
        return PolicyClient(POLICY_URL, tmp_path, client, hosts, ttl_s=60).get(refresh=refresh)

    answers.append(httpx.Response(503))
    assert fetch().source == "bundled"  # nothing fetched yet

    answers.append(httpx.Response(200, json=policy_doc(), headers={"etag": '"v1"'}))
    assert fetch().classifier_model == "foo/bar-judge"

    answers.append(httpx.Response(200, json={"policy_version": 1}))  # broken in the catalog
    kept = fetch()
    assert kept.classifier_model == "foo/bar-judge" and kept.source == "cache"

    answers.append(httpx.Response(304))
    assert fetch().classifier_model == "foo/bar-judge"


def test_local_catalog_reads_policy_next_to_it(tmp_path: Path) -> None:
    (tmp_path / "policy.json").write_text(json.dumps(policy_doc("foo/local")), "utf-8")
    client = PolicyClient(
        str(tmp_path / "policy.json"), tmp_path / "c", httpx.Client(), frozenset(), 60
    )
    assert client.get().classifier_model == "foo/local"
    missing = PolicyClient(
        str(tmp_path / "x.json"), tmp_path / "c2", httpx.Client(), frozenset(), 60
    )
    assert missing.get().source == "bundled"


def test_download_errors_fall_back(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hosts = frozenset({"raw.githubusercontent.com"})
    assert PolicyClient(POLICY_URL, tmp_path, client, hosts, 60).get().source == "bundled"


# -- message sanitizing and the stream filter -----------------------------------------------


async def fake_check(text: str) -> Verdict:
    return Verdict(False, "policy") if "bar" in text else ALLOWED


def test_sanitize_replaces_blocked_messages_wherever_they_are() -> None:
    state = {
        "values": {
            "messages": [
                {"type": "human", "content": "lorem", "id": "1"},
                {"type": "ai", "content": [{"type": "text", "text": "foo bar"}], "id": "2"},
                {"type": "tool", "content": "bar", "id": "3"},
                {"type": "system", "content": "bar", "id": "4"},
            ]
        },
        "next": [],
    }
    cleaned = run(sanitize(state, fake_check))
    contents = [m["content"] for m in cleaned["values"]["messages"]]
    assert contents == ["lorem", BLOCKED_OUTPUT, BLOCKED_OUTPUT, "bar"]
    assert cleaned["values"]["messages"][1]["id"] == "2"

    titles = run(sanitize([{"values": state["values"]}], fake_check, humans_only=True))
    assert titles[0]["values"]["messages"] == [state["values"]["messages"][0]]


def test_human_text_of_a_run() -> None:
    command = {
        "method": "run.start",
        "params": {"input": {"messages": [{"type": "human", "content": [{"text": "lorem"}]}]}},
    }
    assert _human_text(command) == "lorem"
    assert _human_text({"params": {"input": {"foo": "bar"}}}) == '{"foo": "bar"}'


def event(seq: int, method: str, data: dict[str, Any]) -> list[str]:
    envelope = {
        "type": "event",
        "seq": seq,
        "method": method,
        "params": {"namespace": [], "data": data},
    }
    return [f"event: {method}", "data: " + json.dumps(envelope), f"id: {seq}"]


def ai_message(text: str, seq: int = 1) -> list[list[str]]:
    words = text.split(" ")
    return [
        event(seq, "messages", {"event": "message-start", "role": "ai", "id": "m"}),
        event(
            seq + 1,
            "messages",
            {"event": "content-block-start", "index": 0, "content": {"type": "text", "text": ""}},
        ),
        *[
            event(
                seq + 2 + i,
                "messages",
                {
                    "event": "content-block-delta",
                    "index": 0,
                    "delta": {"type": "text-delta", "text": w + " "},
                },
            )
            for i, w in enumerate(words)
        ],
        event(
            seq + 9,
            "messages",
            {
                "event": "content-block-finish",
                "index": 0,
                "content": {"type": "text", "text": text},
            },
        ),
        event(seq + 10, "messages", {"event": "message-finish", "usage": {"input_tokens": 3}}),
    ]


def filtered(events: list[list[str]]) -> list[dict[str, Any]]:
    async def lines() -> Any:
        for item in events:
            for line in item:
                yield line
            yield ""

    async def collect() -> str:
        return "".join([chunk async for chunk in filter_sse(lines(), StreamFilter(fake_check))])

    text = run(collect())
    return [SseEvent(block.split("\n")).data for block in text.strip().split("\n\n")]


def test_allowed_message_passes_unchanged() -> None:
    out = filtered(ai_message("lorem ipsum"))
    assert len(out) == 6
    assert out[-2]["params"]["data"]["content"]["text"] == "lorem ipsum"


def test_blocked_message_is_never_sent() -> None:
    lifecycle = event(30, "lifecycle", {"event": "running"})
    out = filtered([*ai_message("lorem bar ipsum"), lifecycle])
    raw = json.dumps(out, ensure_ascii=False)
    assert "bar" not in raw and "ipsum" not in raw
    kinds = [e["params"]["data"].get("event") for e in out]
    assert kinds == [
        "message-start",
        "content-block-start",
        "content-block-delta",
        "content-block-finish",
        "message-finish",
        "running",
    ]
    assert out[2]["params"]["data"]["delta"]["text"] == BLOCKED_OUTPUT
    assert out[4]["params"]["data"]["usage"] == {"input_tokens": 3}  # cost still counted


def test_events_during_a_message_keep_their_order() -> None:
    message = ai_message("lorem")
    lifecycle = event(5, "lifecycle", {"event": "running"})
    out = filtered([message[0], lifecycle, *message[1:]])
    assert [e["seq"] for e in out] == [1, 5, 2, 3, 10, 11]


def test_values_and_approval_requests_are_checked() -> None:
    values = event(1, "values", {"messages": [{"type": "ai", "content": "bar", "id": "x"}]})
    approval = event(
        2,
        "input.requested",
        {"interrupt_id": "i", "payload": {"type": "approval", "action": "foo", "details": "bar"}},
    )
    out = filtered([values, approval])
    assert out[0]["params"]["data"]["messages"][0]["content"] == BLOCKED_OUTPUT
    payload = out[1]["params"]["data"]["payload"]
    assert payload["action"] == "foo" and "bar" not in json.dumps(payload)


def test_unfinished_message_is_checked_when_the_stream_ends() -> None:
    out = filtered(ai_message("lorem bar")[:4])
    assert "bar" not in json.dumps(out)


# -- the guarded proxy, end to end with the fake agent server -------------------------------


@pytest.fixture
def judge() -> Judge:
    return Judge()


@pytest.fixture
def proxied(
    manager: AgentManager, make_agent_zip: Callable[..., Path], judge: Judge, tmp_path: Path
) -> Iterator[tuple[TestClient, list[tuple[Any, ...]]]]:
    manager.install_archive(make_agent_zip())
    guard, usage = make_guard(tmp_path, judge)
    with TestClient(create_app(manager, TOKEN, guard=guard)) as client:
        yield client, usage


def command(text: str) -> dict[str, Any]:
    return {
        "id": 1,
        "method": "run.start",
        "params": {"input": {"messages": [{"type": "human", "content": text}]}},
    }


LG = "/agents/foo_agent/lg"


def stream_text(client: TestClient) -> str:
    with client.stream(
        "POST", f"{LG}/threads/t1/stream/events", headers=AUTH, json={"channels": ["messages"]}
    ) as response:
        assert response.status_code == 200
        return "".join(response.iter_text())


def test_proxy_needs_the_manager_token(proxied: tuple[TestClient, Any]) -> None:
    client, _ = proxied
    assert client.post(f"{LG}/threads/t1/commands", json=command("lorem")).status_code == 401


def test_allowed_message_reaches_the_agent_and_the_answer_streams_back(
    proxied: tuple[TestClient, Any], manager: AgentManager
) -> None:
    client, usage = proxied
    answer = client.post(f"{LG}/threads/t1/commands", headers=AUTH, json=command("lorem"))
    assert answer.json()["type"] == "success"
    assert "echo: lorem" in stream_text(client)
    state = client.get(f"{LG}/threads/t1/state", headers=AUTH).json()
    assert state["values"]["messages"][1]["content"][0]["text"] == "echo: lorem"
    assert usage and usage[0][1] == "t1"  # guard cost belongs to the conversation


def test_blocked_message_never_reaches_the_agent(
    proxied: tuple[TestClient, Any], manager: AgentManager
) -> None:
    client, _ = proxied
    answer = client.post(f"{LG}/threads/t1/commands", headers=AUTH, json=command("baz?"))
    body = answer.json()
    assert body == {
        "type": "error",
        "id": 1,
        "error": "content_blocked",
        "message": Verdict(False, "scope").user_message,
    }
    commands = manager.get_agent("foo_agent").install_dir / "commands.json"
    assert not commands.exists()


def test_blocked_answer_is_hidden_live_and_in_history(
    proxied: tuple[TestClient, Any], judge: Judge
) -> None:
    client, _ = proxied
    # The input passes; the echo the agent sends back contains a blocked word.
    judge.answer = '{"allowed": true}'
    client.post(f"{LG}/threads/t1/commands", headers=AUTH, json=command("lorem bar"))
    judge.answer = None
    streamed = stream_text(client)
    assert "echo: lorem bar" not in streamed and "echo" not in streamed
    assert BLOCKED_OUTPUT in streamed

    state = client.get(f"{LG}/threads/t1/state", headers=AUTH).json()
    assert state["values"]["messages"][1]["content"] == BLOCKED_OUTPUT
    titles = client.post(f"{LG}/threads/search", headers=AUTH, json={}).json()
    assert [m["type"] for m in titles[0]["values"]["messages"]] == ["human"]


def test_agent_token_never_reaches_the_ui(proxied: tuple[TestClient, Any]) -> None:
    client, _ = proxied
    started = client.post("/agents/foo_agent/start", headers=AUTH).json()
    listed = client.get("/agents/foo_agent", headers=AUTH).json()
    assert "token" not in started and "token" not in listed and "url" not in listed


def test_manifest_scope_is_used_for_installed_agents(
    proxied: tuple[TestClient, Any], judge: Judge
) -> None:
    client, _ = proxied
    client.post(f"{LG}/threads/t1/commands", headers=AUTH, json=command("lorem"))
    assert manifest()["name"] in judge.requests[0]["messages"][0]["content"]


def test_policy_endpoint(proxied: tuple[TestClient, Any], manager: AgentManager) -> None:
    client, _ = proxied
    manager.policy = PolicyClient(
        "/nonexistent/policy.json", manager.settings.home, httpx.Client(), frozenset(), 60
    )
    policy = client.get("/policy", headers=AUTH).json()
    assert policy["source"] == "bundled" and policy["blocked_topics"]


# -- the scope check (catalog gate) ---------------------------------------------------------


class ScopeJudge(Judge):
    """Probes with "baz" are off-task; an answer with "only help" declined."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        text = body["messages"][1]["content"]
        if text.startswith("QUESTION"):
            self.requests.append(body)
            declined = "only help" in text.split("ANSWER:")[1]
            answer = json.dumps({"declined": declined})
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
        return super().handler(request)


def scope_policy(probes: list[str], rate: float = 0.8) -> Any:
    data = policy_doc()
    data.update(scope_probes=probes, min_refusal_rate=rate)
    return parse_policy(data, "catalog")


def scope_report(
    manager: AgentManager,
    make_agent_zip: Callable[..., Path],
    tmp_path: Path,
    policy: Any,
    judge: Judge | None = None,
) -> Any:
    from safe_agents_manager.scope_check import run_scope_check

    agent = manager.install_archive(make_agent_zip())
    handle = manager.start(agent.id)
    guard = ContentGuard(
        tmp_path / "scope.sqlite",
        "https://openrouter.test/api/v1",
        api_key=lambda: "sk-test",
        policy=lambda: policy,
        http=httpx.AsyncClient(transport=httpx.MockTransport((judge or ScopeJudge()).handler)),
    )
    return run_scope_check(handle, agent.manifest, policy, guard)


OFF_TASK = [f"baz question {i}" for i in range(5)]


def test_scope_check_passes_when_the_agent_declines(
    manager: AgentManager, make_agent_zip: Callable[..., Path], tmp_path: Path
) -> None:
    report = scope_report(
        manager, make_agent_zip, tmp_path, scope_policy([*OFF_TASK, "lorem in task"])
    )
    assert report.problem is None
    assert report.refusal_rate == 1.0
    assert [r.outside_task for r in report.results] == [True] * 5 + [False]
    csv_path = tmp_path / "out" / "scope.csv"
    report.write_csv(csv_path)
    rows = csv_path.read_text("utf-8").splitlines()
    assert rows[0] == "question,outside_task,declined,seconds,answer,error"
    assert rows[1].startswith("baz question 0,True,True,")
    assert "skip (inside the task)" in report.summary()


def test_scope_check_fails_when_the_agent_answers_off_task(
    manager: AgentManager, make_agent_zip: Callable[..., Path], tmp_path: Path
) -> None:
    probes = [*OFF_TASK[:3], "baz poem 1", "baz poem 2"]
    report = scope_report(manager, make_agent_zip, tmp_path, scope_policy(probes))
    assert report.refusal_rate == pytest.approx(0.6)
    assert report.problem is not None and "60%" in report.problem
    assert "ANSWERED" in report.summary()


def test_scope_check_needs_enough_off_task_probes(
    manager: AgentManager, make_agent_zip: Callable[..., Path], tmp_path: Path
) -> None:
    probes = [*OFF_TASK[:4], "lorem in task"]
    report = scope_report(manager, make_agent_zip, tmp_path, scope_policy(probes))
    assert report.problem is not None and "scope_probes" in report.problem


def test_scope_check_fails_when_it_cannot_judge(
    manager: AgentManager, make_agent_zip: Callable[..., Path], tmp_path: Path
) -> None:
    report = scope_report(
        manager, make_agent_zip, tmp_path, scope_policy(OFF_TASK), judge=Judge(status=500)
    )
    assert report.problem is not None and "could not judge" in report.problem


def test_a_run_that_ends_mid_message_releases_what_is_held() -> None:
    message = ai_message("lorem bar")
    cancelled = event(40, "lifecycle", {"event": "cancelled"})
    after = event(41, "lifecycle", {"event": "running"})

    async def collect() -> list[list[str]]:
        stream_filter = StreamFilter(fake_check)
        released = []
        for item in [*message[:3], cancelled, after]:
            released.append(await stream_filter.feed(SseEvent(item)))
        return released

    released = run(collect())
    assert released[:3] == [[], [], []]  # held while the message is open
    assert len(released[3]) == 4 and "bar" not in "".join(released[3])
    assert len(released[4]) == 1  # the stream goes on for the next run


def test_policy_for_the_guard_never_waits_on_the_network(tmp_path: Path) -> None:
    import threading

    gate = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        gate.wait(5)
        return httpx.Response(200, json=policy_doc("foo/fresh"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    hosts = frozenset({"raw.githubusercontent.com"})
    policies = PolicyClient(POLICY_URL, tmp_path, client, hosts, ttl_s=60)
    assert policies.current().source == "bundled"  # returns at once; the fetch runs behind
    gate.set()
    for _ in range(100):
        if policies.current().classifier_model == "foo/fresh":
            break
        threading.Event().wait(0.05)
    assert policies.current().classifier_model == "foo/fresh"
