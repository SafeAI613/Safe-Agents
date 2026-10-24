"""Stand-in for ``langgraph dev`` in tests: serves /ok and /assistants/search on a port.

It writes the environment it received to ``env.json`` in its working directory so tests
can check which variables the supervisor passed through.
"""

import json
import os
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
        else:
            self._send(404, {})

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
        if self.path == "/assistants/search":
            config = json.loads(Path(sys.argv[sys.argv.index("--config") + 1]).read_text())
            found = body.get("graph_id") in config["graphs"] and "--no-graphs" not in sys.argv
            self._send(200, [{"graph_id": body["graph_id"]}] if found else [])
        else:
            self._send(404, {})

    def log_message(self, *args: object) -> None:
        pass


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
