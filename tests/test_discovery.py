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
