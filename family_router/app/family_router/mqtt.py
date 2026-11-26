"""Minimal, dependency-free asyncio MQTT 3.1.1 client.

Supports what the router needs: CONNECT with credentials and Last Will,
PUBLISH (QoS 0/1, retain), SUBSCRIBE, incoming PUBLISH (QoS 0/1/2),
keep-alive pings and clean DISCONNECT. Clean session is always used.
Reference: OASIS MQTT Version 3.1.1 (2014).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import struct
import time
from dataclasses import dataclass
from typing import AsyncIterator

from .aiotools import with_timeout

__all__ = [
    "Message",
    "MqttClient",
    "MqttConnectError",
    "MqttError",
    "Will",
    "decode_remaining_length",
    "encode_remaining_length",
]

_LOGGER = logging.getLogger(__name__)

CONNECT = 0x10
CONNACK = 0x20
PUBLISH = 0x30
PUBACK = 0x40
PUBREC = 0x50
PUBREL = 0x62
PUBCOMP = 0x70
SUBSCRIBE = 0x82
SUBACK = 0x90
PINGREQ = 0xC0
PINGRESP = 0xD0
DISCONNECT = 0xE0

_MAX_INCOMING = 1024 * 1024

CONNACK_ERRORS = {
    1: "unacceptable protocol version",
    2: "client identifier rejected",
    3: "server unavailable",
    4: "bad user name or password",
    5: "not authorized",
}


class MqttError(Exception):
    """MQTT protocol or connection error."""


class MqttConnectError(MqttError):
    """The broker refused the connection (CONNACK return code != 0)."""

    def __init__(self, return_code: int) -> None:
        super().__init__(
            f"broker refused connection: {CONNACK_ERRORS.get(return_code, return_code)}"
        )
        self.return_code = return_code


@dataclass(frozen=True)
class Will:
    topic: str
    payload: bytes
    qos: int = 1
    retain: bool = True


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes
    qos: int
    retain: bool


# --------------------------------------------------------------------- codec


def encode_remaining_length(length: int) -> bytes:
    if not 0 <= length <= 268_435_455:
        raise MqttError("packet too large")
    out = bytearray()
    while True:
        byte = length % 128
        length //= 128
        if length:
            byte |= 0x80
        out.append(byte)
        if not length:
            return bytes(out)


def decode_remaining_length(data: bytes) -> tuple[int, int]:
    """Return ``(value, bytes_used)`` for an encoded remaining length."""

    multiplier = 1
    value = 0
    for index, byte in enumerate(data[:4]):
        value += (byte & 0x7F) * multiplier
        if not byte & 0x80:
            return value, index + 1
        multiplier *= 128
    raise MqttError("malformed remaining length")


def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > 65535:
        raise MqttError("string too long")
    return struct.pack("!H", len(raw)) + raw


def _binary(data: bytes) -> bytes:
    if len(data) > 65535:
        raise MqttError("binary field too long")
    return struct.pack("!H", len(data)) + data


def _packet(first_byte: int, body: bytes) -> bytes:
    return bytes([first_byte]) + encode_remaining_length(len(body)) + body


def _check_topic_name(topic: str) -> None:
    if not topic or "+" in topic or "#" in topic or "\x00" in topic:
        raise MqttError(f"invalid topic name: {topic!r}")


def encode_connect(
    client_id: str,
    keepalive: int,
    username: str = "",
    password: str = "",
    will: Will | None = None,
) -> bytes:
    flags = 0x02  # clean session
    payload = _string(client_id)
    if will is not None:
        _check_topic_name(will.topic)
        if will.qos not in (0, 1, 2):
            raise MqttError("invalid will QoS")
        flags |= 0x04 | (will.qos << 3) | (0x20 if will.retain else 0)
        payload += _string(will.topic) + _binary(will.payload)
    if username:
        flags |= 0x80
        payload += _string(username)
        if password:
            flags |= 0x40
            payload += _binary(password.encode("utf-8"))
    elif password:
        raise MqttError("password requires a user name in MQTT 3.1.1")
    variable = _string("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
    return _packet(CONNECT, variable + payload)


def encode_publish(
    topic: str, payload: bytes, qos: int = 0, retain: bool = False, packet_id: int = 0
) -> bytes:
    _check_topic_name(topic)
    if qos not in (0, 1):
        raise MqttError("only QoS 0 and 1 are supported for publishing")
    first = PUBLISH | (qos << 1) | (1 if retain else 0)
    body = _string(topic)
    if qos:
        body += struct.pack("!H", packet_id)
    return _packet(first, body + payload)


def encode_subscribe(packet_id: int, topic_filter: str, qos: int) -> bytes:
    if not topic_filter or "\x00" in topic_filter:
        raise MqttError("invalid topic filter")
    return _packet(SUBSCRIBE, struct.pack("!H", packet_id) + _string(topic_filter) + bytes([qos]))


# -------------------------------------------------------------------- client


class MqttClient:
    """One MQTT connection. Not reusable: create a new client to reconnect."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        client_id: str,
        username: str = "",
        password: str = "",
        keepalive: int = 30,
        will: Will | None = None,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._username = username
        self._password = password
        self._keepalive = keepalive
        self._will = will
        self._ssl = ssl_context
        self._timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[bytes]] = {}
        self._next_id = 0
        self._incoming: asyncio.Queue[Message | None] = asyncio.Queue()
        self._qos2_received: set[int] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._error: BaseException | None = None
        self._closed = asyncio.Event()
        self._last_sent = time.monotonic()
        self._last_received = time.monotonic()
        self._connected = False

    # ------------------------------------------------------------ lifecycle

    async def connect(self) -> None:
        async def _open() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            return await asyncio.open_connection(
                self._host,
                self._port,
                ssl=self._ssl,
                server_hostname=self._host if self._ssl is not None else None,
            )

        reader, writer = await with_timeout(_open(), self._timeout)
        self._reader, self._writer = reader, writer
        try:
            writer.write(
                encode_connect(
                    self._client_id, self._keepalive, self._username, self._password, self._will
                )
            )
            await writer.drain()
            first, body = await with_timeout(self._read_packet(), self._timeout)
            if first != CONNACK or len(body) != 2:
                raise MqttError("expected CONNACK")
            if body[1] != 0:
                raise MqttConnectError(body[1])
        except BaseException:
            writer.close()
            raise
        self._connected = True
        self._last_received = self._last_sent = time.monotonic()
        self._tasks = [
            asyncio.create_task(self._reader_loop(), name="mqtt-reader"),
            asyncio.create_task(self._keepalive_loop(), name="mqtt-keepalive"),
        ]

    async def disconnect(self) -> None:
        """Clean disconnect (the Last Will is NOT published)."""

        if self._writer is not None and self._error is None and self._connected:
            with contextlib.suppress(OSError, RuntimeError, MqttError):
                await self._write(bytes([DISCONNECT, 0]))
        await self._shutdown(MqttError("disconnected"))

    async def wait_closed(self) -> None:
        """Wait until the connection is lost, then raise the reason."""

        await self._closed.wait()
        if self._error is not None:
            raise self._error

    async def __aenter__(self) -> "MqttClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # ---------------------------------------------------------------- api

    async def publish(
        self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False
    ) -> None:
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        if qos == 0:
            await self._write(encode_publish(topic, data, 0, retain))
            return
        packet_id, future = self._new_request()
        try:
            await self._write(encode_publish(topic, data, qos, retain, packet_id))
            await with_timeout(future, self._timeout)
        finally:
            self._pending.pop(packet_id, None)

    async def subscribe(self, topic_filter: str, qos: int = 1) -> int:
        packet_id, future = self._new_request()
        try:
            await self._write(encode_subscribe(packet_id, topic_filter, qos))
            body = await with_timeout(future, self._timeout)
        finally:
            self._pending.pop(packet_id, None)
        granted = body[0] if body else 0x80
        if granted == 0x80:
            raise MqttError(f"subscription to {topic_filter!r} refused by broker")
        return granted

    async def messages(self) -> AsyncIterator[Message]:
        """Yield incoming messages; raise when the connection is lost."""

        while True:
            item = await self._incoming.get()
            if item is None:
                raise self._error or MqttError("connection closed")
            yield item

    # ------------------------------------------------------------ internal

    def _new_request(self) -> tuple[int, asyncio.Future[bytes]]:
        if self._error is not None:
            raise self._error
        for _ in range(65535):
            self._next_id = self._next_id % 65535 + 1
            if self._next_id not in self._pending:
                future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()
                self._pending[self._next_id] = future
                return self._next_id, future
        raise MqttError("no free packet identifier")

    async def _write(self, data: bytes) -> None:
        if self._error is not None:
            raise self._error
        if self._writer is None:
            raise MqttError("not connected")
        try:
            async with self._write_lock:
                self._writer.write(data)
                await self._writer.drain()
        except (OSError, RuntimeError) as exc:
            error = MqttError(f"connection lost while writing: {exc}")
            await self._shutdown(error)
            raise error from exc
        self._last_sent = time.monotonic()

    async def _read_packet(self) -> tuple[int, bytes]:
        assert self._reader is not None
        first = (await self._reader.readexactly(1))[0]
        encoded = bytearray()
        while True:
            byte = (await self._reader.readexactly(1))[0]
            encoded.append(byte)
            if not byte & 0x80:
                break
            if len(encoded) >= 4:
                raise MqttError("malformed remaining length")
        length, _ = decode_remaining_length(bytes(encoded))
        if length > _MAX_INCOMING:
            raise MqttError("incoming packet too large")
        body = await self._reader.readexactly(length) if length else b""
        self._last_received = time.monotonic()
        return first, body

    async def _reader_loop(self) -> None:
        try:
            while True:
                first, body = await self._read_packet()
                kind = first & 0xF0
                if kind == PUBLISH:
                    await self._on_publish(first, body)
                elif kind in (PUBACK, SUBACK):
                    if len(body) < 2:
                        raise MqttError("malformed acknowledgement")
                    (packet_id,) = struct.unpack("!H", body[:2])
                    future = self._pending.get(packet_id)
                    if future is not None and not future.done():
                        future.set_result(body[2:])
                elif kind == (PUBREL & 0xF0):
                    if len(body) < 2:
                        raise MqttError("malformed PUBREL")
                    (packet_id,) = struct.unpack("!H", body[:2])
                    self._qos2_received.discard(packet_id)
                    await self._write(bytes([PUBCOMP, 2]) + body[:2])
                elif kind == PINGRESP:
                    pass
                else:
                    _LOGGER.debug("Ignoring MQTT packet type 0x%02x", first)
        except asyncio.CancelledError:
            raise
        except (asyncio.IncompleteReadError, OSError, ssl.SSLError) as exc:
            await self._shutdown(MqttError(f"connection lost: {exc.__class__.__name__}"))
        except MqttError as exc:
            await self._shutdown(exc)

    async def _on_publish(self, first: int, body: bytes) -> None:
        qos = (first >> 1) & 0x03
        retain = bool(first & 0x01)
        if qos == 3 or len(body) < 2:
            raise MqttError("malformed PUBLISH")
        (topic_len,) = struct.unpack("!H", body[:2])
        offset = 2 + topic_len
        if len(body) < offset + (2 if qos else 0):
            raise MqttError("malformed PUBLISH")
        topic = body[2:offset].decode("utf-8", "replace")
        packet_id = 0
        if qos:
            (packet_id,) = struct.unpack("!H", body[offset : offset + 2])
            offset += 2
        payload = body[offset:]
        if qos == 1:
            await self._write(bytes([PUBACK, 2]) + struct.pack("!H", packet_id))
        elif qos == 2:
            await self._write(bytes([PUBREC, 2]) + struct.pack("!H", packet_id))
            if packet_id in self._qos2_received:
                return  # duplicate delivery
            self._qos2_received.add(packet_id)
        self._incoming.put_nowait(Message(topic, payload, qos, retain))

    async def _keepalive_loop(self) -> None:
        if self._keepalive <= 0:
            return
        interval = max(1.0, self._keepalive / 2)
        try:
            while True:
                await asyncio.sleep(min(interval, 5.0))
                now = time.monotonic()
                if now - self._last_received > self._keepalive * 1.5:
                    await self._shutdown(MqttError("broker stopped responding"))
                    return
                if now - self._last_sent >= interval:
                    await self._write(bytes([PINGREQ, 0]))
        except asyncio.CancelledError:
            raise
        except MqttError:
            return

    async def _shutdown(self, error: BaseException) -> None:
        if self._closed.is_set():
            return
        self._error = error
        self._closed.set()
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        self._incoming.put_nowait(None)
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(asyncio.TimeoutError, OSError, ssl.SSLError, RuntimeError):
                await with_timeout(self._writer.wait_closed(), 2.0)
