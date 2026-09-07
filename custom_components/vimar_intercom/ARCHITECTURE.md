# Vimar Intercom — Architettura

## Panoramica

L'integrazione collega Home Assistant a un videocitofono **Vimar Elvox** tramite uno **stack SIP
custom** scritto in Python/asyncio, che **emula l'app ufficiale Vimar VIEW** ("TOGA"). Non usa RTSP,
non usa librerie SIP esterne: parla direttamente con il **Flexisip** dell'impianto — locale sul Tab
(UDP :5060) o cloud Vimar (TLS, SRV `_sips._tcp.ipvdes.vimar.cloud`).

Elementi distintivi del protocollo Vimar (dedotti dal reverse dell'app, vedi `docs/PROTOCOL.md`):
header identità `Mobile-IMEI` / `MyName`, `User-Agent` TOGA, e l'header proprietario **`Panda`**
(`command` per gli attuatori, `blue` per i messaggi di stato/sistema). Il video arriva **on‑demand**
dalla chiamata SIP (RTP H.264 + PCMU), decodificato da ffmpeg in MJPEG.

---

## Struttura file

```text
vimar_intercom/
├── __init__.py         Setup/teardown entry, registrazione servizi, HTTP views (/video, /av, /audio_ws, /push_token, /debug)
├── sip_client.py       Stack SIP asyncio: REGISTER/INVITE/MESSAGE/OPTIONS/BYE/INFO, digest, UDP+TLS, parsing (~1300 righe)
├── hub.py              VimarIntercomHub: orchestrazione, stats, callback entità, async_door/async_send_command, keepalive
├── runtime.py          R.*: credenziali/impostazioni dinamiche dalla config entry (SIP_USER, domain, ACTUATORS…)
├── config_flow.py      Config flow (QR o manuale) + options flow (rete SIP + attuatori dinamici)
├── qr_decoder.py       Decodifica QR di abbinamento Vimar (AES)
├── const.py            Costanti, comandi (OPEN_2F, VOICEMAIL/DND), SGA_TARGET, header, User-Agent
├── camera.py           Camera on-demand (MJPEG da pipeline RTP/ffmpeg)
├── event.py            Entità event "doorbell" (event_type ring)
├── lock.py             Serratura (unlock = SIP MESSAGE, auto-relock)
├── button.py           Bottoni: chiama/rispondi/riaggancia/apri porta + ATTUATORI DINAMICI
├── switch.py           Switch Segreteria / Non disturbare (VOICEMAIL/DND verso SGA)
├── sensor.py           Sensori stato/statistiche (stato, chiamante, contatori, durata, aperture…)
├── binary_sensor.py    SIP registrato, in call, squillo, chiamata in uscita
├── media_handler.py    RTP H.264/PCMU, WebSocket audio/video, ffmpeg
├── srtp.py             SRTP AES-CM-128-HMAC-SHA1-80 (pycryptodome)
├── model_detect.py     Rilevamento modello dagli header SIP
├── device.py           device_info condiviso
├── push_sender.py      APNs VoIP (opzionale)
├── services.yaml, strings.json, translations/{it,en}.json
├── README.md, ARCHITECTURE.md
└── manifest.json, icon.svg
```

---

## Protocollo (sintesi)

### Trasporto e registrazione
- **UDP locale** verso il Flexisip sul Tab, **oppure TLS cloud** (SRV `_sips._tcp` → `flexiprod{1,2,3}…:7042`, SNI `ipvdes.vimar.cloud`).
- REGISTER con **Digest MD5** (`HA1 = MD5(user:realm:pwd)`, o `ha1` diretto dalla config entry); keepalive OPTIONS + re‑REGISTER.
- Header sempre presenti: `User-Agent: TOGA…`, `Mobile-IMEI`, `MyName`, `Call-ID` (10 char).

### Apri porta e attuatori (`Panda: command`)
- SIP MESSAGE, body = **`MSG` letterale della rubrica** (es. `OPEN_2F`), verso il **`GID_PE`** della targa.
- Gli attuatori dinamici (`button.VimarActuatorButton`) riusano `hub.async_send_command` — non reimplementano nulla dello stack.

### Segreteria / DND (`Panda: blue`)
- `VOICEMAIL;ON|OFF`, `DND;ON|OFF` verso l'**SGA** (`SYSTEM.MAGIC_APT_INTERCOM` della rubrica; su questo impianto **55001**, `const.SGA_TARGET`).
- Lo **stato** è reale: il Tab annuncia i cambi via SIP MESSAGE, l'hub li parsa (`_update_stats`) e gli switch li riflettono.

### Chiamata / squillo / camera
- **INVITE in arrivo** (targa preme il campanello) → hub attiva callback → entità `event` `ring` + `binary_sensor` squillo + statistiche; auto‑answer configurabile.
- **INVITE in uscita** (`call`, o apertura della camera) → negoziazione SDP → RTP H.264/PCMU.
- La **camera** non ha uno stream permanente: quando HA apre lo stream, l'hub avvia la chiamata e la
  pipeline RTP→ffmpeg produce MJPEG. Keyframe via SIP INFO (`picture_fast_update`, estensione nostra).

Dettaglio completo del vocabolario MESSAGE, piano di numerazione e messaggi in ingresso: `docs/PROTOCOL.md`.

---

## Flusso dati

```
Tab/Cloud Flexisip ──SIP(UDP/TLS)──▶ sip_client (asyncio, stato di modulo)
                                        │  parsing, digest, dialoghi
                                        ▼
                                     hub (VimarIntercomHub)
                                        │  stats, callback, comandi
                          ┌─────────────┼───────────────┐
                          ▼             ▼               ▼
                   entità HA      servizi HA       HTTP views
             (sensor/switch/    (send_command,   (/video,/av,
              button/lock/…)     call, open_door)  /audio_ws)
                                        ▲
                                        │ RTP H.264/PCMU
                                  media_handler + ffmpeg ─▶ camera (MJPEG)
```

`runtime.py` (`R.*`) è popolato da `configure(entry.data)` all'avvio: credenziali, target calcolati
(`INTERCOM`/`DOOR_ESTERNO = sip:55001@<domain>`) e la lista `ACTUATORS`.

---

## Config e options flow

- **Config flow** (`config_flow.py`): step `user` con scelta `qr` / `manuale`; il QR viene decodificato
  e validato con un REGISTER di prova; l'entry salva `sip_user/password/domain/cloud_proxy/gid/mac/plant_type`.
- **Options flow**: rete SIP (`local_proxy`, `use_local_udp`, `local_udp_port`) e **attuatori dinamici**
  (campo JSON validato da `_parse_actuators`, salvato in `options["actuators"]`; un update ricarica l'entry).

---

## Sicurezza

- Credenziali cifrate nella config entry; `ha1` supportato per evitare la password in chiaro.
- `/video` e `/av` filtrati solo‑LAN (`_is_local_request`); `/audio_ws` con auth HA.
- Il payload del QR non è loggato a INFO; il traffico SIP va a `_LOGGER.debug`.

---

## Note di compatibilità

Testato su **Elvox Tab 7S 2F+ WiFi (40507)**, planttype `2F`. Il codice è pensato per estendersi a IP/2FV2
leggendo la rubrica dell'impianto (attuatori, SGA, targhe) — vedi `docs/RUBRICA.md` e `docs/ROADMAP.md`.
Lo stato globale di `sip_client.py` è un debito noto (refactor pianificato in `SipClient` — ROADMAP 3.5.0).

## Note legali

Lo stack SIP emula il comportamento dell'app VIEW per interoperare con l'impianto dell'utente, usando
le credenziali fornite dall'utente stesso. Nessuna elusione di DRM. Progetto **non affiliato a Vimar S.p.A.**.
