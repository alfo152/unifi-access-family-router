"""Minimal, dependency-free asyncio WebSocket client (RFC 6455).

Only the client side is implemented: text/binary messages, fragmentation,
ping/pong, close handshake, size limits and a heartbeat that detects dead
connections. TLS is provided by :mod:`ssl`; the optional certificate pin is
checked right after the TLS handshake.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import ssl
import struct
import time
from typing import Mapping

from .aiotools import with_timeout
from .tlsutil import check_fingerprint

__all__ = [
    "WebSocketClient",
    "WebSocketClosed",
    "WebSocketError",
    "WebSocketHandshakeError",
]

_LOGGER = logging.getLogger(__name__)
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(Exception):
    """Generic WebSocket failure."""


class WebSocketHandshakeError(WebSocketError):
    """The HTTP upgrade was refused (e.g. 401 for a wrong token)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class WebSocketClosed(WebSocketError):
    """The connection is closed (normally or not)."""

    def __init__(self, code: int, reason: str = "") -> None:
        super().__init__(f"WebSocket closed ({code}{': ' + reason if reason else ''})")
        self.code = code
        self.reason = reason


def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return b""
    length = len(data)
    repeated = (key * (length // 4 + 1))[:length]
    return (int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")).to_bytes(
        length, "big"
    )


def encode_frame(opcode: int, payload: bytes, mask_key: bytes | None = None) -> bytes:
    """Encode a single, final, client-to-server (masked) frame."""

    key = mask_key if mask_key is not None else os.urandom(4)
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(0x80 | length)
    elif length < 1 << 16:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    header += key
    return bytes(header) + _mask(payload, key)


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")


class WebSocketClient:
    """A connected WebSocket. Create it with :meth:`connect`."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        max_size: int = 4 * 1024 * 1024,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._max_size = max_size
        self._write_lock = asyncio.Lock()
        self._close_sent = False
        self._closed: WebSocketClosed | None = None
        self.last_activity = time.monotonic()

    # ------------------------------------------------------------ connect

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        fingerprint: str = "",
        timeout: float = 15.0,
        max_size: int = 4 * 1024 * 1024,
    ) -> "WebSocketClient":
        async def _open() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            return await asyncio.open_connection(
                host,
                port,
                ssl=ssl_context,
                server_hostname=host if ssl_context is not None else None,
                limit=1 << 16,
            )

        reader, writer = await with_timeout(_open(), timeout)
        try:
            if ssl_context is not None:
                check_fingerprint(writer.get_extra_info("ssl_object"), fingerprint)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host_header = f"[{host}]" if ":" in host and not host.startswith("[") else host
            default_port = 443 if ssl_context is not None else 80
            if port != default_port:
                host_header = f"{host_header}:{port}"
            lines = [
                f"GET {path} HTTP/1.1",
                f"Host: {host_header}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                f"Sec-WebSocket-Key: {key}",
                "Sec-WebSocket-Version: 13",
            ]
            for name, value in (headers or {}).items():
                if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
                    raise WebSocketError("invalid header")
                lines.append(f"{name}: {value}")
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            await writer.drain()

            raw = await with_timeout(reader.readuntil(b"\r\n\r\n"), timeout)
            head = raw.decode("latin-1").split("\r\n")
            status_line = head[0].split(" ", 2)
            if len(status_line) < 2 or not status_line[1].isdigit():
                raise WebSocketHandshakeError(0, f"invalid HTTP response: {head[0]!r}")
            status = int(status_line[1])
            response_headers: dict[str, str] = {}
            for line in head[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    response_headers[name.strip().lower()] = value.strip()
            if status != 101:
                raise WebSocketHandshakeError(status, f"upgrade refused: HTTP {status}")
            if response_headers.get("upgrade", "").lower() != "websocket":
                raise WebSocketHandshakeError(status, "missing 'Upgrade: websocket'")
            connection_tokens = {
                t.strip().lower() for t in response_headers.get("connection", "").split(",")
            }
            if "upgrade" not in connection_tokens:
                raise WebSocketHandshakeError(status, "missing 'Connection: Upgrade'")
            if response_headers.get("sec-websocket-accept") != accept_key(key):
                raise WebSocketHandshakeError(status, "invalid Sec-WebSocket-Accept")
        except BaseException:
            writer.close()
            raise
        return cls(reader, writer, max_size=max_size)

    # --------------------------------------------------------------- send

    async def _send(self, opcode: int, payload: bytes) -> None:
        if self._closed is not None:
            raise self._closed
        async with self._write_lock:
            self._writer.write(encode_frame(opcode, payload))
            await self._writer.drain()

    async def send_text(self, text: str) -> None:
        await self._send(OP_TEXT, text.encode("utf-8"))

    async def ping(self, data: bytes = b"") -> None:
        await self._send(OP_PING, data[:125])

    async def close(self, code: int = 1000, reason: str = "", timeout: float = 3.0) -> None:
        """Send a close frame, wait briefly for the reply, then close."""

        if self._closed is None and not self._close_sent:
            self._close_sent = True
            payload = struct.pack("!H", code) + reason.encode("utf-8")[:123]
            try:
                async with self._write_lock:
                    self._writer.write(encode_frame(OP_CLOSE, payload))
                    await self._writer.drain()
                deadline = time.monotonic() + timeout
                while self._closed is None and time.monotonic() < deadline:
                    try:
                        await with_timeout(self.recv(), deadline - time.monotonic())
                    except (WebSocketError, asyncio.TimeoutError, OSError):
                        break
            except (OSError, RuntimeError):
                pass
        await self._abort(WebSocketClosed(code, reason))

    async def _abort(self, closed: WebSocketClosed) -> None:
        if self._closed is None:
            self._closed = closed
        self._writer.close()
        try:
            await with_timeout(self._writer.wait_closed(), 2.0)
        except (asyncio.TimeoutError, OSError, ssl.SSLError, RuntimeError):
            pass

    @property
    def closed(self) -> bool:
        return self._closed is not None

    # ------------------------------------------------------------ receive

    async def _fail(self, code: int, reason: str) -> WebSocketClosed:
        closed = WebSocketClosed(code, reason)
        if not self._close_sent and self._closed is None:
            self._close_sent = True
            try:
                async with self._write_lock:
                    self._writer.write(
                        encode_frame(OP_CLOSE, struct.pack("!H", code) + reason.encode()[:123])
                    )
                    await self._writer.drain()
            except (OSError, RuntimeError):
                pass
        await self._abort(closed)
        return closed

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            b0, b1 = await self._reader.readexactly(2)
            fin = bool(b0 & 0x80)
            if b0 & 0x70:
                raise await self._fail(1002, "reserved bits set")
            opcode = b0 & 0x0F
            if b1 & 0x80:
                raise await self._fail(1002, "server frames must not be masked")
            length = b1 & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", await self._reader.readexactly(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", await self._reader.readexactly(8))
                if length >> 63:
                    raise await self._fail(1002, "invalid frame length")
            if opcode >= 0x8:
                if not fin or length > 125:
                    raise await self._fail(1002, "invalid control frame")
            elif opcode not in (OP_CONT, OP_TEXT, OP_BINARY):
                raise await self._fail(1002, f"unknown opcode {opcode}")
            if length > self._max_size:
                raise await self._fail(1009, "message too big")
            payload = await self._reader.readexactly(length) if length else b""
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, OSError) as exc:
            closed = WebSocketClosed(1006, f"connection lost: {exc.__class__.__name__}")
            await self._abort(closed)
            raise closed from None
        self.last_activity = time.monotonic()
        return fin, opcode, payload

    async def recv(self) -> str | bytes:
        """Return the next complete text (``str``) or binary (``bytes``) message."""

        if self._closed is not None:
            raise self._closed
        message_opcode: int | None = None
        parts: list[bytes] = []
        size = 0
        while True:
            fin, opcode, payload = await self._read_frame()
            if opcode == OP_PING:
                try:
                    await self._send(OP_PONG, payload)
                except (OSError, RuntimeError, WebSocketError):
                    pass
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = 1005
                reason = ""
                if len(payload) >= 2:
                    (code,) = struct.unpack("!H", payload[:2])
                    reason = payload[2:].decode("utf-8", "replace")
                elif len(payload) == 1:
                    raise await self._fail(1002, "invalid close payload")
                if not self._close_sent:
                    self._close_sent = True
                    try:
                        async with self._write_lock:
                            self._writer.write(encode_frame(OP_CLOSE, payload[:2]))
                            await self._writer.drain()
                    except (OSError, RuntimeError):
                        pass
                closed = WebSocketClosed(code, reason)
                await self._abort(closed)
                raise closed
            if opcode == OP_CONT:
                if message_opcode is None:
                    raise await self._fail(1002, "unexpected continuation frame")
            else:
                if message_opcode is not None:
                    raise await self._fail(1002, "expected continuation frame")
                message_opcode = opcode
            size += len(payload)
            if size > self._max_size:
                raise await self._fail(1009, "message too big")
            parts.append(payload)
            if not fin:
                continue
            data = b"".join(parts)
            if message_opcode == OP_TEXT:
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    raise await self._fail(1007, "invalid UTF-8") from None
            return data

    async def heartbeat(self, interval: float = 30.0, timeout: float = 10.0) -> None:
        """Ping when idle; close the connection if the peer stops answering.

        Runs until the connection is closed. Any received frame (including
        pongs) counts as activity.
        """

        while self._closed is None:
            await asyncio.sleep(min(interval, timeout) / 2)
            idle = time.monotonic() - self.last_activity
            if idle >= interval + timeout:
                _LOGGER.warning("No answer from the console for %.0fs, reconnecting", idle)
                await self._abort(WebSocketClosed(1006, "heartbeat timeout"))
                return
            if idle >= interval:
                try:
                    await self.ping(b"hb")
                except (OSError, RuntimeError, WebSocketError):
                    await self._abort(WebSocketClosed(1006, "ping failed"))
                    return
