"""Core routing logic (pure, no I/O).

Given parsed Access events, decide which MQTT messages this family's
router instance must publish. Every family runs its own instance with its
own configuration, broker and Apple Home / Home Assistant, so families stay
completely separated: an instance only ever acts on calls that match *its*
directory entry.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from .config import Config
from .events import CallEnded, RingEvent

__all__ = ["FamilyRouter", "Publish", "Topics", "RESULT_EVENT_TYPES"]

_LOGGER = logging.getLogger(__name__)

#: event types published on the "call result" event entity
RESULT_EVENT_TYPES: tuple[str, ...] = (
    "answered_elsewhere",
    "unlocked",
    "rejected",
    "canceled",
    "timeout",
    "expired",
    "unknown",
)

_SEEN_TTL = 600.0


@dataclass(frozen=True)
class Publish:
    """An MQTT message to publish."""

    topic: str
    payload: str
    retain: bool = False
    qos: int = 0


class Topics:
    """All MQTT topics used by one family instance."""

    def __init__(self, config: Config) -> None:
        base = f"{config.mqtt.base_topic}/{config.family.id}"
        self.base = base
        self.status = f"{base}/status"
        self.ring = f"{base}/ring"
        self.call = f"{base}/call"
        self.result = f"{base}/call_result"
        self.learn = f"{base}/learn"
        self.unlock_command = f"{base}/unlock/set"
        self.unlock_result = f"{base}/unlock/result"
        hb = config.homebridge
        self.homebridge_doorbell = (
            f"{hb.topic_prefix}/{hb.camera_mac}/doorbell/set" if hb.enabled else ""
        )


def _json(data: Mapping[str, object]) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


class FamilyRouter:
    """Stateful decision engine for one family."""

    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic) -> None:
        self._config = config
        self._clock = clock
        self.topics = Topics(config)
        self._match = config.family.match
        self._room_ids = frozenset(self._match.room_ids)
        self._guard_ids = frozenset(self._match.door_guard_ids)
        self._device_ids = frozenset(self._match.device_ids)
        self._active: dict[str, float] = {}
        self._seen: dict[str, float] = {}

    # ------------------------------------------------------------------ api

    @property
    def active_calls(self) -> tuple[str, ...]:
        return tuple(self._active)

    def matches(self, ring: RingEvent) -> tuple[bool, str]:
        """Return ``(matched, why)`` for a ring event."""

        if self._device_ids and ring.device_id not in self._device_ids:
            return False, f"device {ring.device_id or '?'} not in device_ids"
        if self._match.ring_all:
            return True, "ring_all"
        if ring.room_id and ring.room_id in self._room_ids:
            return True, f"room_id {ring.room_id}"
        common = self._guard_ids.intersection(ring.door_guard_ids)
        if common:
            return True, f"door_guard_id {sorted(common)[0]}"
        return False, f"room_id {ring.room_id or '?'} not configured"

    def handle(self, event: RingEvent | CallEnded | None) -> list[Publish]:
        if event is None:
            return []
        if isinstance(event, RingEvent):
            return self._on_ring(event)
        return self._on_end(event)

    def expire(self) -> list[Publish]:
        """Close calls that never received an end event; prune memory."""

        now = self._clock()
        out: list[Publish] = []
        timeout = self._config.call_timeout_seconds
        for request_id, started in list(self._active.items()):
            if now - started >= timeout:
                del self._active[request_id]
                _LOGGER.info("Call %s expired after %.0fs without end event", request_id, timeout)
                out.extend(self._end_messages(request_id, None, "expired"))
        for request_id, seen in list(self._seen.items()):
            if now - seen >= _SEEN_TTL:
                del self._seen[request_id]
        return out

    def reset_messages(self) -> list[Publish]:
        """Messages to publish after (re)connecting to MQTT."""

        return [Publish(self.topics.call, "ON" if self._active else "OFF", retain=True, qos=1)]

    # ------------------------------------------------------------- internal

    def _on_ring(self, ring: RingEvent) -> list[Publish]:
        now = self._clock()
        if ring.request_id in self._seen:
            _LOGGER.debug("Duplicate ring %s ignored", ring.request_id)
            return []
        self._seen[ring.request_id] = now

        matched, why = self.matches(ring)
        out: list[Publish] = []

        if self._config.learn_mode:
            info = {
                "request_id": ring.request_id,
                "room_id": ring.room_id,
                "door_guard_ids": list(ring.door_guard_ids),
                "device_id": ring.device_id,
                "device_name": ring.device_name,
                "door_name": ring.door_name,
                "create_time": ring.create_time,
                "matched": matched,
            }
            _LOGGER.warning(
                "LEARN: ring on %s (%s) room_id=%s door_guard_ids=%s -> %s",
                ring.door_name or ring.device_name or ring.device_id or "?",
                ring.device_id or "?",
                ring.room_id or "?",
                ",".join(ring.door_guard_ids) or "-",
                "MATCH" if matched else "no match",
            )
            out.append(Publish(self.topics.learn, _json(info), retain=True, qos=1))

        if not matched:
            _LOGGER.debug("Ring %s not for this family: %s", ring.request_id, why)
            return out

        _LOGGER.info(
            "Ring %s for family '%s' (%s) on %s",
            ring.request_id,
            self._config.family.name,
            why,
            ring.door_name or ring.device_name or ring.device_id or "?",
        )
        self._active[ring.request_id] = now
        if self.topics.homebridge_doorbell:
            out.append(Publish(self.topics.homebridge_doorbell, "true", qos=1))
        out.append(
            Publish(
                self.topics.ring,
                _json(
                    {
                        "event_type": "ring",
                        "request_id": ring.request_id,
                        "room_id": ring.room_id,
                        "door_name": ring.door_name,
                        "device_name": ring.device_name,
                    }
                ),
                qos=1,
            )
        )
        out.append(Publish(self.topics.call, "ON", retain=True, qos=1))
        return out

    def _on_end(self, end: CallEnded) -> list[Publish]:
        if end.request_id not in self._active:
            return []
        del self._active[end.request_id]
        _LOGGER.info("Call %s ended: %s", end.request_id, end.reason)
        return self._end_messages(end.request_id, end.reason_code, end.reason)

    def _end_messages(self, request_id: str, code: int | None, reason: str) -> list[Publish]:
        if reason not in RESULT_EVENT_TYPES:
            reason = "unknown"
        out = [
            Publish(
                self.topics.result,
                _json({"event_type": reason, "reason_code": code, "request_id": request_id}),
                qos=1,
            )
        ]
        if not self._active:
            out.append(Publish(self.topics.call, "OFF", retain=True, qos=1))
        return out
