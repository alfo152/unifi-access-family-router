"""Home Assistant MQTT discovery payloads.

Entities created in the family's own Home Assistant:

* ``event``         Doorbell      (device_class doorbell, event type ``ring``)
* ``event``         Call result   (answered elsewhere / unlocked / ...)
* ``binary_sensor`` Call active
* ``button``        Unlock        (only when unlock is enabled)
* ``sensor``        Last ring room_id (diagnostic, only in learn mode)

The Doorbell event entity only ever fires ``ring`` so it can be used as
``linked_doorbell_sensor`` by the Home Assistant HomeKit Bridge.
"""

from __future__ import annotations

import json

from . import __version__
from .config import Config
from .router import RESULT_EVENT_TYPES, Publish, Topics

__all__ = ["discovery_messages"]


def _config_topic(prefix: str, component: str, node: str, object_id: str) -> str:
    return f"{prefix}/{component}/{node}/{object_id}/config"


def discovery_messages(config: Config, topics: Topics) -> list[Publish]:
    """Return retained discovery messages (empty payload removes an entity)."""

    if not config.mqtt.discovery:
        return []

    prefix = config.mqtt.discovery_prefix
    node = f"access_family_router_{config.family.id}"
    device = {
        "identifiers": [node],
        "name": f"Intercom - {config.family.name}",
        "manufacturer": "UniFi Access Family Router",
        "model": "Per-family doorbell router",
        "sw_version": __version__,
    }
    origin = {
        "name": "unifi-access-family-router",
        "sw_version": __version__,
    }
    availability = {
        "availability_topic": topics.status,
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    def entity(component: str, object_id: str, payload: dict[str, object] | None) -> Publish:
        topic = _config_topic(prefix, component, node, object_id)
        if payload is None:
            return Publish(topic, "", retain=True, qos=1)
        body: dict[str, object] = {
            "unique_id": f"{node}_{object_id}",
            "device": device,
            "origin": origin,
            **availability,
            **payload,
        }
        return Publish(
            topic, json.dumps(body, separators=(",", ":"), ensure_ascii=False), retain=True, qos=1
        )

    messages = [
        entity(
            "event",
            "doorbell",
            {
                "name": "Doorbell",
                "device_class": "doorbell",
                "event_types": ["ring"],
                "state_topic": topics.ring,
                "icon": "mdi:doorbell-video",
            },
        ),
        entity(
            "event",
            "call_result",
            {
                "name": "Call result",
                "event_types": list(RESULT_EVENT_TYPES),
                "state_topic": topics.result,
                "icon": "mdi:phone-hangup",
            },
        ),
        entity(
            "binary_sensor",
            "call_active",
            {
                "name": "Call active",
                "state_topic": topics.call,
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:phone-ring",
            },
        ),
        entity(
            "button",
            "unlock",
            {
                "name": "Unlock",
                "command_topic": topics.unlock_command,
                "payload_press": "PRESS",
                "icon": "mdi:door-open",
            }
            if config.unlock.enabled
            else None,
        ),
        entity(
            "sensor",
            "learn",
            {
                "name": "Last ring room_id",
                "state_topic": topics.learn,
                "value_template": "{{ value_json.room_id }}",
                "json_attributes_topic": topics.learn,
                "entity_category": "diagnostic",
                "icon": "mdi:school",
            }
            if config.learn_mode
            else None,
        ),
    ]
    return messages
