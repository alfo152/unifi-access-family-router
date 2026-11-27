# Changelog

## 0.2.0 - 2026-11-27

### English

- **Several doors per family** (`unlock.doors`, up to 10): one *Unlock*
  button and one **lock** entity per door, e.g. *Portone*, *Cancello*,
  *Garage*. Add-on format: `Name=door_id`; YAML/Docker: `{name, door_id}`.
  The older `unlock.door_id` keeps working with the same entities.
- The lock shows *unlocked* after opening, *locked* again after
  `unlock.relock_seconds` (default 5), *jammed* if the console refused.
  New MQTT topics `<base>/unlock/<door>/set|state|result`; `LOCK` only
  refreshes the state. Cooldown is per door.
- Entities of removed doors (and of the old single door) are removed from
  Home Assistant automatically at start.
- Learn mode and `--list-doors` print a ready `unlock.doors` line for each
  door; `--check` lists the configured doors.
- Family names with accents give clean IDs (*Città* → `citta`).
- **Apple Home through the Home Assistant HomeKit Bridge**: step-by-step
  guide (`docs/homekit-bridge.md`) and ready-to-copy package
  (`examples/packages/intercom_family.yaml`): doorbell that rings only for
  this family, live video, lock tiles with Face ID; the
  homebridge-unifi-protect tile stays muted for talking. `Doorbell.Mute`
  also silences MQTT-triggered rings (checked in the HBUP 8.4.0 source);
  an option was requested in hjdhjd/homebridge-unifi-protect#1363.
- `tools/watch_calls.py`: standalone, read-only check (Python 3.8+, standard
  library only) that prints the `room_id` of every intercom call and says
  whether different directory entries produce different `room_id`s.
- Documentation in **English and Italian** (`docs/` and `docs/it/`, add-on
  documentation, issue forms). Network docs list TCP 7446 for HBUP 8.4+
  direct livestreams.

### Italiano

- **Più porte per famiglia** (`unlock.doors`, fino a 10): un pulsante
  *Unlock* e una **serratura** per ogni porta, per esempio *Portone*,
  *Cancello*, *Garage*. Formato dell'add-on: `Nome=door_id`; YAML/Docker:
  `{name, door_id}`. La vecchia opzione `unlock.door_id` continua a
  funzionare con le stesse entità.
- La serratura mostra *aperta* dopo l'apertura, di nuovo *chiusa* dopo
  `unlock.relock_seconds` (di base 5), *bloccata* se la console ha
  rifiutato. Nuovi topic MQTT `<base>/unlock/<porta>/set|state|result`;
  `LOCK` aggiorna solo lo stato. L'attesa tra due aperture vale per porta.
- Le entità delle porte tolte (e della vecchia porta singola) spariscono da
  sole da Home Assistant all'avvio.
- La modalità apprendimento e `--list-doors` scrivono una riga
  `unlock.doors` pronta per ogni porta; `--check` elenca le porte
  configurate.
- I nomi di famiglia con accenti danno ID puliti (*Città* → `citta`).
- **Apple Casa tramite il HomeKit Bridge di Home Assistant**: guida passo
  passo (`docs/it/homekit-bridge.md`) e pacchetto pronto da copiare
  (`examples/packages/intercom_family.yaml`): campanello che squilla solo
  per questa famiglia, video dal vivo, serrature con Face ID; il riquadro di
  homebridge-unifi-protect resta silenzioso e serve per parlare.
  `Doorbell.Mute` zittisce anche gli squilli via MQTT (verificato nel codice
  di HBUP 8.4.0); è stata chiesta un'opzione in
  hjdhjd/homebridge-unifi-protect#1363.
- `tools/watch_calls.py`: verifica autonoma che legge soltanto (Python 3.8+,
  solo libreria standard): scrive il `room_id` di ogni chiamata e dice se
  voci diverse della rubrica danno `room_id` diversi.
- Documentazione in **inglese e italiano** (`docs/` e `docs/it/`,
  documentazione dell'add-on, moduli delle issue). La guida di rete indica
  la porta TCP 7446 per il video diretto di HBUP 8.4+.

## 0.1.1 - 2026-11-26

- Add-on: without the Mosquitto broker the log no longer shows the
  Supervisor's "Service not enabled" ERROR; a clear warning explains how to
  add a broker instead.
- README/LEGGIMI: one-click "Add repository" button and tester call.
- GitHub issue forms for test reports and bugs.

## 0.1.0 - 2026-11-26

First release.

- Per-family routing of UniFi Access intercom calls using the `room_id` of
  the called directory entry (official WebSocket API, `access.remote_view`).
- Optional matching on `door_guard_ids` and intercom `device_ids`; `ring_all`
  for single-family buildings; learn mode to discover the IDs.
- Apple Home: triggers the doorbell of homebridge-unifi-protect through MQTT
  (`<prefix>/<MAC>/doorbell/set`).
- Home Assistant: MQTT discovery of Doorbell and Call result event entities,
  Call active binary sensor, Unlock button, learn-mode sensor.
- Unlock through the official API (`PUT /doors/:id/unlock`) with the family
  shown as actor in the Access logs; retained commands are ignored and
  cleared; cooldown.
- TLS certificate pinning for the console, automatic reconnection of the
  Access WebSocket (with heartbeat) and of MQTT; stale rings are never
  delivered late.
- Home Assistant add-on and standalone Docker image; per-family Docker stack
  (Mosquitto + router + Homebridge).
- No third-party runtime dependency (Python 3.11+ standard library).
