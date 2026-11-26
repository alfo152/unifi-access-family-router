# Test checklist on the real installation

Run it once after installing, with someone at the intercom. Tick every line
for **each** family.

## Preparation

- [ ] `python -m family_router --config config.yaml --check` (or the add-on
      log at start) shows `OK notifications WebSocket` for every family.
- [ ] `access.tls_fingerprint` is set (no "NOT verified" warning in the log).
- [ ] The MQTT broker refuses a client without password.

## Learn mode

- [ ] Each directory entry, called once, logs a **different** `room_id`.
- [ ] Each family has only its own `room_id` in `family.room_ids`.

If two entries show the same `room_id`, use `door_guard_ids` instead (they list
the call receivers) and report it in an issue.

## Calls

| # | Action at the intercom | Expected |
| --- | --- | --- |
| 1 | Call family A | Only A: Apple Home rings with live video, HA *Doorbell* fires, *Call active* on. B and C: nothing. UniFi app/viewers: only A's receivers. |
| 2 | Repeat for B and C | Same, each family alone |
| 3 | Answer and unlock from the UniFi app | HA *Call result* = `unlocked`, *Call active* off |
| 4 | Let it ring without answering | *Call result* = `timeout` (or `expired` after `call_timeout_seconds`) |
| 5 | Visitor cancels | *Call result* = `canceled` |

## Apple Home

- [ ] Ring with the mute switch on (option A) works only for your family —
      otherwise switch to option B ([homekit.md](homekit.md)).
- [ ] Talk: the visitor hears you (you will hear your own echo).
- [ ] Unlock from the intercom tile opens the door.

## Home Assistant

- [ ] Unlock button opens the door; the Access log shows the family name.
- [ ] Phone notification with *Apri* works from outside home (Companion app).

## Resilience

- [ ] Turn off family A's computer: B and C still work.
- [ ] Reboot the console: every router reconnects by itself (log
      `Connected to UniFi Access notifications`) within about a minute.
- [ ] Restart a family broker: the router reconnects and HA entities come
      back available.
