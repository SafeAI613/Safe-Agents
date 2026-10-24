"""Interactive command line: streaming node progress, HITL [Y/N], help, errors."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from agent.graph import builder

HELP = """Lorem ipsum dolor sit amet.
  help / ?   show this help
  exit       quit
Give a path to a local file to work on it."""


def load_env(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def run_turn(graph, payload, config) -> None:  # type: ignore[no-untyped-def]
    for mode, data in graph.stream(payload, config, stream_mode=["tasks", "updates"]):
        if mode == "tasks":
            if "result" not in data:  # a task without a result is a node that just started
                print(f"[INFO] Entering Node: {data['name']}")
            continue
        for node, update in data.items():
            if node == "__interrupt__":
                request = update[0].value
                print(f"[APPROVAL] {request.get('action')}: {request.get('details')}")
                answer = input("Approve? [Y/N] ").strip().lower()
                return run_turn(graph, Command(resume={"approved": answer == "y"}), config)
            for message in (update or {}).get("messages", []):
                if message.type == "ai" and message.text:
                    print(message.text)


def main() -> int:
    load_env()
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("[ERROR] OPENROUTER_API_KEY is missing (copy .env.example to .env)", file=sys.stderr)
        return 1
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    print("Type 'help' for instructions.")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if text in ("exit", "quit"):
            return 0
        if text in ("help", "?"):
            print(HELP)
            continue
        if not text:
            continue
        try:
            run_turn(graph, {"messages": [("user", text)]}, config)
        except Exception as exc:  # show errors clearly instead of a traceback
            print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
