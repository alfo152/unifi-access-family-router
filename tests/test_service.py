"""Service orchestration tests with in-memory doubles (fast, deterministic)."""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, AsyncIterator, Callable

from family_router.mqtt import Message, MqttError, Will
from family_router.service import STALE_RING_SECONDS, Service

from .support import MAC, ROOM_A, ROOM_B, end_message, make_config, ring_message


class FakeClock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


class FakeAccess:
    def __init__(self) -> None:
        self.on_message: Callable[[str | bytes], Any] | None = None
        self.unlocks: list[tuple[str, str, str]] = []
        self.unlock_error: Exception | None = None
        self.ready = asyncio.Event()

    async def listen(self, on_message, on_state=None) -> None:  # type: ignore[no-untyped-def]
        self.on_message = on_message
        if on_state:
            on_state(True)
        self.ready.set()
        await asyncio.Event().wait()

    async def emit(self, raw: str) -> None:
        await self.ready.wait()
        assert self.on_message is not None
        await self.on_message(raw)

    async def unlock(self, door_id: str, actor_id: str, actor_name: str) -> None:
        self.unlocks.append((door_id, actor_id, actor_name))
        if self.unlock_error:
            raise self.unlock_error


class FakeMqtt:
    instances: list["FakeMqtt"] = []
    fail_connects = 0

    def __init__(self, will: Will) -> None:
        self.will = will
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscriptions: list[str] = []
        self.incoming: asyncio.Queue[Message | None] = asyncio.Queue()
        self.closed = asyncio.Event()
        self.disconnected = False
        self.fail_publish = False
        FakeMqtt.instances.append(self)

    async def connect(self) -> None:
        if FakeMqtt.fail_connects > 0:
            FakeMqtt.fail_connects -= 1
            raise ConnectionRefusedError("broker down")

    async def disconnect(self) -> None:
        self.disconnected = True
        self.closed.set()

    async def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        if self.closed.is_set() or self.fail_publish:
            raise MqttError("connection lost")
        text = payload.decode() if isinstance(payload, bytes) else payload
        self.published.append((topic, text, qos, retain))

    async def subscribe(self, topic_filter: str, qos: int = 1) -> int:
        self.subscriptions.append(topic_filter)
        return 1

    async def messages(self) -> AsyncIterator[Message]:
        while True:
            item = await self.incoming.get()
            if item is None:
                raise MqttError("connection lost")
            yield item

    async def wait_closed(self) -> None:
        await self.closed.wait()
        raise MqttError("connection lost")

    def drop(self) -> None:
        self.closed.set()
        self.incoming.put_nowait(None)

    def topics(self) -> list[str]:
        return [p[0] for p in self.published]


async def until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        FakeMqtt.instances = []
        FakeMqtt.fail_connects = 0
        self.clock = FakeClock()
        self.access = FakeAccess()

    async def start(self, **sections: Any) -> Service:
        cfg = make_config(**sections)
        service = Service(
            cfg,
            access=self.access,
            mqtt_factory=FakeMqtt,
            clock=self.clock,
            expiry_interval=0.02,
            mqtt_initial_backoff=0.01,
            mqtt_max_backoff=0.05,
        )
        self.task = asyncio.create_task(service.run())
        await asyncio.wait_for(service.mqtt_connected.wait(), 3)
        await self.access.ready.wait()
        return service

    async def asyncTearDown(self) -> None:
        if hasattr(self, "task"):
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def test_startup_messages(self) -> None:
        await self.start(unlock={"enabled": True, "door_id": "door-1"})
        mqtt = FakeMqtt.instances[0]
        self.assertEqual(mqtt.will, Will("access_family_router/family_a/status", b"offline", 1, True))
        self.assertEqual(mqtt.published[0], ("access_family_router/family_a/status", "online", 1, True))
        discovery = [t for t in mqtt.topics() if t.startswith("homeassistant/")]
        self.assertEqual(len(discovery), 5)
        self.assertIn(("access_family_router/family_a/call", "OFF", 1, True), mqtt.published)
        self.assertEqual(mqtt.subscriptions, ["access_family_router/family_a/unlock/set"])

    async def test_no_subscription_without_unlock(self) -> None:
        await self.start()
        self.assertEqual(FakeMqtt.instances[0].subscriptions, [])

    async def test_ring_routing(self) -> None:
        await self.start()
        mqtt = FakeMqtt.instances[0]
        before = len(mqtt.published)
        await self.access.emit("Hello")
        await self.access.emit(ring_message(ROOM_B, "other-family"))
        await asyncio.sleep(0.05)
        self.assertEqual(len(mqtt.published), before, "a call for another family must not publish")
        await self.access.emit(ring_message(ROOM_A, "mine"))
        await until(lambda: f"unifi/protect/{MAC}/doorbell/set" in mqtt.topics())
        await until(lambda: ("access_family_router/family_a/call", "ON", 1, True) in mqtt.published)
        await self.access.emit(end_message("mine", 107))
        await until(lambda: mqtt.published[-1] == ("access_family_router/family_a/call", "OFF", 1, True))
        result = [p for p in mqtt.published if p[0].endswith("/call_result")][-1]
        self.assertEqual(json.loads(result[1])["event_type"], "unlocked")

    async def test_unlock_command(self) -> None:
        service = await self.start(unlock={"enabled": True, "door_id": "door-1", "actor_name": "Casa A"})
        mqtt = FakeMqtt.instances[0]
        topic = "access_family_router/family_a/unlock/set"

        mqtt.incoming.put_nowait(Message(topic, b"PRESS", 1, True))   # retained -> ignored
        mqtt.incoming.put_nowait(Message(topic, b"open", 1, False))   # wrong payload
        mqtt.incoming.put_nowait(Message("other/topic", b"PRESS", 1, False))
        mqtt.incoming.put_nowait(Message(topic, b"press", 1, False))  # accepted (case-insensitive)
        await until(lambda: len(self.access.unlocks) == 1)
        self.assertEqual(self.access.unlocks[0], ("door-1", "family-router-family_a", "Casa A"))
        self.assertIn((topic, "", 1, True), mqtt.published, "retained command must be cleared")
        mqtt.incoming.put_nowait(Message(topic, b"", 1, False))  # the clear echo is ignored
        await until(lambda: any(p[0].endswith("/unlock/result") for p in mqtt.published))
        result = json.loads([p for p in mqtt.published if p[0].endswith("/unlock/result")][-1][1])
        self.assertTrue(result["ok"])

        # cooldown: a second press within 3 s is ignored
        mqtt.incoming.put_nowait(Message(topic, b"PRESS", 1, False))
        await asyncio.sleep(0.05)
        self.assertEqual(len(self.access.unlocks), 1)

        # after the cooldown a failure is reported, not raised
        self.clock.now += 5
        self.access.unlock_error = RuntimeError("HTTP 403 forbidden")
        mqtt.incoming.put_nowait(Message(topic, b"UNLOCK", 1, False))
        await until(lambda: len(self.access.unlocks) == 2)
        await until(lambda: sum(p[0].endswith("/unlock/result") for p in mqtt.published) == 2)
        result = json.loads([p for p in mqtt.published if p[0].endswith("/unlock/result")][-1][1])
        self.assertFalse(result["ok"])
        self.assertIn("403", result["error"])
        self.assertTrue(service.mqtt_connected.is_set())

    async def test_reconnect_and_queue(self) -> None:
        service = await self.start()
        first = FakeMqtt.instances[0]
        FakeMqtt.fail_connects = 2
        first.drop()
        await until(lambda: not service.mqtt_connected.is_set())

        # while MQTT is down: one fresh ring and one that becomes stale
        await self.access.emit(ring_message(ROOM_A, "old"))
        self.clock.now += STALE_RING_SECONDS + 1
        await self.access.emit(ring_message(ROOM_A, "fresh"))

        await asyncio.wait_for(service.mqtt_connected.wait(), 3)
        second = FakeMqtt.instances[-1]
        self.assertGreaterEqual(len(FakeMqtt.instances), 4)  # 2 failed attempts + 1 good
        self.assertEqual(second.published[0][1], "online")
        await until(lambda: any('"fresh"' in p[1] for p in second.published))
        rings = [json.loads(p[1])["request_id"] for p in second.published if p[0].endswith("/ring")]
        self.assertEqual(rings, ["fresh"], "stale ring must be dropped, fresh one delivered")
        doorbell = [p for p in second.published if p[0].endswith("/doorbell/set")]
        self.assertEqual(len(doorbell), 1)
        # retained call state is still delivered in order
        self.assertIn(("access_family_router/family_a/call", "ON", 1, True), second.published)

    async def test_publish_failure_keeps_message(self) -> None:
        service = await self.start()
        first = FakeMqtt.instances[0]
        first.fail_publish = True
        await self.access.emit(ring_message(ROOM_A, "r1"))
        await until(lambda: len(FakeMqtt.instances) >= 2 and service.mqtt_connected.is_set())
        second = FakeMqtt.instances[-1]
        await until(lambda: any(p[0].endswith("/ring") for p in second.published))

    async def test_expiry(self) -> None:
        await self.start()
        mqtt = FakeMqtt.instances[0]
        await self.access.emit(ring_message(ROOM_A, "r1"))
        await until(lambda: ("access_family_router/family_a/call", "ON", 1, True) in mqtt.published)
        self.clock.now += 61

        def expired() -> bool:
            return any(
                p[0].endswith("/call_result") and json.loads(p[1])["event_type"] == "expired"
                for p in mqtt.published
            )

        await until(expired)
        await until(lambda: mqtt.published[-1] == ("access_family_router/family_a/call", "OFF", 1, True))

    async def test_learn_mode_logs_doors(self) -> None:
        from family_router.config import parse_config

        from .support import raw_config

        async def list_doors() -> list[dict[str, str]]:
            return [{"id": "door-xyz", "full_name": "Console - Portone"}]

        self.access.list_doors = list_doors  # type: ignore[attr-defined]
        cfg = parse_config({**raw_config(family={"match": {}}), "learn_mode": True}, env={})
        with self.assertLogs("family_router.service", level="WARNING") as logs:
            service = Service(cfg, access=self.access, mqtt_factory=FakeMqtt, clock=self.clock)
            self.task = asyncio.create_task(service.run())
            await asyncio.wait_for(service.mqtt_connected.wait(), 3)
            await until(lambda: any("door-xyz" in line for line in logs.output))
        self.assertIn("LEARN: door 'Console - Portone' -> door_id door-xyz", "\n".join(logs.output))

        async def failing() -> list[dict[str, str]]:
            raise RuntimeError("HTTP 403")

        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.access.list_doors = failing  # type: ignore[attr-defined]
        with self.assertLogs("family_router.service", level="WARNING") as logs:
            service = Service(cfg, access=self.access, mqtt_factory=FakeMqtt, clock=self.clock)
            self.task = asyncio.create_task(service.run())
            await asyncio.wait_for(service.mqtt_connected.wait(), 3)
            await until(lambda: any("view:space" in line for line in logs.output))

    async def test_shutdown_publishes_offline(self) -> None:
        await self.start()
        mqtt = FakeMqtt.instances[0]
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.assertEqual(mqtt.published[-1], ("access_family_router/family_a/status", "offline", 1, True))
        self.assertTrue(mqtt.disconnected)


if __name__ == "__main__":
    unittest.main()
