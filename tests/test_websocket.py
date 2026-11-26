"""WebSocket client tests against the Node 'ws' library over real TLS."""

from __future__ import annotations

import asyncio
import base64
import ssl
import unittest

from family_router.access_api import AccessClient
from family_router.config import AccessConfig
from family_router.tlsutil import TlsFingerprintError, fetch_fingerprint, make_ssl_context
from family_router.websocket import (
    WebSocketClient,
    WebSocketClosed,
    WebSocketHandshakeError,
    _mask,
    accept_key,
    encode_frame,
)

from .support import HAVE_NODE_WS, HAVE_OPENSSL, SelfSignedCert

TOKEN = "family-a-token"


class FrameTests(unittest.TestCase):
    def test_accept_key_rfc6455_example(self) -> None:
        # RFC 6455 section 1.3
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masking_rfc6455_example(self) -> None:
        # RFC 6455 section 5.7: masked "Hello"
        frame = encode_frame(0x1, b"Hello", mask_key=bytes([0x37, 0xFA, 0x21, 0x3D]))
        self.assertEqual(frame, bytes([0x81, 0x85, 0x37, 0xFA, 0x21, 0x3D, 0x7F, 0x9F, 0x4D, 0x51, 0x58]))
        self.assertEqual(_mask(_mask(b"abcdefg", b"1234"), b"1234"), b"abcdefg")
        self.assertEqual(_mask(b"", b"1234"), b"")

    def test_extended_lengths(self) -> None:
        key = b"\x00\x00\x00\x00"
        self.assertEqual(encode_frame(0x2, b"x" * 126, key)[:4], bytes([0x82, 0xFE, 0x00, 0x7E]))
        self.assertEqual(
            encode_frame(0x2, b"x" * 65536, key)[:10],
            bytes([0x82, 0xFF, 0, 0, 0, 0, 0, 1, 0, 0]),
        )


@unittest.skipUnless(HAVE_NODE_WS and HAVE_OPENSSL, "node 'ws' module or openssl not available")
class LiveWebSocketTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cert = SelfSignedCert()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.cert.cleanup()

    async def asyncSetUp(self) -> None:
        from .ws_fake import NodeAccessServer

        self.server = NodeAccessServer(self.cert, [TOKEN])
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.stop()

    def access(self, token: str = TOKEN, fingerprint: str | None = None) -> AccessClient:
        return AccessClient(
            AccessConfig(
                host="127.0.0.1",
                port=self.server.port,
                token=token,
                tls_fingerprint=self.cert.fingerprint if fingerprint is None else fingerprint,
            ),
            heartbeat_interval=0.6,
            heartbeat_timeout=0.6,
            max_backoff=0.2,
        )

    async def connect(self, **kw) -> WebSocketClient:  # type: ignore[no-untyped-def]
        ws = await self.access(**kw).connect_notifications()
        self.assertEqual(await asyncio.wait_for(ws.recv(), 3), "Hello")
        return ws

    async def test_auth_header_and_messages(self) -> None:
        ws = await self.connect()
        event = await self.server.wait_event(lambda e: e["event"] == "connected")
        self.assertEqual(event["token"], TOKEN)
        self.assertTrue(event["ua"].startswith("unifi-access-family-router/"))
        for size in (5, 125, 126, 65535, 65536, 300_000):
            text = "é" * (size // 2) + "x" * (size % 2)
            await self.server.broadcast(text)
            self.assertEqual(await asyncio.wait_for(ws.recv(), 5), text)
        await ws.send_text("from-client")
        await self.server.wait_event(lambda e: e["event"] == "message" and e["data"] == "from-client")
        await ws.close()
        closed = await self.server.wait_event(lambda e: e["event"] == "closed")
        self.assertEqual(closed["code"], 1000)

    async def test_fragmented_and_binary(self) -> None:
        ws = await self.connect()
        await self.server.send(cmd="fragmented", parts=['{"event":', '"x",', '"data":{}}'])
        self.assertEqual(await asyncio.wait_for(ws.recv(), 3), '{"event":"x","data":{}}')
        await self.server.send(cmd="binary", data=base64.b64encode(b"\x00\x01\x02").decode())
        self.assertEqual(await asyncio.wait_for(ws.recv(), 3), b"\x00\x01\x02")
        await ws.close()

    async def test_server_ping_gets_pong(self) -> None:
        ws = await self.connect()
        reader = asyncio.create_task(ws.recv())
        await self.server.send(cmd="ping", data="are-you-there")
        pong = await self.server.wait_event(lambda e: e["event"] == "pong")
        self.assertEqual(pong["data"], "are-you-there")
        reader.cancel()
        await ws.close()

    async def test_server_close_code(self) -> None:
        ws = await self.connect()
        await self.server.send(cmd="close", code=1001, reason="going away")
        with self.assertRaises(WebSocketClosed) as ctx:
            await asyncio.wait_for(ws.recv(), 3)
        self.assertEqual((ctx.exception.code, ctx.exception.reason), (1001, "going away"))
        self.assertTrue(ws.closed)

    async def test_abrupt_termination(self) -> None:
        ws = await self.connect()
        await self.server.send(cmd="terminate")
        with self.assertRaises(WebSocketClosed) as ctx:
            await asyncio.wait_for(ws.recv(), 3)
        self.assertEqual(ctx.exception.code, 1006)

    async def test_wrong_token_is_401(self) -> None:
        with self.assertRaises(WebSocketHandshakeError) as ctx:
            await self.access(token="wrong").connect_notifications()
        self.assertEqual(ctx.exception.status, 401)

    async def test_wrong_path_is_404(self) -> None:
        with self.assertRaises(WebSocketHandshakeError) as ctx:
            await WebSocketClient.connect(
                "127.0.0.1", self.server.port, "/nope",
                headers={"Authorization": f"Bearer {TOKEN}"},
                ssl_context=make_ssl_context(AccessConfig("127.0.0.1", TOKEN, tls_fingerprint=self.cert.fingerprint)),
                fingerprint=self.cert.fingerprint,
            )
        self.assertEqual(ctx.exception.status, 404)

    async def test_fingerprint_pinning(self) -> None:
        self.assertEqual(fetch_fingerprint("127.0.0.1", self.server.port), self.cert.fingerprint)
        with self.assertRaises(TlsFingerprintError):
            await self.access(fingerprint="00" * 32).connect_notifications()
        # nothing reached the WebSocket layer
        self.assertEqual(self.server.count(lambda e: e["event"] == "connected"), 0)

    async def test_ca_verification_fails_for_self_signed(self) -> None:
        client = AccessClient(
            AccessConfig("127.0.0.1", TOKEN, port=self.server.port, verify_ssl=True)
        )
        with self.assertRaises(ssl.SSLCertVerificationError):
            await client.connect_notifications()
        # ...but succeeds when the console certificate is the trusted CA file
        client = AccessClient(
            AccessConfig("127.0.0.1", TOKEN, port=self.server.port, verify_ssl=True, ca_file=self.cert.cert)
        )
        ws = await client.connect_notifications()
        await ws.close()

    async def test_heartbeat_detects_dead_peer(self) -> None:
        ws = await self.connect()
        await self.server.send(cmd="mute", value=True)
        hb = asyncio.create_task(ws.heartbeat(interval=0.4, timeout=0.4))
        with self.assertRaises(WebSocketClosed):
            await asyncio.wait_for(ws.recv(), 5)
        await asyncio.wait_for(hb, 2)
        self.assertGreaterEqual(self.server.count(lambda e: e["event"] == "client_ping"), 1)

    async def test_heartbeat_keeps_live_connection(self) -> None:
        ws = await self.connect()
        hb = asyncio.create_task(ws.heartbeat(interval=0.3, timeout=0.5))
        reader = asyncio.create_task(ws.recv())
        await asyncio.sleep(1.5)
        self.assertFalse(ws.closed)
        self.assertGreaterEqual(self.server.count(lambda e: e["event"] == "client_ping"), 2)
        await self.server.broadcast("still-alive")
        self.assertEqual(await asyncio.wait_for(reader, 2), "still-alive")
        hb.cancel()
        await ws.close()

    async def test_listen_reconnects(self) -> None:
        received: list[str] = []
        states: list[bool] = []

        async def on_message(message: str | bytes) -> None:
            assert isinstance(message, str)
            received.append(message)
            if message == "boom":
                raise RuntimeError("handler failure must not drop the connection")

        task = asyncio.create_task(self.access().listen(on_message, states.append))
        await self.server.wait_event(lambda e: e["event"] == "connected")
        await self.server.broadcast("boom")
        await self.server.broadcast("after-boom")
        for _ in range(50):
            if "after-boom" in received:
                break
            await asyncio.sleep(0.05)
        self.assertIn("after-boom", received)
        self.assertEqual(self.server.count(lambda e: e["event"] == "connected"), 1)

        mark = len(self.server.events)
        await self.server.send(cmd="terminate")
        await self.server.wait_event(lambda e: e["event"] == "connected", start=mark, timeout=5)
        # the server sees the connection slightly before the client callback runs
        for _ in range(100):
            if len(states) >= 3 and received.count("Hello") >= 2:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(states[:3], [True, False, True])
        self.assertEqual(received.count("Hello"), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task


if __name__ == "__main__":
    unittest.main()
