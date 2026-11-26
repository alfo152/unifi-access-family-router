from __future__ import annotations

import asyncio
import unittest

from family_router.mqtt import MqttClient, MqttConnectError, MqttError, Will

from .broker import TestBroker


class MqttClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.broker = TestBroker(users={"fam": "pw"})
        await self.broker.start()

    async def asyncTearDown(self) -> None:
        await self.broker.stop()

    def client(self, client_id: str = "c1", **kw) -> MqttClient:  # type: ignore[no-untyped-def]
        kw.setdefault("username", "fam")
        kw.setdefault("password", "pw")
        return MqttClient("127.0.0.1", self.broker.port, client_id=client_id, timeout=3, **kw)

    async def test_bad_credentials(self) -> None:
        client = self.client(password="wrong")
        with self.assertRaises(MqttConnectError) as ctx:
            await client.connect()
        self.assertEqual(ctx.exception.return_code, 5)

    async def test_connection_refused(self) -> None:
        client = MqttClient("127.0.0.1", 1, client_id="x", timeout=2)
        with self.assertRaises(OSError):
            await client.connect()

    async def test_publish_qos0_qos1_and_retain(self) -> None:
        async with self.client() as client:
            await client.publish("a/b", "hello", qos=0)
            await client.publish("a/c", b"\x00\x01", qos=1, retain=True)
            await self.broker.wait_for(lambda: len(self.broker.log) == 2)
        first, second = self.broker.log
        self.assertEqual((first.topic, first.payload, first.qos, first.retain), ("a/b", b"hello", 0, False))
        self.assertEqual((second.topic, second.payload, second.qos, second.retain), ("a/c", b"\x00\x01", 1, True))
        self.assertIn("a/c", self.broker.retained)

    async def test_subscribe_and_receive(self) -> None:
        async with self.client("sub") as sub, self.client("pub") as pub:
            await pub.publish("cmd/old", "retained-value", qos=1, retain=True)
            granted = await sub.subscribe("cmd/#", qos=1)
            self.assertEqual(granted, 1)
            messages = sub.messages()
            retained = await asyncio.wait_for(messages.__anext__(), 3)
            self.assertEqual((retained.topic, retained.payload, retained.retain), ("cmd/old", b"retained-value", True))
            await pub.publish("cmd/unlock", "PRESS", qos=1)
            live = await asyncio.wait_for(messages.__anext__(), 3)
            self.assertEqual((live.topic, live.payload, live.qos, live.retain), ("cmd/unlock", b"PRESS", 1, False))
            await self.broker.inject("cmd/q0", b"zero", qos=0)
            zero = await asyncio.wait_for(messages.__anext__(), 3)
            self.assertEqual((zero.payload, zero.qos), (b"zero", 0))

    async def test_will_on_abrupt_disconnect_only(self) -> None:
        will = Will("fam/status", b"offline", qos=1, retain=True)
        client = self.client("w1", will=will)
        await client.connect()
        await client.disconnect()  # clean: no will
        await asyncio.sleep(0.1)
        self.assertEqual(self.broker.messages("fam/status"), [])

        client = self.client("w2", will=will)
        await client.connect()
        self.broker.disconnect_client("w2")
        await self.broker.wait_for(lambda: self.broker.messages("fam/status"))
        logged = self.broker.messages("fam/status")[0]
        self.assertEqual((logged.payload, logged.retain), (b"offline", True))
        with self.assertRaises(MqttError):
            await asyncio.wait_for(client.wait_closed(), 3)
        with self.assertRaises(MqttError):
            await client.publish("x", "y")

    async def test_messages_iterator_raises_on_loss(self) -> None:
        client = self.client("it")
        await client.connect()
        await client.subscribe("t", 1)

        async def consume() -> None:
            async for _ in client.messages():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await self.broker.stop()
        with self.assertRaises(MqttError):
            await asyncio.wait_for(task, 3)

    async def test_keepalive_pings_and_dead_broker(self) -> None:
        async with self.client("ka", keepalive=2) as client:
            await asyncio.sleep(2.6)
            self.assertGreaterEqual(self.broker.pings, 1)
            self.broker.drop_pingresp = True
            with self.assertRaisesRegex(MqttError, "stopped responding"):
                await asyncio.wait_for(client.wait_closed(), 8)

    async def test_publish_timeout_without_puback(self) -> None:
        # A broker that accepts the connection but never answers PUBACK.
        async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1024)
            writer.write(bytes([0x20, 2, 0, 0]))
            await writer.drain()
            await asyncio.sleep(10)

        server = await asyncio.start_server(silent, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = MqttClient("127.0.0.1", port, client_id="t", timeout=0.5, keepalive=0)
        await client.connect()
        with self.assertRaises(asyncio.TimeoutError):
            await client.publish("a", "b", qos=1)
        await client.disconnect()
        server.close()

    async def test_bad_connack(self) -> None:
        async def wrong(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1024)
            writer.write(bytes([0xD0, 0]))  # PINGRESP instead of CONNACK
            await writer.drain()

        server = await asyncio.start_server(wrong, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        with self.assertRaisesRegex(MqttError, "expected CONNACK"):
            await MqttClient("127.0.0.1", port, client_id="t", timeout=1).connect()
        server.close()

    async def test_client_id_reuse_takes_over(self) -> None:
        first = self.client("same")
        await first.connect()
        second = self.client("same")
        await second.connect()
        with self.assertRaises(MqttError):
            await asyncio.wait_for(first.wait_closed(), 3)
        await second.publish("still/ok", "1", qos=1)
        await second.disconnect()


if __name__ == "__main__":
    unittest.main()
