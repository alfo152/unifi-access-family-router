# UniFi setup

Done once, by whoever manages the shared console in the stairwell.

## 1. Console and devices

1. Install **UniFi Access** and **UniFi Protect** on the console (Protect needs
   storage, e.g. a UCG-Max with an SSD).
2. Adopt the **G3 Intercom** and the door hub (e.g. **Door Hub Mini**) in Access.
3. Wire the lock to the hub relay. A motorised lock for an armoured door
   normally has its own control unit with a dry-contact input: connect it to
   the hub's dry relay output and check the voltage/current limits of the hub
   in Ubiquiti's documentation.
4. Make sure the G3 Intercom also appears as a camera in **UniFi Protect**
   (same console) and note its **MAC address** (`homebridge.camera_mac`).

## 2. Directory: one entry per family

Access → *Interface Designer* → select the G3 Intercom → *Directory*:

- create one entry per family (name and dial number);
- add that family's **receivers** to its entry only: its UniFi users (app),
  its Intercom Viewers, phone numbers if wanted.

UniFi itself then rings only the right family in the UniFi apps and on the
viewers. This project does the same for Apple Home and Home Assistant.

## 3. One API token per family

Access → *Settings* → *General* → *Advanced* → *API Token* → *Create New*.
The PDF of the official API reference can be downloaded from the same page.

| Token name | Permissions | Used for |
| --- | --- | --- |
| `family-router-a` | `view:device` | calls (required) |
| | + `view:space`, `edit:space` | only if family A gets the Unlock button |

Repeat for every family. The token is shown **once**: give it to that family
only. Revoke it if the family moves out.

## 4. One local user per family for Homebridge

UniFi OS → *Admins & Users* → add a user **restricted to local access**, one
per family (e.g. `homebridge-a`), used by that family's
homebridge-unifi-protect and Home Assistant UniFi Protect integration.

homebridge-unifi-protect documents that **Full Management** is needed for all
of its capabilities and that it works in a more limited form without it.
Start with the most limited role that gives live view, then check talk and
unlock in the Home app; raise the role only if something you need is missing.

## 5. Network

Each family's computer (Home Assistant / Docker host) must reach the console;
families must not reach each other.

- Put each family on its own network/VLAN (or a dedicated port of the console).
- Allow from each family network to the console only what is needed:
  TCP **12445** (Access API: notifications, doors, unlock) and the ports used
  by Protect clients (typically TCP **443**, and **7441** for RTSPS streams in
  Home Assistant).
- Block traffic between the family networks.

## 6. Certificate fingerprint

The console uses a self-signed certificate on port 12445. Pin it:

```bash
python -m family_router --fingerprint 192.168.50.1
```

Run it from a trusted network, then put the value in `access.tls_fingerprint`.

Without Python, the same value can be read with OpenSSL (accepted as is):

```bash
openssl s_client -connect 192.168.50.1:12445 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
```

If the console certificate is ever regenerated, the router refuses to connect
(log: `certificate fingerprint mismatch`): read the new fingerprint and
update the configuration.
