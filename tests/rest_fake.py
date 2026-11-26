"""HTTPS double of the Access REST endpoints (doors list + unlock)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .support import SelfSignedCert

DOORS = [
    {
        "door_lock_relay_status": "lock",
        "door_position_status": "close",
        "floor_id": "23c5db06-b59b-494d-94f1-23e88fbe4909",
        "full_name": "UCG-Max - GF - Portone",
        "id": "0ed545f8-2fcd-4839-9021-b39e707f6aa9",
        "is_bind_hub": True,
        "name": "Portone",
        "type": "door",
    }
]


class RestFake:
    def __init__(self, cert: SelfSignedCert, tokens: dict[str, set[str]]) -> None:
        """``tokens`` maps a token to its permission scopes."""

        self.cert = cert
        self.tokens = tokens
        self.requests: list[dict[str, Any]] = []
        self.fail_unlock_with: tuple[int, dict[str, Any]] | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence
                pass

            def _reply(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                record = {
                    "method": method,
                    "path": self.path,
                    "token": token,
                    "content_type": self.headers.get("Content-Type"),
                    "body": json.loads(raw) if raw else None,
                }
                fake.requests.append(record)
                scopes = fake.tokens.get(token)
                if scopes is None:
                    self._reply(401, {"code": "CODE_UNAUTHORIZED", "msg": "unauthorized"})
                    return
                if method == "GET" and self.path == "/api/v1/developer/doors":
                    if "view:space" not in scopes:
                        self._reply(403, {"code": "CODE_FORBIDDEN", "msg": "forbidden"})
                        return
                    self._reply(200, {"code": "SUCCESS", "data": DOORS, "msg": "success"})
                    return
                prefix = "/api/v1/developer/doors/"
                if method == "PUT" and self.path.startswith(prefix) and self.path.endswith("/unlock"):
                    if "edit:space" not in scopes:
                        self._reply(403, {"code": "CODE_FORBIDDEN", "msg": "forbidden"})
                        return
                    if fake.fail_unlock_with is not None:
                        status, payload = fake.fail_unlock_with
                        self._reply(status, payload)
                        return
                    door_id = self.path[len(prefix) : -len("/unlock")]
                    if door_id not in {d["id"] for d in DOORS}:
                        self._reply(200, {"code": "CODE_DEVICE_NOT_FOUND", "msg": "door not found"})
                        return
                    self._reply(200, {"code": "SUCCESS", "data": "success", "msg": "success"})
                    return
                self._reply(404, {"code": "CODE_NOT_FOUND", "msg": "not found"})

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_PUT(self) -> None:  # noqa: N802
                self._handle("PUT")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.socket = self.cert.server_context().wrap_socket(
            self._server.socket, server_side=True
        )
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def unlocks(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "PUT"]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
