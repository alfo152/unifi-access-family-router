"""End-to-end simulation of a 3-family building.

Real components: our AccessClient (TLS + pinned certificate + WebSocket +
REST), our MQTT client, the full Service. Doubles: the UniFi console
(Node 'ws' library, same port for WebSocket and REST, per-token
permissions) and one separate MQTT broker per family.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from family_router.config import parse_config
from family_router.service import Service

from .broker import TestBroker
from .support import (
    HAVE_NODE_WS,
    HAVE_OPENSSL,
    MAC,
    ROOM_A,
    ROOM_B,
    ROOM_C,
    SelfSignedCert,
    end_message,
    ring_message,
)

DOOR_ID = "0ed545f8-2fcd-4839-9021-b39e707f6aa9"
FAMILIES = {
    "a": {"room": ROOM_A, "token": "tok-a", "unlock": True},
    "b": {"room": ROOM_B, "token": "tok-b", "unlock": True},
    "c": {"room": ROOM_C, "token": "tok-c", "unlock": False},
}
SCOPES = {
    "tok-a": ["view:device", "view:space", "edit:space"],
    "tok-b": ["view:device", "view:space", "edit:space"],
    "tok-c": ["view:device"],  # family C: notifications only, no unlock right
}


def topic(name: str, leaf: str) -> str:
    return f"access_family_router/family_{name}/{leaf}"


DOORBELL = f"unifi/protect/{MAC}/doorbell/set"


async def wait_log(logs: Any, text: str, timeout: float = 10.0) -> None:
    """Wait until a captured log line (assertLogs) contains ``text``."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not any(text in line for line in logs.output):
        if loop.time() > deadline:
            raise AssertionError(f"log containing {text!r} not seen: {logs.output}")
        await asyncio.sleep(0.02)


@unittest.skipUnless(HAVE_NODE_WS and HAVE_OPENSSL, "node 'ws' module or openssl not available")
class ThreeFamilyBuildingTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from .ws_fake import NodeAccessServer

        self.cert = SelfSignedCert()
        self.console = NodeAccessServer(self.cert, SCOPES)
        await self.console.start()
        self.brokers: dict[str, TestBroker] = {}
        self.services: dict[str, Service] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        for name, fam in FAMILIES.items():
            broker = TestBroker(users={f"router-{name}": "pw", f"ha-{name}": "pw"})
            await broker.start()
            self.brokers[name] = broker
            raw: dict[str, Any] = {
                "access": {
                    "host": "127.0.0.1",
                    "port": self.console.port,
                    "token": fam["token"],
                    "tls_fingerprint": self.cert.fingerprint,
                },
                "family": {"name": f"Famiglia {name.upper()}", "id": f"family_{name}",
                           "match": {"room_ids": [fam["room"]]}},
                "mqtt": {"host": "127.0.0.1", "port": broker.port,
                         "username": f"router-{name}", "password": "pw", "keepalive": 5},
                "homebridge": {"enabled": True, "camera_mac": MAC},
                "unlock": {"enabled": fam["unlock"], "door_id": DOOR_ID if fam["unlock"] else "",
                           "cooldown_seconds": 0},
            }
            service = Service(parse_config(raw, env={}), mqtt_initial_backoff=0.2, mqtt_max_backoff=1)
            self.services[name] = service
            self.tasks[name] = asyncio.create_task(service.run(), name=f"family-{name}")
        for name, service in self.services.items():
            await asyncio.wait_for(service.mqtt_connected.wait(), 10)
        for fam in FAMILIES.values():
            await self.console.wait_event(
                lambda e, t=fam["token"]: e["event"] == "connected" and e["token"] == t, timeout=10
            )

    async def asyncTearDown(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.console.stop()
        for broker in self.brokers.values():
            await broker.stop()
        self.cert.cleanup()

    # ---------------------------------------------------------------- utils

    def doorbells(self, name: str) -> int:
        return len(self.brokers[name].messages(DOORBELL))

    def last(self, name: str, leaf: str) -> Any:
        messages = self.brokers[name].messages(topic(name, leaf))
        return messages[-1].payload.decode() if messages else None

    async def ring(self, room: str, request_id: str) -> None:
        await self.console.broadcast(ring_message(room, request_id))

    # ---------------------------------------------------------------- tests

    async def test_full_building(self) -> None:
        a, b, c = (self.brokers[n] for n in "abc")

        # Startup: online + discovery + call OFF, each on its own broker only
        for name, broker in self.brokers.items():
            self.assertEqual(self.last(name, "status"), "online")
            self.assertTrue(any(m.topic.startswith("homeassistant/event/") for m in broker.log))
            for other in set("abc") - {name}:
                self.assertFalse(
                    any(f"family_{other}" in m.topic for m in broker.log),
                    f"broker of {name} received data of family {other}",
                )

        # 1) visitor calls family B
        await self.ring(ROOM_B, "call-1")
        await b.wait_for(lambda: self.doorbells("b") == 1)
        await b.wait_for(lambda: self.last("b", "call") == "ON")
        ring = json.loads(self.last("b", "ring"))
        self.assertEqual((ring["event_type"], ring["room_id"]), ("ring", ROOM_B))
        await asyncio.sleep(0.3)
        self.assertEqual((self.doorbells("a"), self.doorbells("c")), (0, 0))

        # 2) answered on another device -> call result + call OFF for B only
        await self.console.broadcast(end_message("call-1", 400))
        await b.wait_for(lambda: self.last("b", "call") == "OFF")
        self.assertEqual(json.loads(self.last("b", "call_result"))["event_type"], "answered_elsewhere")
        self.assertIsNone(self.last("a", "call_result"))

        # 3) noise: Hello, unknown room, duplicate, unrelated events
        await self.console.broadcast("Hello")
        await self.ring("WR-not-configured", "call-x")
        await self.console.broadcast('{"event":"access.data.device.update","data":{}}')
        await self.ring(ROOM_B, "call-1")  # duplicate request id
        await asyncio.sleep(0.4)
        self.assertEqual([self.doorbells(n) for n in "abc"], [0, 1, 0])

        # 4) A and C called almost together
        await self.ring(ROOM_A, "call-2")
        await self.ring(ROOM_C, "call-3")
        await a.wait_for(lambda: self.doorbells("a") == 1)
        await c.wait_for(lambda: self.doorbells("c") == 1)
        self.assertEqual(self.doorbells("b"), 1)

        # 5) family A unlocks from Home Assistant (button press)
        await a.inject(topic("a", "unlock/set"), b"PRESS", qos=1)
        http = await self.console.wait_event(lambda e: e["event"] == "http" and e["method"] == "PUT")
        self.assertEqual(http["token"], "tok-a")
        self.assertEqual(http["path"], f"/api/v1/developer/doors/{DOOR_ID}/unlock")
        self.assertEqual(http["body"], {"actor_id": "family-router-family_a",
                                        "actor_name": "Famiglia A (family router)"})
        await a.wait_for(lambda: self.last("a", "unlock/result") is not None)
        self.assertTrue(json.loads(self.last("a", "unlock/result"))["ok"])

        # 6) a command published *retained* on B's broker (misconfigured
        #    automation): it opens once, live, but must never open again when
        #    the router reconnects and the broker re-delivers it.
        def puts() -> int:
            return self.console.count(lambda e: e["event"] == "http" and e["method"] == "PUT")

        before = puts()
        await b.inject(topic("b", "unlock/set"), b"PRESS", qos=1, retain=True)
        await self.console.wait_event(
            lambda e: e["event"] == "http" and e["method"] == "PUT" and e["token"] == "tok-b"
        )
        self.assertEqual(puts(), before + 1)
        self.assertIn(topic("b", "unlock/set"), b.retained)
        await b.stop()
        await b.start()  # same port, retained store kept -> re-delivered with retain=1
        await b.wait_for(lambda: topic("b", "unlock/set") not in b.retained, timeout=10)
        await asyncio.sleep(0.3)
        self.assertEqual(puts(), before + 1, "re-delivered retained command opened the door")
        puts_before = puts()

        # 7) family C has no unlock button and no subscription at all
        await c.inject(topic("c", "unlock/set"), b"PRESS", qos=1)
        await asyncio.sleep(0.3)
        self.assertEqual(
            self.console.count(lambda e: e["event"] == "http" and e["method"] == "PUT"), puts_before
        )
        disc = [m for m in c.log if m.topic.startswith("homeassistant/button/")]
        self.assertEqual(disc[-1].payload, b"")  # entity removed

        # 8) the console drops every WebSocket -> all families reconnect
        mark = len(self.console.events)
        await self.console.send(cmd="terminate")
        for fam in FAMILIES.values():
            await self.console.wait_event(
                lambda e, t=fam["token"]: e["event"] == "connected" and e["token"] == t,
                start=mark, timeout=10,
            )
        await self.ring(ROOM_C, "call-4")
        await c.wait_for(lambda: self.doorbells("c") == 2)

        # 9) family A's broker restarts -> A reconnects and republishes state
        port = a.port
        await a.stop()
        await asyncio.sleep(0.3)
        await a.start()
        self.assertEqual(a.port, port)
        await a.wait_for(lambda: self.services["a"].mqtt_connected.is_set(), timeout=10)
        await a.wait_for(lambda: self.last("a", "status") == "online", timeout=10)
        await self.ring(ROOM_A, "call-5")
        await a.wait_for(lambda: self.doorbells("a") == 2, timeout=5)

        # 10) final separation check on every broker
        for name, broker in self.brokers.items():
            for other in set("abc") - {name}:
                self.assertFalse(any(f"family_{other}" in m.topic for m in broker.log))

        # 11) clean shutdown -> status offline on each broker
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for name, broker in self.brokers.items():
            self.assertEqual(self.last(name, "status"), "offline")

    async def test_wrong_token_does_not_crash(self) -> None:
        broker = TestBroker()
        await broker.start()
        raw = {
            "access": {"host": "127.0.0.1", "port": self.console.port, "token": "stolen",
                       "tls_fingerprint": self.cert.fingerprint},
            "family": {"name": "Intruder", "match": {"ring_all": True}},
            "mqtt": {"host": "127.0.0.1", "port": broker.port},
        }
        service = Service(parse_config(raw, env={}))
        task = asyncio.create_task(service.run())
        with self.assertLogs("family_router.access_api", level="ERROR") as logs:
            await self.console.wait_event(lambda e: e["event"] == "rejected" and e["token"] == "stolen")
            await wait_log(logs, "view:device")
        await self.ring(ROOM_A, "call-z")
        await asyncio.sleep(0.3)
        self.assertEqual(broker.messages(DOORBELL), [])
        self.assertFalse(task.done())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await broker.stop()

    async def test_tampered_certificate_is_refused(self) -> None:
        broker = TestBroker()
        await broker.start()
        raw = {
            "access": {"host": "127.0.0.1", "port": self.console.port, "token": "tok-a",
                       "tls_fingerprint": "AB" * 32},
            "family": {"name": "A2", "match": {"room_ids": [ROOM_A]}},
            "mqtt": {"host": "127.0.0.1", "port": broker.port},
        }
        before = self.console.count(lambda e: e["event"] == "connected")
        service = Service(parse_config(raw, env={}))
        task = asyncio.create_task(service.run())
        with self.assertLogs("family_router.access_api", level="ERROR") as logs:
            await wait_log(logs, "fingerprint mismatch")
        self.assertEqual(self.console.count(lambda e: e["event"] == "connected"), before)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await broker.stop()


DOOR_2 = "a8f2c6d1-3b4e-4f5a-9c7d-1e2f3a4b5c6d"
UNKNOWN_DOOR = "ffffffff-0000-4000-8000-000000000000"


@unittest.skipUnless(HAVE_NODE_WS and HAVE_OPENSSL, "node 'ws' module or openssl not available")
class MultipleDoorsTest(unittest.IsolatedAsyncioTestCase):
    """One family with three doors, against the console double and a real broker."""

    async def asyncSetUp(self) -> None:
        from .ws_fake import NodeAccessServer

        self.cert = SelfSignedCert()
        self.console = NodeAccessServer(self.cert, {"tok-d": SCOPES["tok-a"]})
        await self.console.start()
        self.broker = TestBroker(users={"router-d": "pw"})
        await self.broker.start()
        # entities left behind by an older configuration (a renamed door)
        node = "homeassistant/{}/access_family_router_family_d/{}/config"
        self.stale = [node.format("button", "unlock_old_gate"), node.format("lock", "lock_old_gate")]
        for stale in self.stale:
            await self.broker.inject(stale, b'{"name":"Old gate"}', qos=1, retain=True)
        raw: dict[str, Any] = {
            "access": {"host": "127.0.0.1", "port": self.console.port, "token": "tok-d",
                       "tls_fingerprint": self.cert.fingerprint},
            "family": {"name": "Famiglia D", "id": "family_d", "match": {"room_ids": [ROOM_A]}},
            "mqtt": {"host": "127.0.0.1", "port": self.broker.port,
                     "username": "router-d", "password": "pw", "keepalive": 5},
            "unlock": {"enabled": True, "cooldown_seconds": 0, "relock_seconds": 1, "doors": [
                f"Portone={DOOR_ID}",
                {"name": "Cancello", "door_id": DOOR_2},
                f"Garage={UNKNOWN_DOOR}",
            ]},
        }
        self.service = Service(parse_config(raw, env={}), mqtt_initial_backoff=0.2)
        self.task = asyncio.create_task(self.service.run())
        await asyncio.wait_for(self.service.mqtt_connected.wait(), 10)

    async def asyncTearDown(self) -> None:
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.console.stop()
        await self.broker.stop()
        self.cert.cleanup()

    def puts(self) -> list[str]:
        return [e["path"] for e in self.console.events if e["event"] == "http" and e["method"] == "PUT"]

    def state(self, key: str) -> str | None:
        retained = self.broker.retained.get(topic("d", f"unlock/{key}/state"))
        return retained[0].decode() if retained else None

    def results(self, key: str) -> list[dict[str, Any]]:
        return [json.loads(m.payload) for m in self.broker.messages(topic("d", f"unlock/{key}/result"))]

    async def test_three_doors(self) -> None:
        b = self.broker
        # stale entities removed, one button + one lock per door, all locked
        await b.wait_for(lambda: not any(s in b.retained for s in self.stale), timeout=5)
        for key in ("portone", "cancello", "garage"):
            self.assertIn(f"homeassistant/button/access_family_router_family_d/unlock_{key}/config",
                          b.retained)
            self.assertIn(f"homeassistant/lock/access_family_router_family_d/lock_{key}/config",
                          b.retained)
            self.assertEqual(self.state(key), "LOCKED")

        # Portone: opens its own door, shows unlocked, then locked again
        await b.inject(topic("d", "unlock/portone/set"), b"PRESS", qos=1)
        await b.wait_for(lambda: self.state("portone") == "UNLOCKED", timeout=5)
        self.assertEqual(self.puts(), [f"/api/v1/developer/doors/{DOOR_ID}/unlock"])
        self.assertTrue(self.results("portone")[-1]["ok"])
        await b.wait_for(lambda: self.state("portone") == "LOCKED", timeout=5)

        # Cancello right after, through the lock entity's UNLOCK payload
        await b.inject(topic("d", "unlock/cancello/set"), b"UNLOCK", qos=1)
        await b.wait_for(lambda: len(self.results("cancello")) == 1, timeout=5)
        self.assertEqual(self.puts()[-1], f"/api/v1/developer/doors/{DOOR_2}/unlock")
        self.assertEqual(self.results("cancello")[-1]["door"], "Cancello")

        # Garage: the console does not know the door -> error and "jammed"
        await b.inject(topic("d", "unlock/garage/set"), b"PRESS", qos=1)
        await b.wait_for(lambda: self.state("garage") == "JAMMED", timeout=5)
        result = self.results("garage")[-1]
        self.assertFalse(result["ok"])
        self.assertIn("NOT_FOUND", result["error"])
        await b.wait_for(lambda: self.state("garage") == "LOCKED", timeout=5)

        # LOCK and the single-door topic never reach the console
        before = len(self.puts())
        await b.inject(topic("d", "unlock/portone/set"), b"LOCK", qos=1)
        await b.inject(topic("d", "unlock/set"), b"PRESS", qos=1)
        await asyncio.sleep(0.4)
        self.assertEqual(len(self.puts()), before)
        self.assertEqual(self.puts().count(f"/api/v1/developer/doors/{UNKNOWN_DOOR}/unlock"), 1)


if __name__ == "__main__":
    unittest.main()
