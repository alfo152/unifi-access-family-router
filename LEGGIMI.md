# UniFi Access Family Router - guida in italiano

**Un G3 Intercom sul portone, tre famiglie, ognuna con il suo Home Assistant
e la sua Apple Casa: squilla solo la famiglia che il visitatore ha chiamato.**

Il programma ascolta le API ufficiali di UniFi Access, legge quale voce
della rubrica è stata chiamata (`room_id`) e fa squillare **solo** quella
famiglia: in Apple Casa (tramite Homebridge, con video e audio nei due sensi)
e in Home Assistant (campanello, chiamata in corso, pulsante Apri).

Ogni famiglia ha **la sua copia**: suo token, suo broker MQTT, suo
Homebridge, suo Home Assistant. In comune ci sono solo il citofono e la
console nell'armadio delle scale.

> Stato: versione 0.1.0, verificata con test automatici basati sulla
> documentazione ufficiale delle API e con la simulazione di un palazzo con
> 3 famiglie. Va confermata sul G3 Intercom vero con la
> [lista di prova](docs/testing-checklist.md).

**🧪 Cerco tester:** hai un G3 Intercom con UniFi Access e almeno due voci
in rubrica? Installa, lascia `learn_mode: true`, chiama ogni voce una volta
e [manda un resoconto](https://github.com/alfo152/unifi-access-family-router/issues/new?template=test_report.yml).
Con le impostazioni di partenza il programma scrive solo nel log: non
squilla nulla e non apre nessuna porta.

---

## Cosa serve

- Console UniFi con **Access** e **Protect** (es. UCG-Max con SSD)
- **G3 Intercom** + **Door Hub Mini** (o altro hub) + serratura motorizzata
- Per ogni famiglia: un piccolo computer sempre acceso con **Home Assistant
  OS** (add-on) oppure **Docker** (pacchetto pronto in `stack/`)

## 1. Console (una volta sola) → [dettagli](docs/unifi-setup.md)

1. Rubrica del G3: **una voce per famiglia**, con i destinatari solo di
   quella famiglia (utenti, schermi, telefoni).
2. **Un token API per famiglia**: Access → Impostazioni → Generale →
   Avanzate → API Token. Permessi: `view:device`; aggiungi `view:space` ed
   `edit:space` solo se vuoi il pulsante Apri.
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
   e l'ID della porta: `LEARN: door 'Portone' -> door_id …`
4. Copia il `room_id` **della tua famiglia** in `family.room_ids`
   (e il `door_id` in `unlock.door_id` se vuoi il pulsante Apri).
5. Metti `learn_mode: false` e riavvia.

Se tutte le voci danno lo stesso `room_id`, usa `door_guard_ids` (i
destinatari della chiamata) e segnalalo su GitHub.

## 4. Apple Casa → [dettagli](docs/homekit.md)

1. Homebridge + plugin **homebridge-unifi-protect** (versione 8 o successiva).
2. Nel plugin: console, utente locale della famiglia, **MQTT URL** del broker
   della famiglia, **MQTT topic** `unifi/protect`.
3. Feature Options: mostra solo il G3 Intercom, lascia attivo l'audio nei due
   sensi, attiva **Doorbell → Mute** e accendi l'interruttore nell'app Casa.
4. Abbina Homebridge **solo** alla Casa della tua famiglia.
5. **Prova**: chiama un'altra famiglia → da te non squilla niente; chiama la
   tua → squilla. Se con l'interruttore acceso non squilla nemmeno la tua,
   usa l'opzione B (HomeKit Bridge di Home Assistant), spiegata nella guida.

## 5. Home Assistant → [dettagli](docs/home-assistant.md)

Compaiono da soli: **Campanello** (evento), **Esito chiamata** (evento),
**Chiamata in corso**, **Apri** (pulsante). Esempi pronti in
[`examples/`](examples/): schermata con video e pulsante Apri, notifica sul
telefono con il tasto **Apri**, collegamento ad Apple Casa (opzione B).

## 6. Prova finale

Segui la [lista di prova](docs/testing-checklist.md): ogni famiglia riceve
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
- Audio nei due sensi in Apple Casa: lo fornisce homebridge-unifi-protect;
  senti l'eco della tua voce (chi è alla porta sente bene).
- Parlare da una schermata di Home Assistant non fa parte del progetto.
