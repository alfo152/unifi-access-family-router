from __future__ import annotations

import json
import unittest

from family_router.config import parse_config
from family_router.discovery import discovery_messages
from family_router.router import RESULT_EVENT_TYPES, Topics

from .support import make_config, raw_config


class DiscoveryTests(unittest.TestCase):
    def payloads(self, cfg) -> dict[str, dict | None]:  # type: ignore[no-untyped-def]
        out: dict[str, dict | None] = {}
        for message in discovery_messages(cfg, Topics(cfg)):
            self.assertTrue(message.retain)
            self.assertEqual(message.qos, 1)
            out[message.topic] = json.loads(message.payload) if message.payload else None
        return out

    def test_entities(self) -> None:
        cfg = make_config()
        data = self.payloads(cfg)
        node = "access_family_router_family_a"
        doorbell = data[f"homeassistant/event/{node}/doorbell/config"]
        assert doorbell is not None
        self.assertEqual(doorbell["device_class"], "doorbell")
        self.assertEqual(doorbell["event_types"], ["ring"])
        self.assertEqual(doorbell["state_topic"], "access_family_router/family_a/ring")
        self.assertEqual(doorbell["availability_topic"], "access_family_router/family_a/status")
        self.assertEqual(doorbell["unique_id"], f"{node}_doorbell")
        self.assertEqual(doorbell["device"]["identifiers"], [node])
        self.assertNotIn("object_id", doorbell)

        result = data[f"homeassistant/event/{node}/call_result/config"]
        assert result is not None
        self.assertEqual(result["event_types"], list(RESULT_EVENT_TYPES))

        call = data[f"homeassistant/binary_sensor/{node}/call_active/config"]
        assert call is not None
        self.assertEqual((call["payload_on"], call["payload_off"]), ("ON", "OFF"))

        # unlock disabled and not in learn mode -> those entities are removed
        self.assertIsNone(data[f"homeassistant/button/{node}/unlock/config"])
        self.assertIsNone(data[f"homeassistant/sensor/{node}/learn/config"])

        unique_ids = [p["unique_id"] for p in data.values() if p]
        self.assertEqual(len(unique_ids), len(set(unique_ids)))

    def test_unlock_and_learn_entities(self) -> None:
        cfg = parse_config(
            {**raw_config(unlock={"enabled": True, "door_id": "d1"}), "learn_mode": True}, env={}
        )
        data = self.payloads(cfg)
        node = "access_family_router_family_a"
        button = data[f"homeassistant/button/{node}/unlock/config"]
        assert button is not None
        self.assertEqual(button["command_topic"], "access_family_router/family_a/unlock/set")
        self.assertEqual(button["payload_press"], "PRESS")
        learn = data[f"homeassistant/sensor/{node}/learn/config"]
        assert learn is not None
        self.assertEqual(learn["entity_category"], "diagnostic")
        self.assertEqual(learn["value_template"], "{{ value_json.room_id }}")

    def test_single_door_lock_entity(self) -> None:
        cfg = make_config(unlock={"enabled": True, "door_id": "d1"})
        data = self.payloads(cfg)
        lock = data["homeassistant/lock/access_family_router_family_a/lock/config"]
        assert lock is not None
        self.assertEqual(lock["name"], "Door")
        self.assertEqual(lock["command_topic"], "access_family_router/family_a/unlock/set")
        self.assertEqual(lock["state_topic"], "access_family_router/family_a/unlock/state")
        self.assertEqual((lock["payload_unlock"], lock["payload_lock"]), ("UNLOCK", "LOCK"))
        self.assertEqual(
            (lock["state_locked"], lock["state_unlocked"], lock["state_jammed"]),
            ("LOCKED", "UNLOCKED", "JAMMED"),
        )
        self.assertFalse(lock["optimistic"])

    def test_multiple_doors(self) -> None:
        cfg = make_config(unlock={"enabled": True, "doors": [
            "Portone=door-1", {"name": "Cancello carrabile", "door_id": "door-2"}]})
        data = self.payloads(cfg)
        node = "access_family_router_family_a"
        base = "access_family_router/family_a/unlock"
        for key, name in (("portone", "Portone"), ("cancello_carrabile", "Cancello carrabile")):
            button = data[f"homeassistant/button/{node}/unlock_{key}/config"]
            lock = data[f"homeassistant/lock/{node}/lock_{key}/config"]
            assert button is not None and lock is not None
            self.assertEqual(button["name"], f"Unlock {name}")
            self.assertEqual(button["command_topic"], f"{base}/{key}/set")
            self.assertEqual(button["unique_id"], f"{node}_unlock_{key}")
            self.assertEqual(lock["name"], name)
            self.assertEqual(lock["command_topic"], f"{base}/{key}/set")
            self.assertEqual(lock["state_topic"], f"{base}/{key}/state")
        # the single-door entities are removed
        self.assertIsNone(data[f"homeassistant/button/{node}/unlock/config"])
        self.assertIsNone(data[f"homeassistant/lock/{node}/lock/config"])
        unique_ids = [p["unique_id"] for p in data.values() if p]
        self.assertEqual(len(unique_ids), len(set(unique_ids)))

    def test_doors_ignored_when_unlock_disabled(self) -> None:
        cfg = make_config(unlock={"enabled": False, "doors": ["Portone=door-1"]})
        data = self.payloads(cfg)
        self.assertFalse(any(topic.endswith("unlock_portone/config") for topic in data))
        self.assertIsNone(data["homeassistant/lock/access_family_router_family_a/lock/config"])

    def test_discovery_filter(self) -> None:
        from family_router.discovery import discovery_filter

        cfg = make_config(mqtt={"discovery_prefix": "ha/disc"})
        self.assertEqual(discovery_filter(cfg), "ha/disc/+/access_family_router_family_a/+/config")

    def test_custom_prefix_and_disabled(self) -> None:
        cfg = make_config(mqtt={"discovery_prefix": "ha", "base_topic": "intercom"})
        topics = Topics(cfg)
        self.assertEqual(topics.ring, "intercom/family_a/ring")
        self.assertTrue(all(m.topic.startswith("ha/") for m in discovery_messages(cfg, topics)))
        cfg = make_config(mqtt={"discovery": False})
        self.assertEqual(discovery_messages(cfg, Topics(cfg)), [])

    def test_unicode_family_name(self) -> None:
        cfg = make_config(family={"name": "Famiglia Città", "id": "citta"})
        doorbell = self.payloads(cfg)["homeassistant/event/access_family_router_citta/doorbell/config"]
        assert doorbell is not None
        self.assertEqual(doorbell["device"]["name"], "Intercom - Famiglia Città")


if __name__ == "__main__":
    unittest.main()
