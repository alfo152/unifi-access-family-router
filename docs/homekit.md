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
- Enable **Doorbell → Mute** (`Doorbell.Mute`): it adds a switch that decides
  whether HomeKit chimes when the doorbell is pressed.

Pair the Homebridge bridge with **this family's** Apple Home only (QR code in
the Homebridge UI).

## 3. Ringing one family only

HBUP 8.0+ turns every press of an Access intercom into a HomeKit ring. In a
multi-family building that would ring every family. The router therefore:

1. leaves the automatic ring **muted** (switch from step 2 turned **on** in
   the Home app);
2. publishes `true` on `unifi/protect/<G3 MAC>/doorbell/set` only for this
   family's calls; HBUP documents this MQTT topic as "trigger the doorbell to
   generate a ring event".

**Test (2 minutes):** with the mute switch on, call another family from the
intercom → nothing must ring. Call your family → the router logs
`Ring … for family …` and the Home app must ring.

- ✅ It rings: done, use this setup (option A).
- ❌ It does not ring: in your HBUP version the mute switch also silences
  MQTT-triggered rings. Use option B.

### Option B: ring through the Home Assistant HomeKit Bridge

Home Assistant exposes the G3 camera to Apple Home with this project's
**Doorbell** event entity as `linked_doorbell_sensor`
([`examples/ha-homekit-bridge.yaml`](../examples/ha-homekit-bridge.yaml)).
Only this family's calls fire that event, so only this family rings, with a
snapshot in the notification. The Home Assistant bridge does not support
talking back, so keep the HBUP intercom tile (still muted) to **talk** and to
**unlock**.

## 4. Talking and unlocking

- **Talk**: open the intercom tile in the Home app and press the microphone.
  HBUP has no echo cancellation: you hear your own voice, the visitor hears
  you normally. Two-way audio depends on Protect exposing talkback for the G3
  Intercom; check it during the test.
- **Unlock**: HBUP adds a lock accessory that can unlock Access doors on the
  same console (unlock only: the lock closes again by itself). The Home
  Assistant Unlock button of this project is an alternative.
