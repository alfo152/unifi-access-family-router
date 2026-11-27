# Apple Home (HomeKit)

Each family pairs **its own Homebridge** with **its own Apple Home**. Live
video, two-way audio and the unlock tile come from
[homebridge-unifi-protect](https://github.com/hjdhjd/homebridge-unifi-protect)
(HBUP, version 8.0 or later, Homebridge 2.0). This project decides *when*
that family's doorbell rings.

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

## 3. Ringing one family only

HBUP 8.0+ turns every press of an Access intercom into a HomeKit ring, so in
a multi-family building every family would ring.

> **Checked in the HBUP 8.4.0 source code** (`src/nvr/event-dispatch.ts`):
> the automatic intercom ring and the MQTT `doorbell/set` trigger both go
> through the same `doorbellEventHandler`, which skips the HomeKit ring when
> `Doorbell.Mute` is on. So with HBUP alone you get either every ring or
> none: the "mute + MQTT trigger" idea of earlier versions of this guide
> does **not** work. A small HBUP option (ignore automatic Access rings, keep
> MQTT/Trigger rings) has been requested from the author; this guide will be
> updated if it arrives.

### Ring through the Home Assistant HomeKit Bridge (works today)

Home Assistant exposes the G3 camera to Apple Home with this project's
**Doorbell** event entity as `linked_doorbell_sensor`
([`examples/ha-homekit-bridge.yaml`](../examples/ha-homekit-bridge.yaml)).
Only this family's calls fire that event, so only this family rings, with a
snapshot in the notification and live video.

The Home Assistant bridge does not support talking back, so keep the HBUP
intercom tile **muted** (step 2) to **talk** and to **unlock**: one tile
rings, the other one talks.

**Test (2 minutes):** call another family from the intercom → nothing rings
in your Apple Home. Call your family → the router logs `Ring … for family …`
and the bridge camera rings in the Home app.

## 4. Talking and unlocking

- **Talk**: open the intercom tile in the Home app and press the microphone.
  HBUP has no echo cancellation: you hear your own voice, the visitor hears
  you normally. Two-way audio depends on Protect exposing talkback for the G3
  Intercom; check it during the test.
- **Unlock**: HBUP adds a lock accessory that can unlock Access doors on the
  same console (unlock only: the lock closes again by itself). The Home
  Assistant Unlock button of this project is an alternative.
