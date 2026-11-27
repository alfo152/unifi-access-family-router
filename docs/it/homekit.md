# Apple Casa (HomeKit)

🇬🇧 [Read in English](../homekit.md)

Ogni famiglia usa **la propria Apple Casa**, abbinata solo al proprio Home
Assistant e al proprio Homebridge. Lavorano insieme due pezzi:

| Pezzo | Cosa dà | Guida |
| --- | --- | --- |
| **HomeKit Bridge di Home Assistant** | Campanello che squilla **solo per questa famiglia**, anteprima, video dal vivo, una serratura per porta | [homekit-bridge.md](homekit-bridge.md) |
| **homebridge-unifi-protect** (HBUP 8.0+, Homebridge 2.0), riquadro **silenzioso** | **Parlare** con chi ha suonato (audio nei due sensi) | questa pagina, passaggi 1–2 |

## 1. Installa Homebridge e il plugin

- Pacchetto Docker: `stack/docker-compose.yml` avvia Homebridge con la rete
  dell'host (serve a HomeKit). Apri l'interfaccia di Homebridge sulla porta
  8581.
- Installa **homebridge-unifi-protect** dalla scheda Plugin.
- Aggiungi la console (oppure incolla
  [`stack/homebridge/unifi-protect-platform.example.json`](../../stack/homebridge/unifi-protect-platform.example.json)
  nella configurazione JSON):
  - indirizzo: l'IP della console;
  - utente/password: l'utente locale di questa famiglia (vedi
    [unifi-setup.md](unifi-setup.md));
  - **MQTT URL**: il broker della famiglia, per esempio
    `mqtt://homebridge:PASSWORD@127.0.0.1:1883` nel pacchetto Docker;
  - **MQTT topic**: `unifi/protect` (deve essere uguale a
    `homebridge.topic_prefix`).

## 2. Feature Options (interfaccia del plugin → Feature Options)

- Mostra **solo il G3 Intercom**: spegni l'opzione *Device* in generale e
  accendila per il citofono (vince l'impostazione più specifica).
- Lascia attivo **Audio → Two-way audio** (audio nei due sensi, attivo di
  base).
- Attiva **Doorbell → Mute** (`Doorbell.Mute`) e accendi il suo interruttore
  nell'app Casa: così il riquadro di HBUP non squilla mai da solo (vedi
  passaggio 3).

Abbina il bridge di Homebridge **solo** all'Apple Casa di questa famiglia
(codice QR nell'interfaccia di Homebridge).

## 3. Perché lo squillo passa da Home Assistant

HBUP 8.0+ trasforma ogni pressione del citofono Access in uno squillo
HomeKit: in un palazzo con più famiglie squillerebbero tutte.

> **Verificato nel codice di HBUP 8.4.0** (`src/nvr/event-dispatch.ts`):
> lo squillo automatico del citofono e il comando MQTT `doorbell/set`
> passano entrambi dalla stessa funzione `doorbellEventHandler`, che salta lo
> squillo HomeKit quando `Doorbell.Mute` è acceso. Quindi con solo HBUP
> squillano tutte le chiamate o nessuna. All'autore è stata chiesta una
> piccola opzione (ignorare gli squilli automatici di Access, tenere quelli
> MQTT/Trigger):
> [hjdhjd/homebridge-unifi-protect#1363](https://github.com/hjdhjd/homebridge-unifi-protect/issues/1363).
> Se arriva, basterà un solo riquadro HBUP: il router pubblica già
> `doorbell/set` solo per questa famiglia.

Il HomeKit Bridge di Home Assistant funziona **già oggi**: espone la
telecamera del G3 con l'evento **Campanello** di questo progetto come
`linked_doorbell_sensor`. Solo le chiamate di questa famiglia fanno scattare
quell'evento, quindi squilla solo questa famiglia. Guida passo passo e
pacchetto pronto da copiare: **[homekit-bridge.md](homekit-bridge.md)**.

## 4. Parlare e aprire

- **Parlare**: apri nell'app Casa il riquadro silenzioso del citofono HBUP e
  premi il microfono. HBUP non ha la cancellazione dell'eco: senti la tua
  voce, chi è alla porta ti sente normalmente. L'audio nei due sensi dipende
  da Protect, che deve offrire il parla/ascolta per il G3 Intercom:
  controllalo durante la prova. La telecamera del bridge di Home Assistant
  non permette di parlare.
- **Aprire**: usa le serrature del bridge di Home Assistant (una per porta,
  chiedono Face ID / Touch ID, il registro di Access mostra il nome di
  questa famiglia). Anche HBUP aggiunge una serratura per le porte Access
  della stessa console: tienine solo una delle due per non avere riquadri
  doppi.
