# Vimar Intercom — Integrazione Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Integra il videocitofono **Vimar Elvox** (2 Fili Plus / IP / 2FV2) in Home Assistant: ricevi lo
squillo, apri la porta/cancello, guarda la camera **su richiesta**, comanda **segreteria** e
**non disturbare**, e usa gli **attuatori** del tuo impianto (F1/F2, luci scala, relè) come bottoni.

> **Come funziona davvero.** Questa integrazione **non** usa RTSP. Implementa uno **stack SIP
> custom in Python/asyncio** che emula l'app ufficiale **Vimar VIEW** ("TOGA"): stesso `User-Agent`,
> stessi header identità (`Mobile-IMEI`, `MyName`) e l'header proprietario **`Panda`**. Parla o con
> il **Flexisip locale sul Tab** (UDP :5060) o con il **cloud Vimar in TLS** (SRV `_sips._tcp`).
> Il video arriva **on‑demand** dalla chiamata SIP (RTP H.264, decodifica ffmpeg → MJPEG), non da uno stream RTSP sempre attivo.

Testato su **Elvox Tab 7S 2F+ WiFi (art. 40507)**. Altri Tab/impianti Vimar 2F/IP/2FV2 dovrebbero funzionare (config dal QR di abbinamento).

---

## Requisiti

- Home Assistant **2024.1** o successivo, Python 3.11+.
- ffmpeg sull'host HA (dipendenza dichiarata nel manifest) per la camera.
- Il **QR di abbinamento** dell'impianto Vimar (dall'app VIEW) **oppure** i parametri SIP manuali
  (id, password, domain, cloud proxy).
- Requisiti Python: solo `pycryptodome` e `requests` (nessuna libreria SIP esterna: lo stack è custom).

---

## Installazione

### Via HACS
1. HACS → Integrazioni → menu ⋮ → *Custom repositories* → aggiungi il repo come categoria *Integration*.
2. Installa **Vimar Intercom**.
3. Riavvia Home Assistant.

### Manuale
Copia `custom_components/vimar_intercom/` nella cartella `config/custom_components/` di HA e riavvia.

> **Nota per chi reinstalla/aggiorna a mano**: `__init__.py` e `sip_client.py` contengono patch
> locali sul logging (non presenti upstream — vedi sezione *Logging* più sotto). Se sovrascrivi
> questi file con una versione presa da un'altra fonte, riapplica le patch: senza HACS non c'è
> nulla che le preservi automaticamente.

---

## Configurazione

Impostazioni → Dispositivi e servizi → Aggiungi integrazione → **Vimar Intercom**.

- **QR** (consigliato): incolla il testo del QR di abbinamento Vimar; l'integrazione lo decodifica
  (`qr_decoder.py`: Base64(AESkey|AES‑CBC|IV) → coppie `KEY=VALUE`) e compila id, password, domain,
  cloud/local proxy, GID, MAC, planttype.
- **Manuale**: inserisci `sip_user`, `sip_password`, `sip_domain`, `cloud_proxy`.

### Opzioni (dopo l'aggiunta)

Impostazioni → Vimar Intercom → **Configura**:

| Opzione | Descrizione |
|---|---|
| **IP del citofono** (`local_proxy`) | IP del Flexisip locale sul Tab |
| **Usa SIP UDP locale** (`use_local_udp`) | ON = UDP locale; OFF = TLS cloud |
| **Porta UDP locale** (`local_udp_port`) | default 5060 |
| **Attuatori (JSON)** (`actuators`) | lista JSON `{name, msg, target, icon}`; crea bottoni dinamici. Vuoto = nessun bottone |
| **SGA** (`sga_target`) | destinatario di `VOICEMAIL;`/`DND;` e dell'apri‑porta "AUTO". Vuoto = default `55001` |
| **PICG** (`picg_target`) | destinatario di `GET_INIT_STATUS`. Sugli impianti verificati coincide con l'SGA. Vuoto = default `55001` |

Gli attuatori e i valori SGA/PICG si ricavano dalla **rubrica dell'impianto** (`rubrica.db`): dal menu
delle opzioni scegli **"Importa attuatori da rubrica.db"**, carica il file (lo trovi con l'app VIEW o
via root, vedi `docs/RUBRICA.md`) e conferma — attuatori, SGA e PICG vengono impostati in automatico.
In alternativa puoi inserire i valori a mano nello step "Impostazioni" (utile se conosci già l'SGA del
tuo impianto o vuoi modificare la lista attuatori prodotta dall'import).

---

## Entità

| Entità | Piattaforma | Descrizione |
|---|---|---|
| Intercom (Videocitofono) | `camera` | Video **on‑demand**: aprendo lo stream l'hub avvia la chiamata SIP, il video RTP H.264 viene decodificato via ffmpeg in MJPEG (no RTSP) |
| Doorbell (Campanello) | `event` | Entità `event` (device_class DOORBELL), event_type `ring`, allo squillo (INVITE in arrivo) |
| Serratura | `lock` | Apri porta (`OPEN_2F` → targa); auto‑relock dopo 5 s (nessun feedback fisico) |
| Chiama | `button` | Chiamata SIP verso la targa di default |
| Chiama Video (esterno) / Chiama Casa (interno) | `button` | Chiamata verso 55001 / 55002 |
| Rispondi / Riaggancia | `button` | Rispondi (200 OK) / termina (BYE) |
| Apri Porta | `button` | `OPEN_2F` verso la targa |
| *Attuatori dinamici* | `button` | Uno per voce in `options["actuators"]` (F1/F2, luci scala, relè…); invia `MSG` con `Panda: command` |
| Segreteria | `switch` | `VOICEMAIL;ON/OFF` (Panda: blue) verso l'SGA; stato letto dagli annunci del Tab |
| Non Disturbare | `switch` | `DND;ON/OFF` (Panda: blue) verso l'SGA; stato reale |
| Intercom SIP | `binary_sensor` | Registrazione SIP attiva (connectivity) |
| Intercom In Call | `binary_sensor` | Chiamata attiva |
| Intercom Squillo | `binary_sensor` | ON mentre una targa chiama (attr: chiamante) |
| Intercom Chiamata In Uscita | `binary_sensor` | ON mentre HA chiama |
| Intercom Stato | `sensor` (enum) | offline / idle / ringing / in_call / calling (+ attributi rete) |
| Intercom Ultimo Chiamante | `sensor` | targa/monitor dell'ultimo squillo |
| Intercom Ultimo Squillo | `sensor` (timestamp) | ora dell'ultimo squillo |
| Intercom Squilli | `sensor` (contatore) | squilli dall'avvio |
| Intercom Chiamate | `sensor` (contatore) | chiamate connesse |
| Intercom Durata Ultima Chiamata | `sensor` (s) | durata ultima chiamata |
| Intercom Ultima Apertura | `sensor` (timestamp) | ultima apertura porta (attr: targa, esito, contatore) |
| Intercom Ultimo Comando | `sensor` | esito ultimo `send_command` |
| Intercom Ultimo Messaggio Ricevuto | `sensor` | ultimo SIP MESSAGE dal citofono |

---

## Servizi (`services.yaml`)

| Servizio | Descrizione | Campi |
|---|---|---|
| `vimar_intercom.send_command` | SIP MESSAGE arbitrario (per test) | `body`, `target`, `header_name`, `header_value` |
| `vimar_intercom.call` | Chiamata SIP verso una targa/monitor | `target` |
| `vimar_intercom.answer` | Risponde alla chiamata in arrivo | — |
| `vimar_intercom.hangup` | Termina la chiamata attiva | — |
| `vimar_intercom.open_door` | Comando di apertura (`OPEN_2F`) | `target`, `command` |
| `vimar_intercom.fetch_local` | GET HTTP Digest verso l'interfaccia locale del Tab (home mode) | `path`, `save_as`, `host`, `scheme` |

Esempio (Strumenti per sviluppatori → Azioni):

```yaml
action: vimar_intercom.send_command
data:
  body: OPEN_2F
  target: "55001"
  header_name: Panda
  header_value: command
```

---

## Eventi

Il campanello è esposto come **entità `event`** (`event.<...>_doorbell`, event_type `ring`), non come
evento sul bus. Nelle automazioni usa un trigger di stato sull'entità `event` (o sul binary_sensor squillo).

In più, dai `MESSAGE` in arrivo dal Tab, l'integrazione emette sul bus HA:
`vimar_intercom_missed_call`, `vimar_intercom_videomessage`, `vimar_intercom_fuoriporta`,
`vimar_intercom_call_info`, `vimar_intercom_phonebook_changed`. Solo lettura, nessun comando in
uscita. Trigger di esempio:

```yaml
automation:
  - alias: "Citofono - Chiamata persa"
    trigger:
      - platform: event
        event_type: vimar_intercom_missed_call
    action:
      - service: notify.mobile_app_telefono
        data: { message: "Chiamata persa al citofono" }
```

---

## Automazioni di esempio

Il file `packages/vimar_intercom.yaml` (da copiare in `config/packages/`) contiene un'automazione
"squillo → snapshot + clip 15 s → notifica", basata sul cambio di stato dell'entità `event`:

```yaml
automation:
  - alias: "Citofono - Squillo → snapshot e clip"
    trigger:
      - platform: state
        entity_id: event.vimar_intercom_doorbell
    action:
      - service: camera.snapshot
        target: { entity_id: camera.vimar_intercom_intercom }
        data: { filename: "/media/vimar/citofono_{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg" }
      # ... clip + notify (vedi packages/vimar_intercom.yaml)
```

---

## Limiti noti

- **Impianto solo‑cloud**: la lettura dello stato iniziale via SIP (`GET_INIT_STATUS`) non riceve
  risposta e l'interfaccia HTTP locale del Tab (:80) può essere muta; camera/attuatori/segreteria
  funzionano lo stesso via SIP.
- **Segreteria/DND**: il *comando* funziona verso l'**SGA** (`SYSTEM.MAGIC_APT_INTERCOM` della rubrica,
  `55001` su impianti come quello testato). Configurabile in Options (**SGA**/**PICG**) o via import
  automatico di `rubrica.db` — vedi sopra.
- **Attuatori By‑me** (es. luci scala di domotica By‑me): potrebbero non rispondere via SIP anche se elencati in rubrica.
- **Lock**: nessun feedback fisico di stato (auto‑relock ottimistico dopo 5 s).
- **Rubrica**: su impianti solo‑cloud va estratta una tantum (vedi `docs/RUBRICA.md`); l'import automatico via cloud dipende da un token provisionato dall'account.

---

## Logging

Il componente tiene un buffer circolare interno (`_debug_log`, in `__init__.py`) per la propria
diagnostica, e per riempirlo alza il proprio logger a `DEBUG`. Di base questo farebbe propagare
ogni riga `DEBUG` anche al log di Home Assistant, scavalcando il livello impostato in `logger:`
nella `configuration.yaml` (i logger Python propagano al root).

Patch applicata: il logger `custom_components.vimar_intercom` resta a `DEBUG` per il buffer interno,
ma con `propagate = False`; un handler dedicato inoltra al log HA solo gli eventi `WARNING` e oltre.
Risultato: diagnostica interna intatta, log HA pulito.

**"Stale response 407" nel keepalive SIP**: l'OPTIONS periodico (`_send_options_ping` in
`sip_client.py`) non registra il proprio Call-ID tra le risposte attese, quindi la risposta del
proxy (tipicamente un `407`) veniva loggata come `WARNING "Stale response ..."` anche se è l'esito
normale del keepalive. `_dispatch_message` ora riconosce i Call-ID con prefisso `ping-` e li logga
a `DEBUG` invece che `WARNING`. Con questa fix + quella sopra, **non serve più** alcun filtro
`logger:` in `configuration.yaml` per silenziare questi messaggi.

Se aggiorni `__init__.py` o `sip_client.py` da una fonte esterna (non HACS, non versionato per
questo componente), ricontrolla che entrambe le patch siano ancora presenti (vedi nota in
*Installazione → Manuale*).

---

## Sicurezza

- Credenziali SIP (password/`ha1`) memorizzate **cifrate** nella config entry di HA, mai in chiaro nel repo.
- Endpoint HTTP interni: `/video` e `/av` sono filtrati **solo LAN** (`_is_local_request`); il WebSocket
  `/audio_ws` richiede autenticazione HA. Il payload del QR non viene loggato a livello INFO.
- Nessuna dipendenza cloud obbligatoria in modalità UDP locale.

---

## Disclaimer

Progetto **non affiliato né approvato da Vimar S.p.A.**. "Vimar", "Elvox", "VIEW" sono marchi dei
rispettivi proprietari. L'utente fornisce le proprie credenziali del proprio impianto.

## Licenza

**MIT** — © [Noise Heroes](https://github.com/noiseheroes-lab) (progetto upstream) e contributori del fork.
