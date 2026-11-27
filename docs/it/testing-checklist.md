# Lista di prova sull'impianto vero

🇬🇧 [Read in English](../testing-checklist.md)

Falla una volta dopo l'installazione, con qualcuno al citofono. Spunta ogni
riga per **ciascuna** famiglia.

## Preparazione

- [ ] `python -m family_router --config config.yaml --check` (o il log
      dell'add-on all'avvio) mostra `OK notifications WebSocket` per ogni
      famiglia.
- [ ] `access.tls_fingerprint` è impostato (nel log nessun avviso
      "NOT verified").
- [ ] Il broker MQTT rifiuta un client senza password.

## Modalità apprendimento

- [ ] Ogni voce della rubrica, chiamata una volta, scrive un `room_id`
      **diverso**.
- [ ] Ogni famiglia ha in `family.room_ids` solo il proprio `room_id`.

Se due voci mostrano lo stesso `room_id`, usa invece `door_guard_ids`
(elencano i destinatari della chiamata) e segnalalo in una issue.

## Chiamate

| # | Azione al citofono | Risultato atteso |
| --- | --- | --- |
| 1 | Chiama la famiglia A | Solo A: Apple Casa squilla con video dal vivo, in HA scatta *Doorbell*, *Call active* acceso. B e C: niente. App UniFi/schermi: solo i destinatari di A. |
| 2 | Ripeti per B e C | Uguale, ogni famiglia da sola |
| 3 | Rispondi e apri dall'app UniFi | In HA *Call result* = `unlocked`, *Call active* spento |
| 4 | Lascia squillare senza rispondere | *Call result* = `timeout` (o `expired` dopo `call_timeout_seconds`) |
| 5 | Il visitatore annulla | *Call result* = `canceled` |

## Apple Casa

- [ ] Solo le chiamate della tua famiglia fanno squillare la telecamera del
      HomeKit Bridge ([homekit-bridge.md](homekit-bridge.md)); il riquadro
      HBUP silenzioso non squilla mai.
- [ ] Parlare: chi è alla porta ti sente (tu senti l'eco della tua voce).
- [ ] Ogni serratura chiede Face ID / Touch ID e apre la sua porta; mostra
      *aperta*, poi *chiusa* dopo `relock_seconds`.

## Home Assistant

- [ ] Ogni pulsante *Unlock* apre la sua porta (e solo quella); il registro
      di Access mostra il nome della famiglia.
- [ ] Con un `door_id` sbagliato la serratura risulta *bloccata* e il log del
      router spiega l'errore.
- [ ] La notifica sul telefono con *Apri* funziona anche fuori casa (app
      Companion).

## Resistenza ai guasti

- [ ] Spegni il computer della famiglia A: B e C funzionano lo stesso.
- [ ] Riavvia la console: ogni router si ricollega da solo (log
      `Connected to UniFi Access notifications`) entro circa un minuto.
- [ ] Riavvia il broker di una famiglia: il router si ricollega e le entità
      di HA tornano disponibili.
