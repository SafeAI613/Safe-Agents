"""Minimal stand-in for the OpenRouter API endpoints the manager calls (E2E tests only)."""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

GOOD_KEY = "sk-or-e2e-good"
MODELS = {
    "data": [
        {
            "id": "meta-llama/llama-3.1-70b-instruct",
            "pricing": {"prompt": "0.0000004", "completion": "0.0000004"},
        }
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

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
