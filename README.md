# UniFi Access Family Router

🇮🇹 [Leggi in italiano](LEGGIMI.md)

**One UniFi G3 Intercom, several families, each with its own Home Assistant
and its own Apple Home: only the family the visitor called gets the ring.**

UniFi Access lets visitors pick a family from the intercom directory, but
smart-home integrations only learn that "someone rang". This project listens
to the **official UniFi Access API**, reads which directory entry was called
(`room_id`) and rings **only that family**:

- 🍎 **Apple Home**: doorbell with snapshot and live video plus one lock
  tile per door through the Home Assistant HomeKit Bridge
  ([ready-to-copy package](examples/packages/intercom_family.yaml)); talking
  through [homebridge-unifi-protect](https://github.com/hjdhjd/homebridge-unifi-protect)
- 🏠 **Home Assistant**, through MQTT discovery (doorbell event, call state,
  one unlock button and one lock per door: *Portone*, *Cancello*, *Garage*…)

Every family runs its **own copy**, with its own API token, its own MQTT
broker, its own Homebridge and its own Home Assistant. Nothing is shared
except the intercom and the UniFi console in the stairwell.

> **Status:** 0.2.0. Verified with an automated test suite built on the
> official UniFi Access API reference (v4.2.16) and a simulated 3-family
> building. Not yet confirmed on real G3 Intercom hardware: see
> [Known limitations](#known-limitations) and run the
> [test checklist](docs/testing-checklist.md) on your installation.

[![Open your Home Assistant instance and show the add add-on repository dialog with this repository pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Falfo152%2Funifi-access-family-router)

## 🧪 Testers wanted

Do you have a **G3 Intercom** (or Intercom) with **UniFi Access** and at
least two directory entries? Install the router, leave `learn_mode: true`,
call each entry once and
[send a test report](https://github.com/alfo152/unifi-access-family-router/issues/new?template=test_report.yml).

The single most useful answer: **does each directory entry show a different
`room_id` in the log?** With the default settings (no family IDs yet,
unlock off) the router only logs: nothing rings and no door is unlocked.
Remove tokens and passwords from anything you paste.

### Five-minute check without Home Assistant

No Home Assistant, Homebridge or MQTT? [`tools/watch_calls.py`](tools/watch_calls.py)
answers the same question with nothing but Python 3.8+ on a computer that
reaches the console. It only listens.

1. In UniFi Access: *Settings → General → Advanced → API Token*, create a
   token with only the **view:device** permission (it cannot open doors;
   delete it after the test).
2. Download and run the script:

   ```sh
   curl -O https://raw.githubusercontent.com/alfo152/unifi-access-family-router/main/tools/watch_calls.py
   python3 watch_calls.py CONSOLE_IP
   ```

   On Windows, use `py` instead of `python3`.

3. Paste the token when asked, call each directory entry once from the
   intercom, press **Ctrl+C** and share the summary it prints (no token
   inside).

---

## How it works

```text
                 Stairwell (shared)                      Family A (private)
  ┌───────────────────────────────────────┐   ┌──────────────────────────────────────┐
  │ G3 Intercom ── directory: A / B / C   │   │ family-router (token A)              │
  │      │                                │   │   room_id == A ? ──► MQTT broker A   │
  │ UniFi console (Access + Protect) ─────┼──►│                          │           │
  │   WebSocket access.remote_view        │   │   Home Assistant A ◄─────┘           │
  │   { room_id: "WR-…A…", request_id }   │   │   (doorbell event, doors)            │
  │                                       │   │    └─► HomeKit Bridge ─► Apple Home A│
  │                                       │   │        (ring, video, lock tiles)     │
  │                                       │   │   Homebridge A ─► Apple Home A (talk)│
  └───────────────────────────────────────┘   └──────────────────────────────────────┘
                                         (same for family B and family C)
```

1. A visitor selects a family in the G3 Intercom directory.
2. The console sends `access.remote_view` on the notifications WebSocket. It
   contains the `room_id` of the called entry (the webhook variant does not).
3. Each family's router compares the `room_id` with its own configuration.
   Only the matching router reacts:
   - fires the **Doorbell** event entity and turns **Call active** on in that
     family's Home Assistant; the Home Assistant HomeKit Bridge rings that
     family's Apple Home (notification with snapshot, live video);
   - optionally publishes `true` on `unifi/protect/<G3 MAC>/doorbell/set` for
     homebridge-unifi-protect (useful once
     [HBUP#1363](https://github.com/hjdhjd/homebridge-unifi-protect/issues/1363)
     is available, see [Known limitations](#known-limitations)).
4. When the call ends (answered, unlocked, canceled, timeout) Home Assistant
   receives a **Call result** event and **Call active** turns off.
5. **Unlock**: each door has a Home Assistant button and a lock (the lock is
   what Apple Home shows, with Face ID). Both call the official unlock
   endpoint; the Access log shows which family opened the door.

## Features

- Routing by `room_id` (directory entry), optional `door_guard_ids` and
  intercom `device_ids`; `ring_all` for single-family buildings.
- **Learn mode** to discover the `room_id` of each directory entry.
- Home Assistant MQTT discovery: `event` Doorbell (`device_class: doorbell`,
  usable as HomeKit `linked_doorbell_sensor`), `event` Call result,
  `binary_sensor` Call active, diagnostic learn sensor.
- **Several doors per family** (up to 10): one `button` and one `lock` each.
  The lock shows *unlocked* after opening, *locked* again after a few
  seconds, *jammed* if the console refused. Entities of removed doors are
  cleaned up automatically.
- Unlock with per-family actor name, per-door cooldown, retained commands
  ignored and cleared (they would otherwise open the door at every
  reconnect).
- **TLS pinning** of the console certificate (SHA-256 fingerprint).
- Automatic reconnection of the Access WebSocket (with heartbeat) and of MQTT,
  independent of each other. Rings queued during an MQTT outage are dropped
  if older than 15 s: nobody is rung minutes after the visitor left.
- Home Assistant **add-on**, standalone **Docker** image, per-family
  **docker compose stack** (Mosquitto + router + Homebridge).
- **No third-party runtime dependency** (Python 3.11+ standard library).

## Requirements

| Component | Notes |
| --- | --- |
| UniFi console with **UniFi Access** and **UniFi Protect** | e.g. UCG-Max (with storage for Protect) |
| **G3 Intercom** + door hub (e.g. Door Hub Mini) | Directory with one entry per family |
| Per family: a small always-on computer | Runs HA OS (add-on) or Docker (stack) |
| Per family: MQTT broker | Mosquitto add-on or the stack's Mosquitto |
| Per family: Home Assistant HomeKit Bridge | Apple Home ring and lock tiles ([guide](docs/homekit-bridge.md)) |
| Per family: Homebridge + homebridge-unifi-protect ≥ 8.0 | Talking from Apple Home (requires Homebridge 2.0) |
| Network | Each family reaches the console on TCP 12445 (Access API) and the ports used by Protect |

## Quick start

1. **UniFi setup** (directory, one API token and one local user per family,
   network isolation): [docs/unifi-setup.md](docs/unifi-setup.md)
2. **Install the router for each family**
   - Home Assistant OS: add this repository with the **Add repository**
     button above (or in *Settings → Add-ons → Add-on Store → ⋮ →
     Repositories*), install **UniFi Access Family Router**;
   - or Docker: copy [`stack/`](stack/) to the family's computer and follow
     the comments in [`stack/docker-compose.yml`](stack/docker-compose.yml).
3. **Find the TLS fingerprint** of the console and put it in
   `access.tls_fingerprint`:

   ```bash
   python -m family_router --fingerprint 192.168.50.1
   # Docker stack: docker compose run --rm router --fingerprint 192.168.50.1
   ```

4. **Learn mode**: start with `learn_mode: true`, call each directory entry
   once from the intercom, copy *your* family's `room_id` from the log (or the
   *Last ring room_id* sensor) into `family.match.room_ids`, then set
   `learn_mode: false`.
5. **Doors** (optional): learn mode also prints one ready line per door,
   e.g. `unlock.doors "Portone=0ed545f8-…"`; set `unlock.enabled: true`
   and list your doors ([details](docs/home-assistant.md#3-doors)).
6. **Apple Home**: [docs/homekit-bridge.md](docs/homekit-bridge.md) (ring
   and locks, ready-to-copy package) and [docs/homekit.md](docs/homekit.md)
   (talking)
7. **Home Assistant** (dashboard, notification with Unlock):
   [docs/home-assistant.md](docs/home-assistant.md)
8. **Run the checklist**: [docs/testing-checklist.md](docs/testing-checklist.md)

**Command line.** From a clone of the repository run `pip install .` once:
then `family-router …` is the same as `python -m family_router …`
(or use `PYTHONPATH=family_router/app python -m family_router …` without
installing). Inside the Docker stack use `docker compose run --rm router …`.

Check a configuration and the token at any time:

```bash
python -m family_router --config config.yaml --check
python -m family_router --config config.yaml --list-doors
# Docker stack:
docker compose run --rm router --config /config/config.yaml --check
```

## Configuration

Full example: [`config.example.yaml`](config.example.yaml). The add-on uses
the same options (match rules directly under `family`).

| Option | Default | Description |
| --- | --- | --- |
| `access.host` | – | IP or name of the UniFi console (no `https://`) |
| `access.port` | `12445` | Access API port |
| `access.token` | – | This family's API token. Env override: `FAMILY_ROUTER_ACCESS_TOKEN` |
| `access.tls_fingerprint` | `""` | SHA-256 of the console certificate (colons allowed). **Recommended** |
| `access.verify_ssl` / `access.ca_file` | `false` / `""` | CA verification instead of pinning |
| `family.name` / `family.id` | – | Display name / topic id (`a-z`, `0-9`, `_`; derived from the name if empty) |
| `family.match.room_ids` | `[]` | `room_id`s of this family's directory entries |
| `family.match.door_guard_ids` | `[]` | Ring if one of these IDs is in `door_guard_ids` |
| `family.match.device_ids` | `[]` | Only react to these intercom device IDs |
| `family.match.ring_all` | `false` | Ring on every call (single-family building) |
| `mqtt.host` / `mqtt.port` | – / `1883` | The family's broker. Add-on: empty = Home Assistant broker |
| `mqtt.username` / `mqtt.password` | `""` | Broker credentials |
| `mqtt.tls` / `mqtt.tls_insecure` | `false` | MQTT over TLS / skip its verification |
| `mqtt.base_topic` | `access_family_router` | Topic prefix |
| `mqtt.discovery` / `mqtt.discovery_prefix` | `true` / `homeassistant` | Home Assistant discovery |
| `homebridge.enabled` | `false` | Trigger the homebridge-unifi-protect doorbell |
| `homebridge.topic_prefix` | `unifi/protect` | = `mqttTopic` of the plugin |
| `homebridge.camera_mac` | – | MAC of the G3 Intercom in Protect (any format) |
| `unlock.enabled` | `false` | Create one Unlock button and one lock per door |
| `unlock.doors` | `[]` | Doors to open: `{name, door_id}` items, or `"Name=door_id"` strings (add-on). IDs from learn mode or `--list-doors`; up to 10 |
| `unlock.door_id` | – | Older single-door option (use either this or `unlock.doors`) |
| `unlock.actor_name` | `<name> (family router)` | Name shown in the Access logs |
| `unlock.cooldown_seconds` | `3` | Ignore repeated presses (per door) |
| `unlock.relock_seconds` | `5` | Seconds the lock shows *unlocked* before *locked* again (1–60) |
| `learn_mode` | `false` | Log/publish the IDs of every call |
| `call_timeout_seconds` | `60` | Close a call that never received an end event |
| `log_level` | `info` | `debug`, `info`, `warning`, `error` |

## MQTT topics

`<base>` = `access_family_router/<family.id>`

| Topic | Direction | Payload |
| --- | --- | --- |
| `<base>/status` | out, retained | `online` / `offline` (Last Will) |
| `<base>/ring` | out | `{"event_type":"ring","request_id","room_id","door_name","device_name"}` |
| `<base>/call` | out, retained | `ON` / `OFF` |
| `<base>/call_result` | out | `{"event_type":"answered_elsewhere\|unlocked\|rejected\|canceled\|timeout\|expired\|unknown","reason_code","request_id"}` |
| `<base>/learn` | out, retained | IDs of the last call (learn mode only) |
| `<base>/unlock/<door>/set` | in | `PRESS` or `UNLOCK` opens; `LOCK` only refreshes the state; retained messages are ignored and cleared |
| `<base>/unlock/<door>/state` | out, retained | `LOCKED` / `UNLOCKED` / `JAMMED` |
| `<base>/unlock/<door>/result` | out | `{"ok":true,"door":"portone","time":…}` or `{"ok":false,"door":…,"error":…}` |
| `<homebridge.topic_prefix>/<MAC>/doorbell/set` | out | `true` |

`<door>` is the door name in lower case without accents (`Cancello
carrabile` → `cancello_carrabile`). With the older `unlock.door_id` the door
topics are `<base>/unlock/set`, `<base>/unlock/state` and
`<base>/unlock/result`.

## Security

- **One API token per family**, with only the permissions it needs:
  `view:device` (notifications) and, only if unlock is wanted, `view:space` +
  `edit:space`. Never use an admin account.
- Each token can see the notification stream of the intercom (which entry was
  called and when), but each router only acts on its own calls.
- **Pin the console certificate** (`access.tls_fingerprint`).
- The unlock command travels over the family's broker: **disable anonymous
  access** and use one password per client (the stack does this).
- Isolate each family's network from the others; allow only the console
  ports they need ([docs/unifi-setup.md](docs/unifi-setup.md)).

## Known limitations

- **Real hardware**: the meaning of `room_id` is inferred from the official
  API reference (the *Trigger Doorbell* endpoint identifies a directory entry
  by `room_name`). Learn mode shows the real values; confirm on your G3
  Intercom that each entry has its own `room_id`. If it does not,
  `door_guard_ids` (the call receivers) is the fallback rule.
- **Apple Home, automatic rings**: homebridge-unifi-protect ≥ 8.0 rings
  HomeKit for *every* intercom press, and its `Doorbell.Mute` switch also
  silences MQTT-triggered rings (checked in the 8.4.0 source). Per-family
  rings in Apple Home therefore go through the Home Assistant HomeKit Bridge
  today ([docs/homekit-bridge.md](docs/homekit-bridge.md)), with the HBUP
  tile kept muted for talking. An HBUP option to ignore automatic Access
  rings has been requested
  ([homebridge-unifi-protect#1363](https://github.com/hjdhjd/homebridge-unifi-protect/issues/1363)).
- **G3 Intercom camera in Home Assistant**: the HomeKit Bridge path needs the
  G3 Intercom to appear as a camera in the official UniFi Protect
  integration; still to be confirmed on real hardware.
- **Two-way audio in Apple Home** is provided by homebridge-unifi-protect and
  depends on Protect exposing talkback for the G3 Intercom (a G3 owner
  confirmed Protect's native talkback session works). The plugin has no
  echo cancellation: you hear your own voice in the Home app.
- **Talking from a Home Assistant dashboard** is not part of this project.
- Each family's router must reach the console directly (TCP 12445).

## Development

```bash
python -m unittest discover -s tests -t .     # 3.11+, no dependency
python -m pytest                              # same tests with pytest
python -m mypy --strict family_router/app/family_router
```

The tests include: configuration validation, parsing of the official
payloads, routing for three families, MQTT encoding checked against the
MQTT 3.1.1 specification, the MQTT client against a test broker, the
WebSocket client against the Node.js `ws` library over TLS (fragmentation,
large frames, ping/pong, close codes, heartbeat, wrong token, certificate
pinning), the REST client, the service with reconnections, the command line,
and an end-to-end simulation of a 3-family building. The WebSocket and
end-to-end tests are skipped if `node` and the `ws` module are not available
(`WS_MODULE_PATH`).

## Disclaimer

Not affiliated with or endorsed by Ubiquiti Inc. UniFi, UniFi Access and
UniFi Protect are trademarks of Ubiquiti Inc. HomeKit and Apple Home are
trademarks of Apple Inc. Use at your own risk: this software can open a door.

## License

[MIT](LICENSE)
