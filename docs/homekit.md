# Apple Home (HomeKit)

🇮🇹 [Leggi in italiano](it/homekit.md)

Each family uses **its own Apple Home**, paired only with its own Home
Assistant and its own Homebridge. Two pieces work together:

| Piece | Gives | Guide |
| --- | --- | --- |
| **Home Assistant HomeKit Bridge** | Doorbell that rings **only for this family**, snapshot, live video, one lock tile per door | [homekit-bridge.md](homekit-bridge.md) |
| **homebridge-unifi-protect** (HBUP 8.0+, Homebridge 2.0), tile kept **muted** | **Talk** to the visitor (two-way audio) | this page, steps 1–2 |

## 1. Install Homebridge and the plugin

- Docker stack: `stack/docker-compose.yml` starts Homebridge with host
  networking (needed by HomeKit). Open the Homebridge UI on port 8581.
- Install **homebridge-unifi-protect** from the Plugins tab.
- Add the controller (or paste
  [`stack/homebridge/unifi-protect-platform.example.json`](../stack/homebridge/unifi-protect-platform.example.json)
  in the JSON config):
  - address: the console IP;
  - username/password: this family's local user (see
    [unifi-setup.md](unifi-setup.md));
  - **MQTT URL**: the family broker, e.g. `mqtt://homebridge:PASSWORD@127.0.0.1:1883`
    in the stack;
  - **MQTT topic**: `unifi/protect` (must equal `homebridge.topic_prefix`).

## 2. Feature options (plugin web UI → Feature Options)

- Show **only the G3 Intercom**: turn the *Device* option off globally and on
  for the intercom (the most specific setting wins).
- Keep **Audio → Two-way audio** enabled (default).
- Enable **Doorbell → Mute** (`Doorbell.Mute`) and turn its switch **on** in
  the Home app, so the HBUP tile never rings by itself (see step 3).

Pair the Homebridge bridge with **this family's** Apple Home only (QR code in
the Homebridge UI).

## 3. Why the ring goes through Home Assistant

HBUP 8.0+ turns every press of an Access intercom into a HomeKit ring, so in
a multi-family building every family would ring.

> **Checked in the HBUP 8.4.0 source code** (`src/nvr/event-dispatch.ts`):
> the automatic intercom ring and the MQTT `doorbell/set` trigger both go
> through the same `doorbellEventHandler`, which skips the HomeKit ring when
> `Doorbell.Mute` is on. So with HBUP alone you get either every ring or
> none. A small HBUP option (ignore automatic Access rings, keep MQTT/Trigger
> rings) has been requested from the author
> ([hjdhjd/homebridge-unifi-protect#1363](https://github.com/hjdhjd/homebridge-unifi-protect/issues/1363)).
> If it arrives, one HBUP tile will do everything: the router already
> publishes `doorbell/set` for this family only.

The Home Assistant HomeKit Bridge works **today**: it exposes the G3 camera
with this project's **Doorbell** event entity as `linked_doorbell_sensor`.
Only this family's calls fire that event, so only this family rings.
Step-by-step guide and ready-to-copy package:
**[homekit-bridge.md](homekit-bridge.md)**.

## 4. Talking and unlocking

- **Talk**: open the muted HBUP intercom tile in the Home app and press the
  microphone. HBUP has no echo cancellation: you hear your own voice, the
  visitor hears you normally. Two-way audio depends on Protect exposing
  talkback for the G3 Intercom; check it during the test. The Home Assistant
  bridge camera does not talk back.
- **Unlock**: use the lock tiles of the Home Assistant bridge (one per door,
  Face ID / Touch ID required, the Access log shows this family's name).
  HBUP also adds a lock accessory for Access doors on the same console; keep
  only one of the two to avoid duplicate tiles.
