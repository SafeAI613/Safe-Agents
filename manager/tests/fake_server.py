"""Stand-in for ``langgraph dev`` in tests: serves /ok and /assistants/search on a port.

It writes the environment it received to ``env.json`` in its working directory so tests
can check which variables the supervisor passed through.

For the content-guard proxy it also speaks a small part of the v2 thread protocol: a
``run.start`` command is recorded in ``commands.json``, and the event stream answers with
one AI message that echoes the user text ("echo: <text>"), in the real server's format.
"""

import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/ok":
            self._send(200, {"ok": True})
        elif re.fullmatch(r"/threads/[^/]+/state", self.path):
            if not self._authorized():
                return
            self._send(200, {"values": {"messages": _messages()}})
        else:
            self._send(404, {})

    def _events(self) -> None:
        reply = "echo: " + _last_text()
        events = [
            ("messages", {"event": "message-start", "role": "ai", "id": "ai-1"}),
            (
                "messages",
                {
                    "event": "content-block-start",
                    "index": 0,
                    "content": {"type": "text", "text": ""},
                },
            ),
            *[
                (
                    "messages",
                    {
                        "event": "content-block-delta",
                        "index": 0,
                        "delta": {"type": "text-delta", "text": word},
                    },
                )
                for word in re.split(r"(\s+)", reply)
                if word
            ],
            (
                "messages",
                {
                    "event": "content-block-finish",
                    "index": 0,
                    "content": {"type": "text", "text": reply},
                },
            ),
            (
                "messages",
                {"event": "message-finish", "usage": {"input_tokens": 10, "output_tokens": 5}},
            ),
            ("values", {"messages": _messages()}),
            ("lifecycle", {"event": "completed", "graph_name": "foo_agent"}),
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        for seq, (method, data) in enumerate(events, start=1):
            envelope = {
                "type": "event",
                "seq": seq,
                "method": method,
                "params": {"namespace": [], "data": data},
            }
            chunk = f"event: {method}\ndata: {json.dumps(envelope)}\nid: {seq}\n\n"
            self.wfile.write(chunk.encode())
        self.wfile.flush()
        self.close_connection = True

    def _authorized(self) -> bool:
        token = os.environ.get("SAFE_AGENTS_AGENT_TOKEN", "")
        if token and self.headers.get("x-api-key") == token:
            return True
        self._send(401, {"detail": "invalid agent token"})
        return False

    def do_POST(self) -> None:
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        if re.fullmatch(r"/threads/[^/]+/commands", self.path):
            Path("commands.json").write_text(json.dumps(body))
            self._send(200, {"type": "success", "id": body.get("id"), "result": {}})
        elif re.fullmatch(r"/threads/[^/]+/stream/events", self.path):
            self._events()
        elif self.path == "/runs/wait":
            # Scope check: declines everything except questions that mention a poem.
            question = body["input"]["messages"][-1]["content"]
            reply = "Here is a poem: lorem." if "poem" in question else "I only help with foo."
            self._send(200, {"messages": [{"type": "ai", "content": reply}]})
        elif self.path == "/threads/search":
            self._send(200, [{"thread_id": "t1", "values": {"messages": _messages()}}])
        elif self.path == "/assistants/search":
            config = json.loads(Path(sys.argv[sys.argv.index("--config") + 1]).read_text())
            found = body.get("graph_id") in config["graphs"] and "--no-graphs" not in sys.argv
            self._send(200, [{"graph_id": body["graph_id"]}] if found else [])
        else:
            self._send(404, {})

    def log_message(self, *args: object) -> None:
        pass


def _last_text() -> str:
    try:
        command = json.loads(Path("commands.json").read_text())
    except OSError:
        return ""
    return str(command["params"]["input"]["messages"][-1]["content"])


def _messages() -> list[dict[str, object]]:
    text = _last_text()
    return [
        {"type": "human", "content": text, "id": "human-1"},
        {"type": "ai", "content": [{"type": "text", "text": "echo: " + text}], "id": "ai-1"},
    ]


def _stop_on_stdin(server: HTTPServer) -> None:
    # Same protocol as platform_launcher.py: "shutdown" or EOF on stdin stops the server.
    for line in sys.stdin:
        if line.strip() == "shutdown":
            break
    server.shutdown()


if __name__ == "__main__":
    import threading

    Path("env.json").write_text(json.dumps(dict(os.environ)))
    httpd = HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
    if "--ignore-stdin" not in sys.argv:
        threading.Thread(target=_stop_on_stdin, args=(httpd,), daemon=True).start()
    httpd.serve_forever(poll_interval=0.05)
