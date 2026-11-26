"""Parsing of UniFi Access WebSocket notifications.

Source: official UniFi Access API reference, section 11.1
"Fetch Notifications [WebSocket]" (``/api/v1/developer/devices/notifications``).

Only the two doorbell events are used:

* ``access.remote_view``         - a doorbell call starts (contains ``room_id``)
* ``access.remote_view.change``  - the call ends (``reason_code``)

The webhook event ``access.doorbell.incoming`` does *not* carry ``room_id``,
which is why the WebSocket is used.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Union

__all__ = [
    "CallEnded",
    "REASONS",
    "RingEvent",
    "parse_message",
]

_LOGGER = logging.getLogger(__name__)

EVENT_RING = "access.remote_view"
EVENT_CHANGE = "access.remote_view.change"

#: reason codes documented for ``access.remote_view.change``
REASONS: dict[int, str] = {
    105: "timeout",
    106: "rejected",
    107: "unlocked",
    108: "canceled",
    400: "answered_elsewhere",
}


@dataclass(frozen=True)
class RingEvent:
    """A doorbell call started on an intercom or reader."""

    request_id: str
    room_id: str = ""
    door_guard_ids: tuple[str, ...] = ()
    device_id: str = ""
    device_name: str = ""
    device_type: str = ""
    door_name: str = ""
    floor_name: str = ""
    create_time: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True)
class CallEnded:
    """A doorbell call ended (answered, unlocked, canceled, timed out...)."""

    request_id: str
    reason_code: int | None
    reason: str


Event = Union[RingEvent, CallEnded]


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def parse_message(message: str | bytes) -> Event | None:
    """Return a :class:`RingEvent`/:class:`CallEnded`, or ``None`` to ignore.

    Non-JSON frames (the console sends a plain ``Hello`` on connect) and
    unrelated events are ignored silently.
    """

    if isinstance(message, (bytes, bytearray)):
        try:
            message = bytes(message).decode("utf-8")
        except UnicodeDecodeError:
            return None
    text = message.strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        _LOGGER.debug("Ignoring malformed JSON frame: %.200s", text)
        return None
    if not isinstance(payload, dict):
        return None

    event = payload.get("event")
    if event == EVENT_RING:
        data = _data(payload)
        request_id = _text(data.get("request_id"))
        if not request_id:
            _LOGGER.debug("Ignoring %s without request_id", EVENT_RING)
            return None
        guards_raw = data.get("door_guard_ids")
        guards: tuple[str, ...] = ()
        if isinstance(guards_raw, list):
            guards = tuple(g for g in (_text(x) for x in guards_raw) if g)
        return RingEvent(
            request_id=request_id,
            room_id=_text(data.get("room_id")),
            door_guard_ids=guards,
            device_id=_text(data.get("device_id")),
            device_name=_text(data.get("device_name")),
            device_type=_text(data.get("device_type")),
            door_name=_text(data.get("door_name")),
            floor_name=_text(data.get("floor_name")),
            create_time=_int_or_none(data.get("create_time")),
            raw=payload,
        )

    if event == EVENT_CHANGE:
        data = _data(payload)
        request_id = _text(data.get("remote_call_request_id")) or _text(
            data.get("request_id")
        )
        if not request_id:
            return None
        code = _int_or_none(data.get("reason_code"))
        reason = REASONS.get(code, "unknown") if code is not None else "unknown"
        return CallEnded(request_id=request_id, reason_code=code, reason=reason)

    return None
