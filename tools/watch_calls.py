#!/usr/bin/env python3
"""Show which directory entry (room_id) each UniFi Access intercom call uses.

A standalone, read-only check: no Home Assistant, no Homebridge, no MQTT and
nothing to install besides Python 3.8 or newer. It connects to the official
UniFi Access notifications WebSocket, prints every intercom call with its
room_id and, when you stop it, tells you whether different directory entries
produced different room_ids, which is what per-family routing relies on.

It only listens: the API token needs just the "view:device" permission, which
cannot unlock doors, and the script sends nothing to the console except the
WebSocket keep-alive.

Usage:
    python3 watch_calls.py CONSOLE_IP[:PORT] [--duration SECONDS] [--raw]

The API token is read from the UNIFI_ACCESS_TOKEN environment variable or
asked for (hidden input). Call each directory entry once from the intercom,
then press Ctrl+C to see the summary.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import ipaddress
import json
import os
import socket
import ssl
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0"
NOTIFY_PATH = "/api/v1/developer/devices/notifications"
DEFAULT_PORT = 12445
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1 << 20
MAX_HEADER = 16 * 1024
PING_INTERVAL = 20.0
REASONS = {
    105: "timed out",
    106: "rejected",
    107: "door unlocked",
    108: "cancelled",
    400: "answered elsewhere",
}
REDACTED_KEYS = {"token", "channel"}

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class HandshakeError(Exception):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


class Closed(Exception):
    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(f"connection closed ({code}{': ' + reason if reason else ''})")
        self.code = code
        self.reason = reason


def parse_target(text: str) -> Tuple[str, int]:
    """Split "host", "host:port", "[ipv6]:port" or a bare IPv6 address."""
    text = text.strip()
    if not text:
        raise ValueError("empty console address")
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise ValueError(f"invalid address: {text}")
        host, rest = text[1:end], text[end + 1:]
        if rest and not rest.startswith(":"):
            raise ValueError(f"invalid address: {text}")
        port_text = rest[1:]
    elif text.count(":") == 1:
        host, port_text = text.split(":")
    else:
        host, port_text = text, ""
    if not host:
        raise ValueError(f"invalid address: {text}")
    port = int(port_text) if port_text else DEFAULT_PORT
    if not 0 < port < 65536:
        raise ValueError(f"invalid port: {port}")
    return host, port


def normalize_fingerprint(text: str) -> str:
    value = text.strip()
    if "=" in value:
        value = value.rsplit("=", 1)[1]
    value = value.replace(":", "").replace(" ", "").lower()
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("the fingerprint must be a SHA-256 value (64 hex digits)")
    return value


def format_fingerprint(hex_value: str) -> str:
    return ":".join(hex_value[i:i + 2] for i in range(0, len(hex_value), 2)).upper()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("<redacted>" if k in REDACTED_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class Connection:
    """Minimal RFC 6455 client: enough to read the Access notifications."""

    def __init__(self, sock: ssl.SSLSocket, leftover: bytes) -> None:
        self.sock = sock
        self.buf = bytearray(leftover)
        self._frag_op: Optional[int] = None
        self._frag = bytearray()

    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        header = bytearray([0x80 | opcode])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", size)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _parse_frame(self) -> Optional[Tuple[bool, int, bytes]]:
        buf = self.buf
        if len(buf) < 2:
            return None
        fin = bool(buf[0] & 0x80)
        opcode = buf[0] & 0x0F
        masked = bool(buf[1] & 0x80)
        size = buf[1] & 0x7F
        pos = 2
        if size == 126:
            if len(buf) < 4:
                return None
            size = struct.unpack("!H", bytes(buf[2:4]))[0]
            pos = 4
        elif size == 127:
            if len(buf) < 10:
                return None
            size = struct.unpack("!Q", bytes(buf[2:10]))[0]
            pos = 10
        if size > MAX_MESSAGE:
            raise Closed(1009, "message too big")
        mask = b""
        if masked:
            if len(buf) < pos + 4:
                return None
            mask = bytes(buf[pos:pos + 4])
            pos += 4
        if len(buf) < pos + size:
            return None
        payload = bytes(buf[pos:pos + size])
        del buf[:pos + size]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def recv_message(self) -> Tuple[int, bytes]:
        """Return the next data message; raises socket.timeout with no data."""
        while True:
            frame = self._parse_frame()
            if frame is None:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise Closed(1006, "connection lost")
                self.buf += chunk
                continue
            fin, opcode, payload = frame
            if opcode == OP_PING:
                self.send_frame(OP_PONG, payload)
            elif opcode == OP_PONG:
                pass
            elif opcode == OP_CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                reason = payload[2:].decode("utf-8", "replace")
                try:
                    self.send_frame(OP_CLOSE, payload[:2])
                except OSError:
                    pass
                raise Closed(code, reason)
            elif opcode in (OP_TEXT, OP_BINARY):
                if self._frag_op is not None:
                    raise Closed(1002, "new message inside a fragmented one")
                if fin:
                    return opcode, payload
                self._frag_op, self._frag = opcode, bytearray(payload)
            elif opcode == OP_CONT:
                if self._frag_op is None:
                    raise Closed(1002, "unexpected continuation frame")
                self._frag += payload
                if len(self._frag) > MAX_MESSAGE:
                    raise Closed(1009, "message too big")
                if fin:
                    opcode, data = self._frag_op, bytes(self._frag)
                    self._frag_op, self._frag = None, bytearray()
                    return opcode, data
            else:
                raise Closed(1002, f"unknown opcode {opcode}")

    def close(self) -> None:
        try:
            self.send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def connect(host: str, port: int, token: str, timeout: float = 10.0) -> Tuple[Connection, str]:
    raw = socket.create_connection((host, port), timeout=timeout)
    context = ssl.create_default_context()
    # UniFi consoles use a self-signed certificate: the fingerprint is printed
    # so it can be compared, and --fingerprint enforces it.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        sock = context.wrap_socket(raw, server_hostname=None if _is_ip(host) else host)
    except Exception:
        raw.close()
        raise
    der = sock.getpeercert(binary_form=True) or b""
    fingerprint = hashlib.sha256(der).hexdigest()
    key = base64.b64encode(os.urandom(16)).decode()
    host_header = f"[{host}]" if ":" in host else host
    request = (
        f"GET {NOTIFY_PATH} HTTP/1.1\r\n"
        f"Host: {host_header}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Authorization: Bearer {token}\r\n"
        f"User-Agent: watch-calls/{VERSION}\r\n"
        "\r\n"
    )
    try:
        sock.sendall(request.encode())
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise HandshakeError(0, "the console closed the connection")
            data += chunk
            if len(data) > MAX_HEADER:
                raise HandshakeError(0, "response header too large")
        head, _, leftover = bytes(data).partition(b"\r\n\r\n")
        lines = head.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if status != 101:
            raise HandshakeError(status, parts[2] if len(parts) > 2 else "")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        if headers.get("sec-websocket-accept") != accept_key(key):
            raise HandshakeError(status, "invalid Sec-WebSocket-Accept")
    except Exception:
        sock.close()
        raise
    return Connection(sock, leftover), fingerprint


class Recorder:
    """Keeps the calls seen and prints them as they arrive."""

    def __init__(self, raw: bool = False, out: Any = None) -> None:
        self.raw = raw
        self.out = out or sys.stdout
        self.calls: List[Dict[str, Any]] = []

    def _print(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    def handle(self, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            if self.raw:
                self._print(f"[raw] {text}")
            return
        if not isinstance(message, dict):
            return
        if self.raw:
            self._print("[raw] " + json.dumps(redact(message), ensure_ascii=False))
        event = message.get("event")
        data = message.get("data") if isinstance(message.get("data"), dict) else {}
        stamp = time.strftime("%H:%M:%S")
        if event == "access.remote_view":
            room_id = str(data.get("room_id") or "(missing)")
            call = {
                "room_id": room_id,
                "request_id": str(data.get("request_id") or ""),
                "device": str(data.get("device_name") or data.get("device_id") or "?"),
                "door": str(data.get("door_name") or "?"),
                "guards": len(data.get("door_guard_ids") or []),
            }
            self.calls.append(call)
            self._print(
                f"[{stamp}] CALL  room_id={room_id}  device={call['device']}  "
                f"door={call['door']}  receivers={call['guards']}"
            )
        elif event == "access.remote_view.change":
            code = data.get("reason_code")
            reason = REASONS.get(code, "unknown") if isinstance(code, int) else "unknown"
            request = str(data.get("remote_call_request_id") or "")
            room = next((c["room_id"] for c in self.calls if c["request_id"] == request), "?")
            self._print(f"[{stamp}] END   room_id={room}  reason={reason} ({code})")

    def summary(self) -> str:
        rooms: Dict[str, List[Dict[str, Any]]] = {}
        for call in self.calls:
            rooms.setdefault(call["room_id"], []).append(call)
        lines = ["", "=== Summary ===",
                 f"Calls seen: {len(self.calls)}, different room_id: {len(rooms)}"]
        for room_id, calls in rooms.items():
            first = calls[0]
            lines.append(f"  {room_id}: {len(calls)} call(s) (device {first['device']}, "
                         f"door {first['door']}, receivers {first['guards']})")
        if not self.calls:
            verdict = ("No calls seen. Check that the token has the view:device permission "
                       "and call a directory entry from the intercom.")
        elif len(self.calls) == 1:
            verdict = "Only one call seen: call at least two different directory entries."
        elif len(rooms) >= 2:
            verdict = ("OK: different calls produced different room_ids. If each one was a "
                       "different directory entry, per-family routing by room_id works here.")
        else:
            verdict = ("All calls used the same room_id. If you called different directory "
                       "entries, routing by room_id does NOT work on this setup: please report it.")
        lines += [f"Result: {verdict}",
                  "You can share this summary: it contains no token or password."]
        return "\n".join(lines)


def run(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show the room_id of each UniFi Access intercom call (read-only).")
    parser.add_argument("console", help="IP or name of the UniFi console, optionally :PORT "
                        f"(default {DEFAULT_PORT})")
    parser.add_argument("--duration", type=float, default=0,
                        help="stop by itself after this many seconds (default: until Ctrl+C)")
    parser.add_argument("--fingerprint", help="SHA-256 fingerprint the console certificate "
                        "must have (printed at the first run)")
    parser.add_argument("--raw", action="store_true",
                        help="also print every notification (call tokens redacted)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)

    try:
        host, port = parse_target(args.console)
        expected = normalize_fingerprint(args.fingerprint) if args.fingerprint else None
    except ValueError as err:
        parser.error(str(err))
    if args.duration < 0:
        parser.error("--duration must be positive")

    token = os.environ.get("UNIFI_ACCESS_TOKEN", "").strip()
    if not token:
        try:
            token = getpass.getpass("UniFi Access API token (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNo token given.", file=sys.stderr)
            return 2
    if not token:
        print("No token given.", file=sys.stderr)
        return 2

    print(f"Connecting to {host}:{port} ...", flush=True)
    try:
        conn, fingerprint = connect(host, port, token)
    except HandshakeError as err:
        if err.status in (401, 403):
            print(f"Refused by the console ({err}): the token is wrong or lacks the "
                  "view:device permission.", file=sys.stderr)
            return 2
        print(f"WebSocket handshake failed: {err}", file=sys.stderr)
        return 1
    except (OSError, ssl.SSLError) as err:
        print(f"Cannot connect to {host}:{port}: {err}", file=sys.stderr)
        return 1

    print(f"Certificate SHA-256: {format_fingerprint(fingerprint)}", flush=True)
    if expected and fingerprint != expected:
        print("The certificate does not match --fingerprint: stopping.", file=sys.stderr)
        conn.close()
        return 1
    print("Connected. Call each directory entry once from the intercom.", flush=True)
    print("Press Ctrl+C when done." if not args.duration
          else f"Stopping by itself in {args.duration:g} s.", flush=True)

    recorder = Recorder(raw=args.raw)
    conn.sock.settimeout(1.0)
    deadline = time.monotonic() + args.duration if args.duration else None
    last_ping = time.monotonic()
    status = 0
    try:
        while deadline is None or time.monotonic() < deadline:
            if time.monotonic() - last_ping >= PING_INTERVAL:
                conn.send_frame(OP_PING, b"watch-calls")
                last_ping = time.monotonic()
            try:
                opcode, payload = conn.recv_message()
            except socket.timeout:
                continue
            if opcode == OP_TEXT:
                recorder.handle(payload.decode("utf-8", "replace"))
    except KeyboardInterrupt:
        pass
    except Closed as err:
        print(f"The console closed the connection: {err}", file=sys.stderr)
        status = 1
    except (OSError, ssl.SSLError) as err:
        print(f"Connection error: {err}", file=sys.stderr)
        status = 1
    finally:
        conn.close()
    print(recorder.summary(), flush=True)
    return status


if __name__ == "__main__":
    sys.exit(run())
