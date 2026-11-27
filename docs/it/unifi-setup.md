# Configurazione UniFi

🇬🇧 [Read in English](../unifi-setup.md)

Si fa una volta sola, a cura di chi gestisce la console condivisa delle scale.

## 1. Console e dispositivi

1. Installa **UniFi Access** e **UniFi Protect** sulla console (Protect vuole
   un disco, per esempio un UCG-Max con SSD).
2. Adotta in Access il **G3 Intercom** e l'hub della porta (per esempio
   **Door Hub Mini**).
3. Collega la serratura al relè dell'hub. Una serratura motorizzata per porta
   blindata di solito ha una sua centralina con un ingresso a contatto pulito:
   collegalo all'uscita a relè pulito dell'hub e controlla i limiti di
   tensione/corrente dell'hub nella documentazione Ubiquiti.
4. Controlla che il G3 Intercom compaia anche come telecamera in **UniFi
   Protect** (stessa console) e annota il suo **indirizzo MAC**
   (`homebridge.camera_mac`).

## 2. Rubrica: una voce per famiglia

Access → *Interface Designer* → scegli il G3 Intercom → *Directory*:

- crea una voce per famiglia (nome e numero);
- aggiungi i **destinatari** di quella famiglia solo alla sua voce: i suoi
  utenti UniFi (app), i suoi Intercom Viewer, eventuali numeri di telefono.

Così UniFi stesso fa squillare solo la famiglia giusta nelle app UniFi e
sugli schermi. Questo progetto fa lo stesso per Apple Casa e Home Assistant.

## 3. Un token API per famiglia

Access → *Settings* → *General* → *Advanced* → *API Token* → *Create New*
(Impostazioni → Generali → Avanzate). Dalla stessa pagina si scarica il PDF
della documentazione ufficiale delle API.

| Nome del token | Permessi | Serve per |
| --- | --- | --- |
| `family-router-a` | `view:device` | chiamate (obbligatorio) |
| | + `view:space`, `edit:space` | solo se la famiglia A apre le porte (pulsanti e serrature) |

Ripeti per ogni famiglia. Il token si vede **una volta sola**: dallo solo a
quella famiglia. Revocalo se la famiglia trasloca.

## 4. Un utente locale per famiglia per Homebridge

UniFi OS → *Admins & Users* → aggiungi un utente **solo con accesso
locale**, uno per famiglia (per esempio `homebridge-a`), usato da
homebridge-unifi-protect e dall'integrazione UniFi Protect di Home Assistant
di quella famiglia.

homebridge-unifi-protect scrive che serve **Full Management** per tutte le
sue funzioni e che senza funziona in modo più limitato. Parti dal ruolo più
limitato che dà il video dal vivo, poi prova parlare e aprire nell'app Casa;
alza il ruolo solo se manca qualcosa che ti serve.

## 5. Rete

Il computer di ogni famiglia (Home Assistant / host Docker) deve raggiungere
la console; le famiglie non devono raggiungersi tra loro.

- Metti ogni famiglia nella sua rete/VLAN (o su una porta dedicata della
  console).
- Da ogni rete di famiglia verso la console permetti solo il necessario:
  TCP **12445** (API di Access: notifiche, porte, apertura) e le porte usate
  dai client di Protect (di solito TCP **443**, **7441** per gli stream RTSPS
  in Home Assistant e **7446** per il video dal vivo diretto di
  homebridge-unifi-protect 8.4+, che altrimenti ripiega sulla 443).
- Blocca il traffico tra le reti delle famiglie.

## 6. Impronta del certificato

La console usa un certificato autofirmato sulla porta 12445. Fissalo:

```bash
python -m family_router --fingerprint 192.168.50.1
```

Eseguilo da una rete fidata, poi metti il valore in `access.tls_fingerprint`.

Senza Python lo stesso valore si legge con OpenSSL (accettato così com'è):

```bash
openssl s_client -connect 192.168.50.1:12445 </dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256
```

Se il certificato della console viene rigenerato, il router rifiuta di
collegarsi (log: `certificate fingerprint mismatch`): leggi la nuova impronta
e aggiorna la configurazione.
