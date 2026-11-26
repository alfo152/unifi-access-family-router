from __future__ import annotations

import json
import unittest

from family_router.events import parse_message
from family_router.router import FamilyRouter, Publish

from .support import MAC, ROOM_A, ROOM_B, ROOM_C, end_message, make_config, ring_message


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def topics_of(messages: list[Publish]) -> list[str]:
    return [m.topic for m in messages]


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.families = {
            "a": FamilyRouter(
                make_config(family={"id": "family_a", "name": "A", "match": {"room_ids": [ROOM_A]}}),
                clock=self.clock,
            ),
            "b": FamilyRouter(
                make_config(family={"id": "family_b", "name": "B", "match": {"room_ids": [ROOM_B]}}),
                clock=self.clock,
            ),
            "c": FamilyRouter(
                make_config(family={"id": "family_c", "name": "C", "match": {"room_ids": [ROOM_C]}}),
                clock=self.clock,
            ),
        }

    def dispatch(self, raw: str) -> dict[str, list[Publish]]:
        event = parse_message(raw)
        return {name: router.handle(event) for name, router in self.families.items()}

    def test_only_called_family_rings(self) -> None:
        for room, expected in ((ROOM_A, "a"), (ROOM_B, "b"), (ROOM_C, "c")):
            with self.subTest(room=room):
                out = self.dispatch(ring_message(room, f"req-{expected}"))
                for name, messages in out.items():
                    if name == expected:
                        self.assertEqual(
                            topics_of(messages),
                            [
                                f"unifi/protect/{MAC}/doorbell/set",
                                f"access_family_router/family_{name}/ring",
                                f"access_family_router/family_{name}/call",
                            ],
                        )
                        self.assertEqual(messages[0].payload, "true")
                        ring = json.loads(messages[1].payload)
                        self.assertEqual(ring["event_type"], "ring")
                        self.assertEqual(ring["room_id"], room)
                        self.assertEqual(messages[2], Publish(messages[2].topic, "ON", True, 1))
                    else:
                        self.assertEqual(messages, [], f"family {name} must stay silent")

    def test_unknown_room_rings_nobody(self) -> None:
        out = self.dispatch(ring_message("WR-unknown", "req-x"))
        self.assertTrue(all(m == [] for m in out.values()))

    def test_duplicate_ring_ignored(self) -> None:
        first = self.families["a"].handle(parse_message(ring_message(ROOM_A, "dup")))
        second = self.families["a"].handle(parse_message(ring_message(ROOM_A, "dup")))
        self.assertTrue(first)
        self.assertEqual(second, [])

    def test_call_end_only_for_own_call(self) -> None:
        self.dispatch(ring_message(ROOM_B, "req-b"))
        out = self.dispatch(end_message("req-b", 400))
        self.assertEqual(out["a"], [])
        self.assertEqual(out["c"], [])
        result, call = out["b"]
        self.assertEqual(result.topic, "access_family_router/family_b/call_result")
        self.assertEqual(
            json.loads(result.payload),
            {"event_type": "answered_elsewhere", "reason_code": 400, "request_id": "req-b"},
        )
        self.assertEqual(call, Publish("access_family_router/family_b/call", "OFF", True, 1))
        # a second end event for the same call does nothing
        self.assertEqual(self.dispatch(end_message("req-b", 105))["b"], [])

    def test_call_stays_on_while_another_call_active(self) -> None:
        router = self.families["a"]
        router.handle(parse_message(ring_message(ROOM_A, "r1")))
        router.handle(parse_message(ring_message(ROOM_A, "r2")))
        out = router.handle(parse_message(end_message("r1", 108)))
        self.assertEqual(topics_of(out), ["access_family_router/family_a/call_result"])
        out = router.handle(parse_message(end_message("r2", 107)))
        self.assertEqual(out[-1].payload, "OFF")
        self.assertEqual(json.loads(out[0].payload)["event_type"], "unlocked")

    def test_expiry(self) -> None:
        router = self.families["a"]
        router.handle(parse_message(ring_message(ROOM_A, "r1")))
        self.clock.now += 59
        self.assertEqual(router.expire(), [])
        self.clock.now += 1
        out = router.expire()
        self.assertEqual(json.loads(out[0].payload)["event_type"], "expired")
        self.assertIsNone(json.loads(out[0].payload)["reason_code"])
        self.assertEqual(out[1].payload, "OFF")
        self.assertEqual(router.active_calls, ())
        # dedupe memory is pruned after 10 minutes: the same id rings again
        self.clock.now += 601
        router.expire()
        self.assertTrue(router.handle(parse_message(ring_message(ROOM_A, "r1"))))

    def test_door_guard_match(self) -> None:
        router = FamilyRouter(
            make_config(family={"match": {"room_ids": None, "door_guard_ids": ["guard-1"]}}),
            clock=self.clock,
        )
        self.assertEqual(router.handle(parse_message(ring_message("WR-x", "r1"))), [])
        out = router.handle(parse_message(ring_message("WR-x", "r2", door_guard_ids=["z", "guard-1"])))
        self.assertTrue(out)
        self.assertEqual(router.matches(parse_message(ring_message("", "r3", door_guard_ids=["guard-1"])))[1],  # type: ignore[arg-type]
                         "door_guard_id guard-1")

    def test_device_filter(self) -> None:
        router = FamilyRouter(
            make_config(family={"match": {"room_ids": [ROOM_A], "device_ids": ["intercom1"]}}),
            clock=self.clock,
        )
        self.assertEqual(router.handle(parse_message(ring_message(ROOM_A, "r1", device_id="other"))), [])
        self.assertTrue(router.handle(parse_message(ring_message(ROOM_A, "r2", device_id="intercom1"))))

    def test_ring_all(self) -> None:
        router = FamilyRouter(make_config(family={"match": {"room_ids": None, "ring_all": True}}),
                              clock=self.clock)
        self.assertTrue(router.handle(parse_message(ring_message("anything", "r1"))))

    def test_homebridge_disabled(self) -> None:
        router = FamilyRouter(make_config(homebridge={"enabled": False, "camera_mac": ""}),
                              clock=self.clock)
        out = router.handle(parse_message(ring_message(ROOM_A, "r1")))
        self.assertEqual(
            topics_of(out),
            ["access_family_router/family_a/ring", "access_family_router/family_a/call"],
        )

    def test_learn_mode(self) -> None:
        from family_router.config import parse_config

        from .support import raw_config

        cfg = parse_config({**raw_config(family={"match": {}}), "learn_mode": True}, env={})
        router = FamilyRouter(cfg, clock=self.clock)
        with self.assertLogs("family_router.router", level="WARNING") as logs:
            out = router.handle(parse_message(ring_message(ROOM_B, "r1")))
        self.assertEqual(topics_of(out), ["access_family_router/family_a/learn"])
        info = json.loads(out[0].payload)
        self.assertEqual(info["room_id"], ROOM_B)
        self.assertFalse(info["matched"])
        self.assertTrue(out[0].retain)
        self.assertIn(ROOM_B, logs.output[0])

        # learn mode with a rule: still rings and reports a match
        cfg = parse_config({**raw_config(), "learn_mode": True}, env={})
        router = FamilyRouter(cfg, clock=self.clock)
        with self.assertLogs("family_router.router", level="WARNING"):
            out = router.handle(parse_message(ring_message(ROOM_A, "r2")))
        self.assertEqual(out[0].topic, "access_family_router/family_a/learn")
        self.assertTrue(json.loads(out[0].payload)["matched"])
        self.assertIn(f"unifi/protect/{MAC}/doorbell/set", topics_of(out))

    def test_reset_messages(self) -> None:
        router = self.families["a"]
        self.assertEqual(router.reset_messages()[0].payload, "OFF")
        router.handle(parse_message(ring_message(ROOM_A, "r1")))
        self.assertEqual(router.reset_messages()[0].payload, "ON")

    def test_none_event(self) -> None:
        self.assertEqual(self.families["a"].handle(None), [])


if __name__ == "__main__":
    unittest.main()
