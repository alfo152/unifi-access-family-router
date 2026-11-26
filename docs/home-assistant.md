# Home Assistant

## 1. Connect to the family broker

- **Add-on**: leave `mqtt.host` empty to use the Home Assistant broker
  (Mosquitto add-on). The add-on asks the Supervisor for the credentials.
- **Docker stack**: add the MQTT integration in Home Assistant pointing to the
  family's Mosquitto (user `homeassistant`).

## 2. Entities created (MQTT discovery)

Device **Intercom - <family name>**. Entity IDs depend on the names; with the
family "Famiglia Rossi" they are typically:

| Entity | Meaning |
| --- | --- |
| `event.intercom_famiglia_rossi_doorbell` | Fires `ring` when **this family** is called |
| `event.intercom_famiglia_rossi_call_result` | `answered_elsewhere`, `unlocked`, `rejected`, `canceled`, `timeout`, `expired`, `unknown` |
| `binary_sensor.intercom_famiglia_rossi_call_active` | On while the call is ringing |
| `button.intercom_famiglia_rossi_unlock` | Opens the door (only if `unlock.enabled`) |
| `sensor.intercom_famiglia_rossi_last_ring_room_id` | Learn mode only |

Check the real IDs in *Settings → Devices & services → MQTT*.

## 3. Video

Add the official **UniFi Protect** integration with this family's local user:
the G3 Intercom appears as a camera (e.g. `camera.g3_intercom_high`).

## 4. Examples

- Dashboard (live view during a call + Unlock button):
  [`examples/ha-dashboard.yaml`](../examples/ha-dashboard.yaml)
- Phone notification with an **Open** action:
  [`examples/ha-automations.yaml`](../examples/ha-automations.yaml)
- Apple Home through the HomeKit Bridge (option B):
  [`examples/ha-homekit-bridge.yaml`](../examples/ha-homekit-bridge.yaml)

## 5. Learn mode in the add-on

Turn on `learn_mode`, start the add-on, call each directory entry once and
open the add-on **Log**:

```text
LEARN: ring on Door 236b (e4388386be1d) room_id=WR-… door_guard_ids=… -> no match
LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-…
```

Copy this family's `room_id` into `family.room_ids` (and the `door_id` into
`unlock.door_id` if you want the Unlock button), turn `learn_mode` off and
restart the add-on.
