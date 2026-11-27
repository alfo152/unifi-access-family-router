# Apple Casa tramite il HomeKit Bridge di Home Assistant

🇬🇧 [Read in English](../homekit-bridge.md)

Così ogni famiglia ha, **già oggi**, un campanello nella propria Apple Casa che
squilla solo per le proprie chiamate, con l'anteprima nella notifica, il video
dal vivo e una serratura per ogni porta. Perché questa strada e non solo
homebridge-unifi-protect: vedi [homekit.md](homekit.md).

Cosa vedrà questa famiglia nell'app Casa:

| Riquadro | Da dove arriva | Cosa fa |
| --- | --- | --- |
| **Citofono** (videocitofono) | HomeKit Bridge di Home Assistant | Squilla solo per le chiamate di questa famiglia, anteprima nella notifica, video dal vivo |
| **Portone**, **Cancello**… (serrature) | HomeKit Bridge di Home Assistant | Apre la porta dopo Face ID / Touch ID; mostra *aperta* per qualche secondo, poi *chiusa* |
| **G3 Intercom** (silenzioso) | homebridge-unifi-protect | **Parlare** con chi ha suonato (audio nei due sensi) |

Fai ogni passaggio nel Home Assistant e nell'Apple Casa di **questa famiglia**.

## Prima di iniziare

- Il router è attivo per questa famiglia, con `learn_mode` spento e i suoi
  `room_ids` impostati: Home Assistant mostra il dispositivo
  **Intercom - *nome famiglia*** con l'evento **Doorbell** (campanello).
- È configurata l'integrazione ufficiale **UniFi Protect** con l'utente
  locale di questa famiglia, e c'è la telecamera del G3 Intercom (per esempio
  `camera.g3_intercom_high`). ⚠️ Da confermare su un G3 vero: controlla che
  il G3 Intercom compaia come telecamera in questa integrazione.
- Facoltativo, per le serrature: `unlock.enabled: true` e le porte in
  `unlock.doors` nella configurazione del router.
- Durante l'abbinamento gli iPhone e Home Assistant sono nella stessa rete
  locale (il Home Assistant di ogni famiglia sta nella rete di quella
  famiglia, quindi è il caso normale).

## 1. Trova gli ID delle entità

1. **Impostazioni → Dispositivi e servizi → Dispositivi**, apri
   **Intercom - *nome famiglia***.
2. Annota gli ID (tocca un'entità → ⚙️):
   - evento Campanello, per esempio `event.intercom_famiglia_rossi_doorbell`;
   - una serratura per porta, per esempio `lock.intercom_famiglia_rossi_portone`.
3. **Impostazioni → Entità**, cerca `intercom`: annota la telecamera del G3,
   per esempio `camera.g3_intercom_high` (lo stream di qualità alta).

## 2. Attiva i pacchetti (una volta sola)

Aggiungi queste righe a `configuration.yaml` (app File editor o Studio Code
Server), poi crea la cartella `/config/packages`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Se `homeassistant:` c'è già, aggiungi sotto solo la riga `packages:`.

## 3. Copia il pacchetto

Copia [`examples/packages/intercom_family.yaml`](../../examples/packages/intercom_family.yaml)
in `/config/packages/intercom.yaml` e sostituisci gli ID segnati con `<<<`
con quelli del passaggio 1. Una riga per ogni porta; cancella il secondo
blocco se questa famiglia non apre porte. Ogni `name` deve avere da 3 a 25
caratteri, e `name` e `port` devono essere diversi da quelli di altri
HomeKit Bridge che hai già.

## 4. Controlla e riavvia

**Strumenti per sviluppatori → YAML → Verifica configurazione**. Se dice che
la configurazione non impedirà l'avvio, premi **Riavvia**.

## 5. Abbina all'Apple Casa di questa famiglia

1. Dopo il riavvio Home Assistant mostra due schede con codice QR e PIN:
   **Citofono** e **Porte *nome famiglia***. Se non le vedi, apri le
   🔔 **Notifiche**.
2. Su un iPhone di questa famiglia: **app Casa → + → Aggiungi accessorio**,
   inquadra il codice di **Citofono**. Ripeti con il codice delle porte.
3. Scegli la stanza, tieni i nomi proposti o cambiali.

## 6. Parlare: tieni silenzioso il riquadro di homebridge-unifi-protect

Segui i passaggi 1 e 2 di [homekit.md](homekit.md): il riquadro del G3
Intercom di homebridge-unifi-protect resta nella Casa di questa famiglia con
**Doorbell → Mute** acceso. Non squilla mai; aprilo per **parlare**.

## 7. Prova (5 minuti)

| # | Azione | Risultato atteso |
| --- | --- | --- |
| 1 | Dal citofono chiama **un'altra** famiglia | Nella tua app Casa non succede niente |
| 2 | Chiama **la tua** famiglia | Notifica del campanello con anteprima |
| 3 | Tocca la notifica | Video dal vivo |
| 4 | Apri il riquadro G3 Intercom (silenzioso), premi il microfono | Chi ha suonato ti sente |
| 5 | Apri **Portone** nell'app Casa | Face ID, la porta si apre, *aperta* poi *chiusa* |
| 6 | Apri una porta con `door_id` sbagliato | Il riquadro mostra *bloccata*, poi *chiusa*; il log del router spiega il motivo |

## Se qualcosa non va

- **Non squilla niente**: in **Strumenti per sviluppatori → Stati**, chiama
  la tua famiglia e controlla che `event.…_doorbell` cambi. Se non cambia, il
  problema è nel router (`room_ids`, modalità apprendimento), non in HomeKit.
- **Squilla ma non c'è video**: prova un altro stream della telecamera, per
  esempio `camera.g3_intercom_medium`.
- **Nessun codice QR**: guarda nelle 🔔 **Notifiche**.
- **L'abbinamento non riesce**: iPhone e Home Assistant devono essere nella
  stessa rete; devono essere permessi UDP 5353 e TCP 21071/21072 verso Home
  Assistant.
- **Squillano due riquadri**: il riquadro di homebridge-unifi-protect deve
  avere **Doorbell → Mute** acceso.
- **Resta una vecchia serratura "Door"** dopo il passaggio da
  `unlock.door_id` a `unlock.doors`: il router la toglie al riavvio
  successivo; in Apple Casa rimuovi una volta l'accessorio rimasto.
