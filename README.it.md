# Vimar Intercom — Integrazione Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

🇬🇧 *[Read this page in English](README.md)*

Integra il videocitofono **Vimar Elvox** (2 Fili Plus / IP / 2FV2) in Home Assistant: ricevi lo
squillo, apri la porta/cancello, guarda la camera **su richiesta**, comanda **segreteria** e
**non disturbare**, e usa gli **attuatori** del tuo impianto (F1/F2, luci scala, relè) come bottoni.

> **Come funziona davvero.** Questa integrazione **non** usa RTSP. Implementa uno **stack SIP
> custom in Python/asyncio** che emula l'app ufficiale **Vimar VIEW** ("TOGA"): stesso `User-Agent`,
> stessi header identità (`Mobile-IMEI`, `MyName`) e l'header proprietario **`Panda`**. Parla o con
> il **Flexisip locale sul Tab** (UDP :5060) o con il **cloud Vimar in TLS** (SRV `_sips._tcp`).
> Il video arriva **on‑demand** dalla chiamata SIP (RTP H.264, decodifica ffmpeg → MJPEG), non da uno stream RTSP sempre attivo.

---

## Compatibilità

Sviluppata su **Elvox Tab 7S 2F+ WiFi (art. 40507)**. Anche gli altri Tab Vimar 2F / 2FV2 / IP
dovrebbero funzionare — la configurazione arriva dal QR di abbinamento — e la tabella qui sotto
riporta quello che è stato effettivamente segnalato finora.

| Modello | Art. | Impianto | Firmware | Connessione | Stato |
|---|---|---|---|---|---|
| Elvox Tab 7S 2F+ WiFi | 40507 | 2F | — | UDP locale | Piattaforma di sviluppo: squillo, chiamata, rispondi/riaggancia, apri porta, video on-demand, attuatori |
| Elvox Tab 5S UP 2 Wire WiFi | 40515 | 2FV2 | 2.1.0203 | TLS cloud | Funzionante, segnalato da @CPietro — vedi note sotto |
| Elvox Tab 5S UP 2 Wire WiFi | 40515 | — | — | TLS cloud | Registrazione cloud OK dopo la fix 1.0.1, segnalato da @gtarraran992 ([#1](../../issues/1)) |

**Cosa cambia da impianto a impianto.** Le due segnalazioni sui Tab 5S, messe accanto all'impianto di
sviluppo, portano alla stessa conclusione pratica: *conta l'indirizzo a cui mandi il comando, e quanto
il Tab ti racconta di ritorno*.

- **I comandi di stato vanno all'SGA.** Sull'impianto di sviluppo (40507 / 2F) `VOICEMAIL;ON|OFF` e
  `DND;ON|OFF` funzionano se inviati all'SGA — lì `55001`, preso da `SYSTEM.MAGIC_APT_INTERCOM` della
  rubrica. Mandati altrove (l'indirizzo del Tab stesso, il gruppo appartamento, il vecchio default
  `60001`) tornano un 200 OK senza effetto, o un 404. Se i tuoi switch sembrano morti, le opzioni
  SGA/PICG sono la prima cosa da controllare — vedi la tabella delle opzioni più sotto.
- **`GET_INIT_STATUS` risponde, ma con quantità di dettaglio diverse.** Sul 40507 la risposta è corta:
  `rubrica_ver`, `vm_ver`, `vm_level`, `dnd`, `voicemail`. Sull'impianto 40515 / 2FV2 è il payload
  completo — `dnd`, `voicemail`, `rubrica_ver`, `vm_ver`, `vm_level`, `vm_timeout`,
  `vm_timeout_values`, `apt_names`, `GID`, `media_enc` e un `token`. Per questo l'integrazione legge
  quello che trova e ignora quello che manca, invece di aspettarsi un insieme fisso.
- **La cifratura media è un valore dell'impianto**, non un default globale: il 40515 dichiara
  `media_enc: "srtp"`, mentre l'impianto di sviluppo rifiuta SRTP e lavora in RTP chiaro.
- Ancora aperto sul 40515: **la segreteria si accende ma non si spegne**, in corso di verifica da chi
  l'ha segnalato.

Se lo fai funzionare su un modello diverso, o sullo stesso con risultati diversi, apri una
[segnalazione di compatibilità hardware](../../issues/new?template=compatibility_report.yml) — anche
i casi in cui ha funzionato tutto al primo colpo sono utili quanto quelli in cui si è rotto qualcosa.

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
"squillo → notifica" basata sul cambio di stato dell'entità `event`, più un riaggancio di sicurezza
che chiude una chiamata rimasta aperta per due minuti:

```yaml
automation:
  - alias: "Citofono - Squillo → notifica"
    trigger:
      - platform: state
        entity_id: event.vimar_intercom_doorbell
    action:
      - action: notify.mobile_app_IL_TUO_TELEFONO
        data:
          title: "🔔 Qualcuno al citofono"
          message: "Squillo delle {{ now().strftime('%H:%M:%S') }}."
          data:
            image: "/api/camera_proxy/camera.vimar_intercom_intercom"
```

⚠ **Non aggiungerci `camera.snapshot`.** Sembra funzionare — la chiamata al servizio riesce — ma non
scrive nessun file e non logga niente, perché sulla versione attuale l'entità camera non può produrre
immagini ([#8](../../issues/8)). Nemmeno `camera.record` funziona: richiede l'integrazione `stream`,
che una camera MJPEG non fornisce. E non aggirare il problema con una `camera: platform: ffmpeg`
puntata su `/api/vimar_intercom/av`: blocca Home Assistant finché la sonda di ffmpeg non scade. Gli
stessi avvisi, con i dettagli, sono dentro il file del package.

In `docs/lovelace_example.yaml` c'è una card Lovelace di base con i pulsanti rispondi / apri porta /
riaggancia. Il riquadro del video, per lo stesso motivo di sopra, resta vuoto.

## Limiti noti

- **Impianto solo‑cloud**: l'interfaccia HTTP locale del Tab (:80) può accettare il TCP e poi restare
  muta, quindi non c'è rubrica da leggere in LAN; camera, attuatori, apri‑porta e i comandi di stato
  funzionano lo stesso via SIP.
- **Segreteria/DND**: si comandano attraverso l'**SGA** (`SYSTEM.MAGIC_APT_INTERCOM` della rubrica,
  `55001` sull'impianto di sviluppo). Inviati a qualunque altro indirizzo vengono ignorati in
  silenzio: azzeccare l'SGA è ciò che li fa funzionare — impostalo in Options o lascialo riempire
  dall'import di `rubrica.db`.
- **Rubrica cloud**: serve un `token`. Gli impianti che rispondono al `GET_INIT_STATUS` in forma lunga
  lo consegnano direttamente, e a quel punto la rubrica si scarica con una sola richiesta autenticata —
  vedi `docs/RUBRICA.md` §0, verificato su un 40515. Gli impianti che rispondono in forma corta
  (compreso quello di sviluppo) non hanno il token, e lì resta l'estrazione manuale. Lo scaricamento
  automatico non è ancora implementato ([#5](../../issues/5)).
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

**Limite noto**: il compromesso vale in entrambe le direzioni. Poiché il componente tiene il proprio
logger a `DEBUG` e inoltra solo `WARNING` e oltre, impostare
`logger: logs: custom_components.vimar_intercom: debug` in `configuration.yaml` **non** farà comparire
le righe `DEBUG` di questo componente nel log di Home Assistant: si leggono da
`/api/vimar_intercom/debug`. Rendere configurabile il livello inoltrato è nella lista delle cose da
fare.

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

## Contribuire

Vedi [CONTRIBUTING.md](CONTRIBUTING.md) (in inglese). In breve: mai indovinare comandi o token SIP (un
token attuatore sbagliato può aprire fisicamente una porta), niente credenziali nel repo né nei log
che alleghi, `pytest` verde prima di aprire una PR, e indica sempre su che hardware hai provato.

---

## Disclaimer

Progetto **non affiliato né approvato da Vimar S.p.A.**. "Vimar", "Elvox", "VIEW" sono marchi dei
rispettivi proprietari. L'utente fornisce le proprie credenziali del proprio impianto.

## Licenza

**MIT** — © [Noise Heroes](https://github.com/noiseheroes-lab) (progetto upstream) e contributori del fork.
