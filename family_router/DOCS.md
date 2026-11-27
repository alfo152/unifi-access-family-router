# UniFi Access Family Router (add-on)

Rings **only this family** when a visitor calls its entry in the directory of
a UniFi Access intercom (G3 Intercom). Install the add-on in **each family's
own** Home Assistant.

## Before you start

The console administrator gives each family:

- the console IP;
- an Access **API token** for this family (`view:device`, plus `view:space` and
  `edit:space` if you want the Unlock button);
- the **MAC** of the G3 Intercom (only for Apple Home through Homebridge).

Details: <https://github.com/alfo152/unifi-access-family-router/blob/main/docs/unifi-setup.md>

Install the **Mosquitto broker** add-on and the MQTT integration first.

## First start (learn mode)

1. Fill in `access.host`, `access.token` and `family.name`. Leave
   `learn_mode` on and `family.room_ids` empty.
2. Start the add-on and open the **Log** tab.
3. Call each directory entry once from the intercom. The log shows:

   ```text
   LEARN: ring on … room_id=WR-… -> no match
   LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-…
   ```

4. Put **your** family's `room_id` in `family.room_ids`, turn `learn_mode`
   off, restart. From now on only your calls ring.

## Options

| Option | Description |
| --- | --- |
| `access.host` / `access.port` | Console IP / `12445` |
| `access.token` | This family's API token |
| `access.tls_fingerprint` | SHA-256 of the console certificate (recommended) |
| `family.name` / `family.id` | Name / topic id (derived from the name if empty) |
| `family.room_ids` | `room_id`s of this family's entries (from learn mode) |
| `family.door_guard_ids` / `family.device_ids` | Optional extra rules |
| `family.ring_all` | Ring on every call (single-family building only) |
| `mqtt.*` | Leave `host` empty to use the Home Assistant broker |
| `homebridge.enabled` / `camera_mac` / `topic_prefix` | Ring Apple Home through homebridge-unifi-protect |
| `unlock.enabled` / `door_id` / `actor_name` | Unlock button in Home Assistant |
| `call_timeout_seconds` | Close a call without end event after this time |

## Entities

Device **Intercom - *family name***: *Doorbell* (event), *Call result*
(event), *Call active* (binary sensor), *Unlock* (button), *Last ring room_id*
(learn mode). Examples of dashboard, notification and HomeKit Bridge:
<https://github.com/alfo152/unifi-access-family-router/tree/main/examples>

## Apple Home

See <https://github.com/alfo152/unifi-access-family-router/blob/main/docs/homekit.md>.

## Find the TLS fingerprint

From any computer with Python 3.11+ and a copy of the repository:

```bash
python -m family_router --fingerprint 192.168.50.1
```

(run it from the `family_router/app` folder of the repository).

Without Python, the same value can be read with OpenSSL (accepted as is):

```bash
openssl s_client -connect 192.168.50.1:12445 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
```
