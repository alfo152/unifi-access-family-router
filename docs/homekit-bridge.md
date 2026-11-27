# Apple Home through the Home Assistant HomeKit Bridge

🇮🇹 [Leggi in italiano](it/homekit-bridge.md)

This is how each family gets, **today**, a doorbell in its own Apple Home that
rings only for its own calls, with a snapshot in the notification, live video
and one lock tile per door. Why this path and not homebridge-unifi-protect
alone: see [homekit.md](homekit.md).

What this family will see in the Home app:

| Tile | Comes from | What it does |
| --- | --- | --- |
| **Citofono** (video doorbell) | Home Assistant HomeKit Bridge | Rings only for this family's calls, snapshot in the notification, live video |
| **Portone**, **Cancello**… (locks) | Home Assistant HomeKit Bridge | Opens the door after Face ID / Touch ID; shows *unlocked* for a few seconds, then *locked* |
| **G3 Intercom** (muted) | homebridge-unifi-protect | **Talk** to the visitor (two-way audio) |

Do every step in **this family's** Home Assistant and Apple Home.

## Before you start

- The router runs for this family with `learn_mode` off and its `room_ids`
  set: Home Assistant shows the device **Intercom - *family name*** with the
  **Doorbell** event.
- The official **UniFi Protect** integration is set up with this family's
  local user, and the G3 Intercom camera is there (for example
  `camera.g3_intercom_high`). ⚠️ Still to be confirmed on real hardware:
  check that the G3 Intercom appears as a camera in this integration.
- Optional, for the lock tiles: `unlock.enabled: true` and the doors in
  `unlock.doors` in the router configuration.
- The iPhones and Home Assistant are on the same local network while pairing
  (each family's Home Assistant lives on that family's network, so this is
  the normal case).

## 1. Find the entity IDs

1. **Settings → Devices & services → Devices**, open **Intercom - *family name***.
2. Note the IDs (tap an entity → ⚙️):
   - Doorbell event, e.g. `event.intercom_famiglia_rossi_doorbell`;
   - one lock per door, e.g. `lock.intercom_famiglia_rossi_portone`.
3. **Settings → Entities**, search `intercom`: note the G3 camera, e.g.
   `camera.g3_intercom_high` (the high-quality stream).

## 2. Turn on packages (once)

Add these lines to `configuration.yaml` (File editor or Studio Code Server
app), then create the folder `/config/packages`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

If `homeassistant:` already exists, add only the `packages:` line under it.

## 3. Copy the package

Copy [`examples/packages/intercom_family.yaml`](../examples/packages/intercom_family.yaml)
to `/config/packages/intercom.yaml` and replace the IDs marked with `<<<`
with the ones from step 1. One lock line per door; delete the second block
if this family does not unlock doors. Each `name` must be 3–25 characters
long, and each `name` and `port` must differ from any other HomeKit Bridge
you already have.

## 4. Check and restart

**Developer tools → YAML → Check configuration**. If it says *Configuration
will not prevent Home Assistant from starting*, press **Restart**.

## 5. Pair with this family's Apple Home

1. After the restart Home Assistant shows two cards with a QR code and a PIN:
   **Citofono** and **Porte *family name***. If you do not see them, open the
   🔔 **Notifications**.
2. On an iPhone of this family: **Home app → + → Add accessory**, scan the
   **Citofono** code. Repeat with the doors code.
3. Choose the room, keep the suggested names or rename them.

## 6. Talk: keep the homebridge-unifi-protect tile muted

Follow steps 1 and 2 of [homekit.md](homekit.md): the G3 Intercom tile of
homebridge-unifi-protect stays in this family's Home with **Doorbell → Mute**
switched on. It never rings; open it to **talk**.

## 7. Test (5 minutes)

| # | Action | Expected |
| --- | --- | --- |
| 1 | Call **another** family from the intercom | Nothing in your Home app |
| 2 | Call **your** family | Doorbell notification with a snapshot |
| 3 | Tap the notification | Live video |
| 4 | Open the G3 Intercom (muted) tile, press the microphone | The visitor hears you |
| 5 | Unlock **Portone** in the Home app | Face ID, the door opens, *unlocked* then *locked* |
| 6 | Unlock a door whose `door_id` is wrong | The tile shows *jammed*, then *locked*; the router log explains why |

## Troubleshooting

- **Nothing rings**: in **Developer tools → States**, call your family and
  check that `event.…_doorbell` changes. If not, the problem is in the router
  (`room_ids`, learn mode), not in HomeKit.
- **It rings but there is no video**: try another stream of the camera, e.g.
  `camera.g3_intercom_medium`.
- **No QR code**: look in 🔔 **Notifications**.
- **Pairing fails**: iPhone and Home Assistant must be on the same network;
  UDP 5353 and TCP 21071/21072 must be allowed to Home Assistant.
- **Two tiles ring**: the homebridge-unifi-protect tile must have
  **Doorbell → Mute** on.
- **An old "Door" lock remains** after moving from `unlock.door_id` to
  `unlock.doors`: the router removes it at its next start; in Apple Home,
  remove the stale accessory once.
