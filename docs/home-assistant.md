# Home Assistant

🇮🇹 [Leggi in italiano](it/home-assistant.md)

## 1. Connect to the family broker

- **Add-on**: leave `mqtt.host` empty to use the Home Assistant broker
  (Mosquitto add-on). The add-on asks the Supervisor for the credentials.
- **Docker stack**: add the MQTT integration in Home Assistant pointing to the
  family's Mosquitto (user `homeassistant`).

## 2. Entities created (MQTT discovery)

Device **Intercom - *family name***. Entity IDs depend on the names; with the
family "Famiglia Rossi" and the doors `Portone` and `Cancello` they are
typically:

| Entity | Meaning |
| --- | --- |
| `event.intercom_famiglia_rossi_doorbell` | Fires `ring` when **this family** is called |
| `event.intercom_famiglia_rossi_call_result` | `answered_elsewhere`, `unlocked`, `rejected`, `canceled`, `timeout`, `expired`, `unknown` |
| `binary_sensor.intercom_famiglia_rossi_call_active` | On while the call is ringing |
| `button.intercom_famiglia_rossi_unlock_portone` | Opens *Portone* (one button per door, only if `unlock.enabled`) |
| `lock.intercom_famiglia_rossi_portone` | Same door as a lock: *locked*, *unlocked* for `relock_seconds` after opening, *jammed* if the console refused. For Apple Home |
| `sensor.intercom_famiglia_rossi_last_ring_room_id` | Learn mode only |

With the older single-door option `unlock.door_id` the entities are
`button.intercom_famiglia_rossi_unlock` and `lock.intercom_famiglia_rossi_door`.
Entities of doors you remove from the configuration disappear at the next
start of the router.

Check the real IDs in *Settings → Devices & services → MQTT*.

## 3. Doors

Learn mode prints one ready line per door of the console:

```text
LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-… (add-on: unlock.doors "Portone=0ed545f8-…")
```

Add-on (`unlock` section):

```yaml
enabled: true
doors:
  - Portone=0ed545f8-…
  - Cancello=a8f2c6d1-…
```

Docker / command line (`config.yaml`):

```yaml
unlock:
  enabled: true
  doors:
    - name: Portone
      door_id: 0ed545f8-…
    - name: Cancello
      door_id: a8f2c6d1-…
```

Up to 10 doors. The name becomes part of the entity IDs (accents are
removed: *Città* → `citta`).

## 4. Video

Add the official **UniFi Protect** integration with this family's local user:
the G3 Intercom appears as a camera (e.g. `camera.g3_intercom_high`).

## 5. Examples

- **Apple Home** (per-family ring, lock tiles): package
  [`examples/packages/intercom_family.yaml`](../examples/packages/intercom_family.yaml),
  guide [homekit-bridge.md](homekit-bridge.md)
- Dashboard (live view during a call + one button per door):
  [`examples/ha-dashboard.yaml`](../examples/ha-dashboard.yaml)
- Phone notification with an **Open** action:
  [`examples/ha-automations.yaml`](../examples/ha-automations.yaml)

## 6. Learn mode in the add-on

Turn on `learn_mode`, start the add-on, call each directory entry once and
open the add-on **Log**:

```text
LEARN: ring on Door 236b (e4388386be1d) room_id=WR-… door_guard_ids=… -> no match
LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-… (add-on: unlock.doors "Portone=0ed545f8-…")
```

Copy this family's `room_id` into `family.room_ids` (and the doors into
`unlock.doors` if you want to open them), turn `learn_mode` off and restart
the add-on.
