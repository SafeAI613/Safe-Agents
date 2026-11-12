"""Minimal stand-in for the OpenRouter API endpoints the manager calls (E2E tests only).

The chat endpoint plays the content guard's judge: text with "forbidden" breaks the policy,
a user message with "off-topic" is outside the agent's task, and agent text with
"hidden-answer" breaks the policy (a user may ask for it; the agent may not say it).
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

GOOD_KEY = "sk-or-e2e-good"
MODELS = {
    "data": [
        {
            "id": "meta-llama/llama-3.1-70b-instruct",
            "pricing": {"prompt": "0.0000004", "completion": "0.0000004"},
        },
        {
            "id": "openai/gpt-4o-mini",
            "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
        },
    ]
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: object) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/api/v1/key":
            if self.headers.get("Authorization") == f"Bearer {GOOD_KEY}":
                self._send(200, {"data": {"label": "e2e key", "usage": 0.25, "limit": 10}})
            else:
                self._send(401, {"error": {"message": "invalid key"}})
        elif self.path == "/api/v1/models":
            self._send(200, MODELS)
        else:
            self._send(404, {})

    def do_POST(self) -> None:
        if self.path != "/api/v1/chat/completions":
            self._send(404, {})
            return
        if self.headers.get("Authorization") != f"Bearer {GOOD_KEY}":
            self._send(401, {"error": {"message": "invalid key"}})
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        text = body["messages"][-1]["content"]
        if "forbidden" in text or ("hidden-answer" in text and text.startswith("ASSISTANT")):
            verdict = {"allowed": False, "category": "policy"}
        elif "off-topic" in text and text.startswith("USER MESSAGE"):
            verdict = {"allowed": False, "category": "scope"}
        else:
            verdict = {"allowed": True, "category": "ok"}
        self._send(
            200,
            {
                "choices": [{"message": {"content": json.dumps(verdict)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5},
            },
        )

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
