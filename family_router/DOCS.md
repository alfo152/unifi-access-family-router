# UniFi Access Family Router (add-on)

🇮🇹 *La versione italiana è più sotto.*

Rings **only this family** when a visitor calls its entry in the directory of
a UniFi Access intercom (G3 Intercom). Install the add-on in **each family's
own** Home Assistant.

## Before you start

The console administrator gives each family:

- the console IP;
- an Access **API token** for this family (`view:device`, plus `view:space` and
  `edit:space` if you want to open doors);
- the **MAC** of the G3 Intercom (only for homebridge-unifi-protect).

Details: <https://github.com/alfo152/unifi-access-family-router/blob/main/docs/unifi-setup.md>

Install the **Mosquitto broker** add-on and the MQTT integration first.

## First start (learn mode)

1. Fill in `access.host`, `access.token` and `family.name`. Leave
   `learn_mode` on and `family.room_ids` empty.
2. Start the add-on and open the **Log** tab.
3. Call each directory entry once from the intercom. The log shows:

   ```text
   LEARN: ring on … room_id=WR-… -> no match
   LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-… (add-on: unlock.doors "Portone=0ed545f8-…")
   ```

4. Put **your** family's `room_id` in `family.room_ids`.
5. To open doors: turn `unlock.enabled` on and add one line per door to
   `unlock.doors`, copied from the log, e.g. `Portone=0ed545f8-…`,
   `Cancello=a8f2c6d1-…` (up to 10).
6. Turn `learn_mode` off, restart. From now on only your calls ring.

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
| `homebridge.enabled` / `camera_mac` / `topic_prefix` | Trigger the homebridge-unifi-protect doorbell |
| `unlock.enabled` | One Unlock button and one lock per door |
| `unlock.doors` | One `Name=door_id` line per door |
| `unlock.door_id` | Older single-door option (leave empty when you use `doors`) |
| `unlock.actor_name` / `cooldown_seconds` | Name in the Access logs / ignore repeated presses |
| `unlock.relock_seconds` | Seconds the lock shows *unlocked* before *locked* again |
| `call_timeout_seconds` | Close a call without end event after this time |

## Entities

Device **Intercom - *family name***: *Doorbell* (event), *Call result*
(event), *Call active* (binary sensor), per door a button and a lock (e.g.
*Unlock Portone* and *Portone*), *Last ring room_id* (learn mode).

## Apple Home

Ring, live video and one lock tile per door through the Home Assistant
HomeKit Bridge, with a ready-to-copy package:
<https://github.com/alfo152/unifi-access-family-router/blob/main/docs/homekit-bridge.md>

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

---

<!-- markdownlint-disable-next-line MD025 -->
# 🇮🇹 Italiano

Fa squillare **solo questa famiglia** quando un visitatore chiama la sua voce
nella rubrica del citofono UniFi Access (G3 Intercom). Installa l'add-on nel
Home Assistant **di ogni famiglia**.

## Prima di iniziare

Chi gestisce la console dà a ogni famiglia:

- l'IP della console;
- un **token API** di Access per questa famiglia (`view:device`, più
  `view:space` ed `edit:space` se vuoi aprire le porte);
- il **MAC** del G3 Intercom (solo per homebridge-unifi-protect).

Dettagli: <https://github.com/alfo152/unifi-access-family-router/blob/main/docs/it/unifi-setup.md>

Prima installa l'add-on **Mosquitto broker** e l'integrazione MQTT.

## Primo avvio (modalità apprendimento)

1. Compila `access.host`, `access.token` e `family.name`. Lascia acceso
   `learn_mode` e vuoto `family.room_ids`.
2. Avvia l'add-on e apri la scheda **Registro**.
3. Dal citofono chiama una volta ogni voce della rubrica. Il registro mostra:

   ```text
   LEARN: ring on … room_id=WR-… -> no match
   LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-… (add-on: unlock.doors "Portone=0ed545f8-…")
   ```

4. Metti il `room_id` della **tua** famiglia in `family.room_ids`.
5. Per aprire le porte: accendi `unlock.enabled` e aggiungi in
   `unlock.doors` una riga per porta, copiata dal registro, per esempio
   `Portone=0ed545f8-…`, `Cancello=a8f2c6d1-…` (fino a 10).
6. Spegni `learn_mode` e riavvia. Da ora squillano solo le tue chiamate.

## Opzioni

| Opzione | Descrizione |
| --- | --- |
| `access.host` / `access.port` | IP della console / `12445` |
| `access.token` | Token API di questa famiglia |
| `access.tls_fingerprint` | SHA-256 del certificato della console (consigliato) |
| `family.name` / `family.id` | Nome / id nei topic (ricavato dal nome se vuoto) |
| `family.room_ids` | `room_id` delle voci di questa famiglia (dalla modalità apprendimento) |
| `family.door_guard_ids` / `family.device_ids` | Regole extra facoltative |
| `family.ring_all` | Squilla a ogni chiamata (solo palazzo con una famiglia) |
| `mqtt.*` | Lascia vuoto `host` per usare il broker di Home Assistant |
| `homebridge.enabled` / `camera_mac` / `topic_prefix` | Fa squillare il campanello di homebridge-unifi-protect |
| `unlock.enabled` | Un pulsante Apri e una serratura per porta |
| `unlock.doors` | Una riga `Nome=door_id` per porta |
| `unlock.door_id` | Vecchia opzione a porta singola (lasciala vuota se usi `doors`) |
| `unlock.actor_name` / `cooldown_seconds` | Nome nel registro di Access / ignora le pressioni ripetute |
| `unlock.relock_seconds` | Secondi in cui la serratura resta *aperta* prima di tornare *chiusa* |
| `call_timeout_seconds` | Chiude una chiamata senza evento di fine dopo questo tempo |

## Entità

Dispositivo **Intercom - *nome famiglia***: *Doorbell* (evento campanello),
*Call result* (evento esito), *Call active* (chiamata in corso), per ogni
porta un pulsante e una serratura (per esempio *Unlock Portone* e
*Portone*), *Last ring room_id* (solo modalità apprendimento).

## Apple Casa

Squillo, video dal vivo e una serratura per porta tramite il HomeKit Bridge
di Home Assistant, con un pacchetto pronto da copiare:
<https://github.com/alfo152/unifi-access-family-router/blob/main/docs/it/homekit-bridge.md>

## Impronta del certificato TLS

Da un computer con Python 3.11+ e una copia del progetto:

```bash
python -m family_router --fingerprint 192.168.50.1
```

(eseguilo dalla cartella `family_router/app` del progetto).

Senza Python lo stesso valore si legge con OpenSSL (accettato così com'è):

```bash
openssl s_client -connect 192.168.50.1:12445 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
```
