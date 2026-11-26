"""Shared helpers for the tests."""

from __future__ import annotations

import copy
import json
import os
import shutil
import ssl
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from family_router.config import Config, parse_config

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent

ROOM_A = "WR-e4388386be1d-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ROOM_B = "WR-e4388386be1d-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
ROOM_C = "WR-e4388386be1d-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
MAC = "F4E2C6123456"

BASE: dict[str, Any] = {
    "access": {"host": "192.168.50.1", "token": "token-a"},
    "family": {"name": "Family A", "id": "family_a", "match": {"room_ids": [ROOM_A]}},
    "mqtt": {"host": "127.0.0.1", "port": 1883},
    "homebridge": {"enabled": True, "camera_mac": "F4:E2:C6:12:34:56"},
}


def raw_config(**sections: Any) -> dict[str, Any]:
    """Deep-merge section overrides into BASE (use None to delete a key)."""

    raw = copy.deepcopy(BASE)
    for key, value in sections.items():
        if value is None:
            raw.pop(key, None)
        elif isinstance(value, dict) and isinstance(raw.get(key), dict):
            merged = dict(raw[key])
            for sub_key, sub_value in value.items():
                if sub_value is None:
                    merged.pop(sub_key, None)
                else:
                    merged[sub_key] = sub_value
            raw[key] = merged
        else:
            raw[key] = value
    return raw


def make_config(**sections: Any) -> Config:
    return parse_config(raw_config(**sections), env={})


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def ring_message(room_id: str, request_id: str, device_id: str = "e4388386be1d", **data: Any) -> str:
    payload = load_fixture("remote_view.json")
    payload["data"]["room_id"] = room_id
    payload["data"]["request_id"] = request_id
    payload["data"]["device_id"] = device_id
    payload["data"].update(data)
    return json.dumps(payload)


def end_message(request_id: str, reason_code: int) -> str:
    payload = load_fixture("remote_view_change.json")
    payload["data"]["remote_call_request_id"] = request_id
    payload["data"]["reason_code"] = reason_code
    return json.dumps(payload)


class SelfSignedCert:
    """A temporary self-signed certificate for 127.0.0.1/localhost."""

    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="afr-cert-")
        self.cert = os.path.join(self.dir, "cert.pem")
        self.key = os.path.join(self.dir, "key.pem")
        subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", self.key, "-out", self.cert, "-days", "2",
                "-subj", "/CN=localhost",
                "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
            ],
            check=True,
            capture_output=True,
        )
        der = ssl.PEM_cert_to_DER_cert(Path(self.cert).read_text())
        import hashlib

        self.fingerprint = hashlib.sha256(der).hexdigest().upper()

    def server_context(self) -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.cert, self.key)
        return ctx

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


NODE = shutil.which("node")
WS_MODULE = "/opt/npm-tools/node_modules/ws"
HAVE_NODE_WS = bool(NODE) and os.path.isdir(os.environ.get("WS_MODULE_PATH", WS_MODULE))
HAVE_OPENSSL = bool(shutil.which("openssl"))
