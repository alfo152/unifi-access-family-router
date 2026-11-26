# Changelog

## Unreleased

- `tools/watch_calls.py`: standalone, read-only check (Python 3.8+, standard
  library only) that prints the `room_id` of every intercom call and says
  whether different directory entries produce different `room_id`s. No Home
  Assistant, Homebridge or MQTT needed.

## 0.1.1 - 2026-11-26

- Add-on: without the Mosquitto broker the log no longer shows the
  Supervisor's "Service not enabled" ERROR; a clear warning explains how to
  add a broker instead.
- README/LEGGIMI: one-click "Add repository" button and tester call.
- GitHub issue forms for test reports and bugs.

## 0.1.0 - 2026-11-26

First release.

- Per-family routing of UniFi Access intercom calls using the `room_id` of
  the called directory entry (official WebSocket API, `access.remote_view`).
- Optional matching on `door_guard_ids` and intercom `device_ids`; `ring_all`
  for single-family buildings; learn mode to discover the IDs.
- Apple Home: triggers the doorbell of homebridge-unifi-protect through MQTT
  (`<prefix>/<MAC>/doorbell/set`).
- Home Assistant: MQTT discovery of Doorbell and Call result event entities,
  Call active binary sensor, Unlock button, learn-mode sensor.
- Unlock through the official API (`PUT /doors/:id/unlock`) with the family
  shown as actor in the Access logs; retained commands are ignored and
  cleared; cooldown.
- TLS certificate pinning for the console, automatic reconnection of the
  Access WebSocket (with heartbeat) and of MQTT; stale rings are never
  delivered late.
- Home Assistant add-on and standalone Docker image; per-family Docker stack
  (Mosquitto + router + Homebridge).
- No third-party runtime dependency (Python 3.11+ standard library).
