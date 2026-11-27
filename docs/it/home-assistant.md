# Home Assistant

🇬🇧 [Read in English](../home-assistant.md)

## 1. Collegamento al broker della famiglia

- **Add-on**: lascia vuoto `mqtt.host` per usare il broker di Home Assistant
  (add-on Mosquitto). L'add-on chiede le credenziali al Supervisor.
- **Pacchetto Docker**: aggiungi in Home Assistant l'integrazione MQTT che
  punta al Mosquitto della famiglia (utente `homeassistant`).

## 2. Entità create (MQTT discovery)

Dispositivo **Intercom - *nome famiglia***. Gli ID dipendono dai nomi; con la
famiglia "Famiglia Rossi" e le porte `Portone` e `Cancello` di solito sono:

| Entità | Significato |
| --- | --- |
| `event.intercom_famiglia_rossi_doorbell` | Scatta `ring` quando chiamano **questa famiglia** |
| `event.intercom_famiglia_rossi_call_result` | Esito: `answered_elsewhere` (risposto altrove), `unlocked` (aperto), `rejected` (rifiutata), `canceled` (annullata), `timeout`, `expired` (scaduta), `unknown` |
| `binary_sensor.intercom_famiglia_rossi_call_active` | Acceso mentre la chiamata squilla |
| `button.intercom_famiglia_rossi_unlock_portone` | Apre il *Portone* (un pulsante per porta, solo con `unlock.enabled`) |
| `lock.intercom_famiglia_rossi_portone` | La stessa porta come serratura: *chiusa*, *aperta* per `relock_seconds` dopo l'apertura, *bloccata* se la console ha rifiutato. Per Apple Casa |
| `sensor.intercom_famiglia_rossi_last_ring_room_id` | Solo in modalità apprendimento |

Con la vecchia opzione a porta singola `unlock.door_id` le entità sono
`button.intercom_famiglia_rossi_unlock` e `lock.intercom_famiglia_rossi_door`.
Le entità delle porte tolte dalla configurazione spariscono al successivo
avvio del router.

Controlla gli ID veri in *Impostazioni → Dispositivi e servizi → MQTT*.

## 3. Porte

La modalità apprendimento scrive una riga pronta per ogni porta della
console:

```text
LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-… (add-on: unlock.doors "Portone=0ed545f8-…")
```

Add-on (sezione `unlock`):

```yaml
enabled: true
doors:
  - Portone=0ed545f8-…
  - Cancello=a8f2c6d1-…
```

Docker / riga di comando (`config.yaml`):

```yaml
unlock:
  enabled: true
  doors:
    - name: Portone
      door_id: 0ed545f8-…
    - name: Cancello
      door_id: a8f2c6d1-…
```

Fino a 10 porte. Il nome entra negli ID delle entità (senza accenti:
*Città* → `citta`).

## 4. Video

Aggiungi l'integrazione ufficiale **UniFi Protect** con l'utente locale di
questa famiglia: il G3 Intercom compare come telecamera (per esempio
`camera.g3_intercom_high`).

## 5. Esempi

- **Apple Casa** (squillo solo per la famiglia, serrature): pacchetto
  [`examples/packages/intercom_family.yaml`](../../examples/packages/intercom_family.yaml),
  guida [homekit-bridge.md](homekit-bridge.md)
- Schermata (video durante la chiamata + un pulsante per porta):
  [`examples/ha-dashboard.yaml`](../../examples/ha-dashboard.yaml)
- Notifica sul telefono con il tasto **Apri**:
  [`examples/ha-automations.yaml`](../../examples/ha-automations.yaml)

## 6. Modalità apprendimento nell'add-on

Accendi `learn_mode`, avvia l'add-on, chiama una volta ogni voce della
rubrica e apri il **Registro** dell'add-on:

```text
LEARN: ring on Door 236b (e4388386be1d) room_id=WR-… door_guard_ids=… -> no match
LEARN: door 'UCG-Max - GF - Portone' -> door_id 0ed545f8-… (add-on: unlock.doors "Portone=0ed545f8-…")
```

Copia il `room_id` di questa famiglia in `family.room_ids` (e le porte in
`unlock.doors` se vuoi aprirle), spegni `learn_mode` e riavvia l'add-on.
