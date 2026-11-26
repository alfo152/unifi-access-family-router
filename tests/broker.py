"""Small MQTT 3.1.1 broker used only by the tests.

Implements CONNECT (with optional user/password check), SUBSCRIBE with
'+'/'#' wildcards, PUBLISH QoS 0/1 routing, retained messages, Last Will on
abnormal disconnect, PINGREQ and DISCONNECT. Every routed message is
recorded in ``self.log`` for assertions.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field


def topic_matches(filt: str, topic: str) -> bool:
    f_parts = filt.split("/")
    t_parts = topic.split("/")
    for index, part in enumerate(f_parts):
        if part == "#":
            return True
        if index >= len(t_parts):
            return False
        if part != "+" and part != t_parts[index]:
            return False
    return len(f_parts) == len(t_parts)


@dataclass
class Logged:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    client_id: str


@dataclass
class Session:
    client_id: str
    writer: asyncio.StreamWriter
    subscriptions: dict[str, int] = field(default_factory=dict)
    will: tuple[str, bytes, int, bool] | None = None
    next_id: int = 0
    clean_disconnect: bool = False


class TestBroker:
    __test__ = False  # not a pytest test class

    def __init__(self, users: dict[str, str] | None = None, port: int = 0) -> None:
        self.users = users
        self.port = port
        self.sessions: dict[str, Session] = {}
        self.retained: dict[str, tuple[bytes, int]] = {}
        self.log: list[Logged] = []
        self.connects: list[tuple[str, str, int]] = []  # client_id, username, keepalive
        self.pings = 0
        self._server: asyncio.base_events.Server | None = None
        self._new_message = asyncio.Event()
        self.drop_pingresp = False

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            for session in list(self.sessions.values()):
                session.writer.close()
            await self._server.wait_closed()
            self._server = None
        self.sessions.clear()

    # ------------------------------------------------------------ helpers

    def messages(self, topic: str) -> list[Logged]:
        return [m for m in self.log if m.topic == topic]

    async def wait_for(self, predicate, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AssertionError("timeout waiting for broker condition")
            self._new_message.clear()
            try:
                await asyncio.wait_for(self._new_message.wait(), min(remaining, 0.05))
            except asyncio.TimeoutError:
                pass

    async def inject(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        """Publish a message as if sent by another client."""

        await self._route(topic, payload, qos, retain, "test-injector")

    # ------------------------------------------------------------ protocol

    @staticmethod
    async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, bytes]:
        first = (await reader.readexactly(1))[0]
        multiplier, length = 1, 0
        while True:
            byte = (await reader.readexactly(1))[0]
            length += (byte & 0x7F) * multiplier
            if not byte & 0x80:
                break
            multiplier *= 128
        body = await reader.readexactly(length) if length else b""
        return first, body

    @staticmethod
    def _encode_len(length: int) -> bytes:
        out = bytearray()
        while True:
            byte = length % 128
            length //= 128
            if length:
                byte |= 0x80
            out.append(byte)
            if not length:
                return bytes(out)

    def _packet(self, first: int, body: bytes) -> bytes:
        return bytes([first]) + self._encode_len(len(body)) + body

    @staticmethod
    def _read_string(body: bytes, offset: int) -> tuple[bytes, int]:
        (length,) = struct.unpack("!H", body[offset : offset + 2])
        return body[offset + 2 : offset + 2 + length], offset + 2 + length

    async def _send_publish(
        self, session: Session, topic: str, payload: bytes, qos: int, retain: bool
    ) -> None:
        body = struct.pack("!H", len(topic.encode())) + topic.encode()
        if qos:
            session.next_id = session.next_id % 65535 + 1
            body += struct.pack("!H", session.next_id)
        first = 0x30 | (qos << 1) | (1 if retain else 0)
        try:
            session.writer.write(self._packet(first, body + payload))
            await session.writer.drain()
        except (ConnectionError, RuntimeError):
            pass

    async def _route(
        self, topic: str, payload: bytes, qos: int, retain: bool, client_id: str
    ) -> None:
        self.log.append(Logged(topic, payload, qos, retain, client_id))
        if retain:
            if payload:
                self.retained[topic] = (payload, qos)
            else:
                self.retained.pop(topic, None)
        for session in list(self.sessions.values()):
            granted = [q for f, q in session.subscriptions.items() if topic_matches(f, topic)]
            if granted:
                await self._send_publish(session, topic, payload, min(qos, max(granted)), False)
        self._new_message.set()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session: Session | None = None
        try:
            first, body = await self._read_packet(reader)
            if first != 0x10:
                writer.close()
                return
            name, off = self._read_string(body, 0)
            assert name == b"MQTT" and body[off] == 4, "protocol must be MQTT 3.1.1"
            flags = body[off + 1]
            (keepalive,) = struct.unpack("!H", body[off + 2 : off + 4])
            off += 4
            client_id_raw, off = self._read_string(body, off)
            client_id = client_id_raw.decode()
            will = None
            if flags & 0x04:
                will_topic, off = self._read_string(body, off)
                will_payload, off = self._read_string(body, off)
                will = (will_topic.decode(), will_payload, (flags >> 3) & 3, bool(flags & 0x20))
            username = password = ""
            if flags & 0x80:
                raw, off = self._read_string(body, off)
                username = raw.decode()
            if flags & 0x40:
                raw, off = self._read_string(body, off)
                password = raw.decode()
            assert flags & 0x02, "client must use clean session"
            if self.users is not None and self.users.get(username) != password:
                writer.write(bytes([0x20, 2, 0, 5]))
                await writer.drain()
                writer.close()
                return
            old = self.sessions.pop(client_id, None)
            if old is not None:
                old.clean_disconnect = True
                old.writer.close()
            session = Session(client_id, writer, will=will)
            self.sessions[client_id] = session
            self.connects.append((client_id, username, keepalive))
            writer.write(bytes([0x20, 2, 0, 0]))
            await writer.drain()

            while True:
                first, body = await self._read_packet(reader)
                kind = first & 0xF0
                if kind == 0x30:
                    qos = (first >> 1) & 3
                    retain = bool(first & 1)
                    topic_raw, off = self._read_string(body, 0)
                    if qos:
                        (pid,) = struct.unpack("!H", body[off : off + 2])
                        off += 2
                        writer.write(bytes([0x40, 2]) + struct.pack("!H", pid))
                        await writer.drain()
                    await self._route(topic_raw.decode(), body[off:], qos, retain, client_id)
                elif kind == 0x80:
                    assert first == 0x82, "SUBSCRIBE flags must be 0010"
                    (pid,) = struct.unpack("!H", body[:2])
                    off = 2
                    codes = bytearray()
                    new_filters: list[str] = []
                    while off < len(body):
                        filt, off = self._read_string(body, off)
                        qos = body[off]
                        off += 1
                        session.subscriptions[filt.decode()] = min(qos, 1)
                        new_filters.append(filt.decode())
                        codes.append(min(qos, 1))
                    writer.write(self._packet(0x90, struct.pack("!H", pid) + bytes(codes)))
                    await writer.drain()
                    for filt in new_filters:
                        for topic, (payload, rqos) in list(self.retained.items()):
                            if topic_matches(filt, topic):
                                await self._send_publish(session, topic, payload, min(rqos, 1), True)
                elif kind == 0x40:
                    pass  # PUBACK from subscriber
                elif kind == 0xC0:
                    self.pings += 1
                    if not self.drop_pingresp:
                        writer.write(bytes([0xD0, 0]))
                        await writer.drain()
                elif kind == 0xE0:
                    session.clean_disconnect = True
                    break
        except (asyncio.IncompleteReadError, ConnectionError, AssertionError):
            pass
        finally:
            if session is not None:
                if self.sessions.get(session.client_id) is session:
                    del self.sessions[session.client_id]
                if not session.clean_disconnect and session.will is not None:
                    topic, payload, qos, retain = session.will
                    await self._route(topic, payload, qos, retain, session.client_id)
            writer.close()

    def disconnect_client(self, client_id: str) -> None:
        """Abruptly close a client connection (Last Will is published)."""

        session = self.sessions.get(client_id)
        if session is not None:
            session.writer.close()
