from __future__ import annotations

import json
import unittest

from family_router.events import REASONS, CallEnded, RingEvent, parse_message

from .support import load_fixture


class EventParsingTests(unittest.TestCase):
    def test_official_ring_payload(self) -> None:
        raw = json.dumps(load_fixture("remote_view.json"))
        event = parse_message(raw)
        self.assertIsInstance(event, RingEvent)
        assert isinstance(event, RingEvent)
        self.assertEqual(event.request_id, "J0FeDJc8ZNzHjxr1SUIP6GLQDjAkdZFp")
        self.assertEqual(event.room_id, "WR-e4388386be1d-3YSCjtuV5VuyfLT46mUXnnY2q5KQfKxX")
        self.assertEqual(event.device_id, "e4388386be1d")
        self.assertEqual(event.device_type, "UA-G2-PRO")
        self.assertEqual(event.door_name, "Door 236b")
        self.assertEqual(event.floor_name, "1F")
        self.assertEqual(event.create_time, 1694771479)
        self.assertEqual(len(event.door_guard_ids), 9)
        self.assertEqual(event.door_guard_ids[0], "daa10301-7744-4623-a90e-372718af1d41")

    def test_official_change_payload(self) -> None:
        event = parse_message(json.dumps(load_fixture("remote_view_change.json")))
        self.assertEqual(
            event, CallEnded("J0FeDJc8ZNzHjxr1SUIP6GLQDjAkdZFp", 108, "canceled")
        )

    def test_all_reason_codes(self) -> None:
        for code, reason in REASONS.items():
            payload = load_fixture("remote_view_change.json")
            payload["data"]["reason_code"] = code
            event = parse_message(json.dumps(payload))
            self.assertEqual(event, CallEnded("J0FeDJc8ZNzHjxr1SUIP6GLQDjAkdZFp", code, reason))
        payload = load_fixture("remote_view_change.json")
        payload["data"]["reason_code"] = 999
        self.assertEqual(parse_message(json.dumps(payload)).reason, "unknown")  # type: ignore[union-attr]
        payload["data"]["reason_code"] = "x"
        ended = parse_message(json.dumps(payload))
        assert isinstance(ended, CallEnded)
        self.assertIsNone(ended.reason_code)

    def test_webhook_event_is_ignored(self) -> None:
        # The webhook variant has no room_id: it must not be used.
        self.assertIsNone(parse_message(json.dumps(load_fixture("doorbell_incoming_webhook.json"))))

    def test_ignored_frames(self) -> None:
        for frame in ("Hello", "", "   ", "[1,2]", "{not json", '"text"', "null",
                      '{"event":"access.data.device.update","data":{}}',
                      '{"event":"access.remote_view","data":{}}',
                      '{"event":"access.remote_view","data":"not-json"}',
                      '{"event":"access.remote_view.change","data":{"reason_code":105}}',
                      b"\xff\xfe", b'{"event": 1}'):
            with self.subTest(frame=frame):
                self.assertIsNone(parse_message(frame))

    def test_bytes_and_data_as_string(self) -> None:
        payload = load_fixture("remote_view.json")
        payload["data"] = json.dumps(payload["data"])
        event = parse_message(json.dumps(payload).encode())
        self.assertIsInstance(event, RingEvent)

    def test_change_with_request_id_fallback(self) -> None:
        event = parse_message(
            '{"event":"access.remote_view.change","data":{"reason_code":107,"request_id":"R1"}}'
        )
        self.assertEqual(event, CallEnded("R1", 107, "unlocked"))

    def test_robust_types(self) -> None:
        payload = load_fixture("remote_view.json")
        payload["data"]["door_guard_ids"] = ["a", None, 3, {"x": 1}, " "]
        payload["data"]["create_time"] = True
        payload["data"]["room_id"] = None
        event = parse_message(json.dumps(payload))
        assert isinstance(event, RingEvent)
        self.assertEqual(event.door_guard_ids, ("a", "3"))
        self.assertIsNone(event.create_time)
        self.assertEqual(event.room_id, "")


if __name__ == "__main__":
    unittest.main()
