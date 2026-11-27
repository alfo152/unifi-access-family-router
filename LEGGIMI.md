# UniFi Access Family Router - guida in italiano

🇬🇧 [Read in English](README.md)

**Un G3 Intercom sul portone, tre famiglie, ognuna con il suo Home Assistant
e la sua Apple Casa: squilla solo la famiglia che il visitatore ha chiamato.**

Il programma ascolta le API ufficiali di UniFi Access, legge quale voce
della rubrica è stata chiamata (`room_id`) e fa squillare **solo** quella
famiglia:

- 🍎 **Apple Casa**: campanello con anteprima e video dal vivo, più una
  serratura per ogni porta, tramite il HomeKit Bridge di Home Assistant
  ([pacchetto pronto](examples/packages/intercom_family.yaml)); per parlare
  c'è [homebridge-unifi-protect](https://github.com/hjdhjd/homebridge-unifi-protect)
- 🏠 **Home Assistant**: campanello, chiamata in corso, un pulsante Apri e
  una serratura per ogni porta (*Portone*, *Cancello*, *Garage*…)

Ogni famiglia ha **la sua copia**: suo token, suo broker MQTT, suo
Homebridge, suo Home Assistant. In comune ci sono solo il citofono e la
console nell'armadio delle scale.

> Stato: versione 0.2.0, verificata con test automatici basati sulla
> documentazione ufficiale delle API e con la simulazione di un palazzo con
> 3 famiglie. Va confermata sul G3 Intercom vero con la
> [lista di prova](docs/it/testing-checklist.md).

**🧪 Cerco tester:** hai un G3 Intercom con UniFi Access e almeno due voci
in rubrica? Installa, lascia `learn_mode: true`, chiama ogni voce una volta
e [manda un resoconto](https://github.com/alfo152/unifi-access-family-router/issues/new?template=test_report.yml).
Con le impostazioni di partenza il programma scrive solo nel log: non
squilla nulla e non apre nessuna porta.

**Verifica in 5 minuti senza Home Assistant:** basta Python 3.8+ su un
computer che raggiunge la console. Lo script [`tools/watch_calls.py`](tools/watch_calls.py)
ascolta e basta.

1. In UniFi Access: *Settings → General → Advanced → API Token*
   (Impostazioni → Generali → Avanzate), crea un token con il solo permesso **view:device** (non può aprire porte;
   cancellalo dopo la prova).
2. Scarica e avvia lo script:

   ```sh
   curl -O https://raw.githubusercontent.com/alfo152/unifi-access-family-router/main/tools/watch_calls.py
   python3 watch_calls.py IP_DELLA_CONSOLE
   ```

   Su Windows al posto di `python3` scrivi `py`.

3. Incolla il token quando lo chiede, chiama ogni voce della rubrica una
   volta, premi **Ctrl+C** e condividi il riepilogo (non contiene il token).

---

## Cosa serve

- Console UniFi con **Access** e **Protect** (es. UCG-Max con SSD)
- **G3 Intercom** + **Door Hub Mini** (o altro hub) + serratura motorizzata
- Per ogni famiglia: un piccolo computer sempre acceso con **Home Assistant
  OS** (add-on) oppure **Docker** (pacchetto pronto in `stack/`)

## 1. Console (una volta sola) → [dettagli](docs/it/unifi-setup.md)

1. Rubrica del G3: **una voce per famiglia**, con i destinatari solo di
   quella famiglia (utenti, schermi, telefoni).
2. **Un token API per famiglia**: Access → Impostazioni → Generale →
   Avanzate → API Token. Permessi: `view:device`; aggiungi `view:space` ed
   `edit:space` solo se vuoi aprire le porte.
3. **Un utente locale per famiglia** per Homebridge (solo accesso locale).
4. Rete: ogni famiglia vede la console (porta **12445** e le porte di
   Protect), ma non le altre famiglie.
5. Leggi l'**impronta del certificato** della console:
   `python -m family_router --fingerprint 192.168.50.1`
   (con Docker: `docker compose run --rm router --fingerprint 192.168.50.1`)

Per usare i comandi `python -m family_router …` su un computer: scarica il
progetto, apri la cartella ed esegui una volta `pip install .` (poi puoi
scrivere anche `family-router …`).

## 2. Installazione per ogni famiglia

**Con Home Assistant OS (consigliato):**

1. Installa l'add-on **Mosquitto broker** e l'integrazione **MQTT**.
2. Tocca questo pulsante (oppure Impostazioni → Componenti aggiuntivi →
   Negozio → ⋮ → Repository → aggiungi l'indirizzo GitHub di questo
   progetto):

   [![Aggiungi il repository a Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Falfo152%2Funifi-access-family-router)

3. Installa **UniFi Access Family Router**, compila `access.host`,
   `access.token`, `access.tls_fingerprint`, `family.name`. Lascia
   `mqtt.host` vuoto (usa il broker di Home Assistant).

**Con Docker:** copia la cartella `stack/` sul computer della famiglia e
segui i comandi scritti in cima a `stack/docker-compose.yml`.

## 3. Modalità apprendimento

1. Avvia con `learn_mode: true`.
2. Dal citofono chiama **una volta ogni famiglia**.
3. Nel log trovi una riga per ogni chiamata:
   `LEARN: ring … room_id=WR-… -> no match`
   e una riga pronta per ogni porta:
   `LEARN: door 'Portone' -> door_id … (add-on: unlock.doors "Portone=…")`
4. Copia il `room_id` **della tua famiglia** in `family.room_ids`.
5. Porte da aprire (facoltativo): `unlock.enabled: true` e in
   `unlock.doors` una riga per porta, copiata dal log:

   ```yaml
   doors:
     - Portone=0ed545f8-…
     - Cancello=a8f2c6d1-…
   ```

   Fino a 10 porte; per ognuna Home Assistant crea un pulsante e una
   serratura ([dettagli](docs/it/home-assistant.md#3-porte)).
6. Metti `learn_mode: false` e riavvia.

Se tutte le voci danno lo stesso `room_id`, usa `door_guard_ids` (i
destinatari della chiamata) e segnalalo su GitHub.

## 4. Apple Casa → [guida passo passo](docs/it/homekit-bridge.md)

1. **Squillo, video e serrature**: copia il pacchetto
   [`examples/packages/intercom_family.yaml`](examples/packages/intercom_family.yaml)
   in `/config/packages/` del Home Assistant della famiglia, metti i tuoi ID,
   riavvia e inquadra i 2 codici QR con l'app Casa.
2. **Parlare**: Homebridge + plugin **homebridge-unifi-protect** (versione 8
   o successiva) con console, utente locale della famiglia, **MQTT URL** del
   broker della famiglia, **MQTT topic** `unifi/protect`.
3. Nelle Feature Options del plugin: mostra solo il G3 Intercom, lascia
   attivo l'audio nei due sensi, attiva **Doorbell → Mute** e accendi
   l'interruttore nell'app Casa: questo riquadro serve per **parlare**, non
   squilla ([dettagli](docs/it/homekit.md)).
4. Abbina tutto **solo** alla Casa della tua famiglia.
5. **Prova**: chiama un'altra famiglia → da te non squilla niente; chiama la
   tua → squilla il citofono del bridge; apri **Portone** → Face ID e la
   porta si apre.

Perché non basta homebridge-unifi-protect: verificato nel suo codice
(versione 8.4.0), con Mute acceso non fa squillare nemmeno i comandi MQTT.
All'autore è stata chiesta un'opzione
([#1363](https://github.com/hjdhjd/homebridge-unifi-protect/issues/1363)).

## 5. Home Assistant → [dettagli](docs/it/home-assistant.md)

Compaiono da soli: **Campanello** (evento), **Esito chiamata** (evento),
**Chiamata in corso** e, per ogni porta, un pulsante **Apri** e una
**serratura** (*chiusa*, *aperta* per qualche secondo, *bloccata* se la
console rifiuta). Esempi pronti in [`examples/`](examples/): pacchetto per
Apple Casa, schermata con video e un pulsante per porta, notifica sul
telefono con il tasto **Apri**.

## 6. Prova finale

Segui la [lista di prova](docs/it/testing-checklist.md): ogni famiglia riceve
solo le sue chiamate, l'apertura funziona e il registro di Access mostra
quale famiglia ha aperto.

## Sicurezza

- Un token per famiglia, solo con i permessi necessari. Mai account admin.
- Impronta del certificato sempre impostata.
- Broker MQTT con password (il comando Apri passa da lì).
- Reti delle famiglie separate tra loro.

## Limiti da conoscere

- Il significato di `room_id` è dedotto dalla documentazione ufficiale:
  va confermato con la modalità apprendimento sul G3 vero.
- Il pacchetto per Apple Casa richiede che il G3 Intercom compaia come
  telecamera nell'integrazione UniFi Protect di Home Assistant: da
  confermare su un G3 vero.
- Audio nei due sensi in Apple Casa: lo fornisce homebridge-unifi-protect;
  senti l'eco della tua voce (chi è alla porta sente bene).
- Parlare da una schermata di Home Assistant non fa parte del progetto.
