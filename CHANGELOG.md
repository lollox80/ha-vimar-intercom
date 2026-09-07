# Changelog

Formato: Keep a Changelog. Versioni: semver. Le voci più recenti in alto.

## [Unreleased]

- **`sga_target`/`picg_target` configurabili** (ROADMAP P0, gap principale pre-pubblicazione): erano hardcoded a `"55001"` in giro per il codice, un valore verificato solo su questo impianto — impianti diversi possono avere un SGA/PICG diverso. Ora sono in `runtime.SGA_TARGET`/`runtime.PICG_TARGET`, popolati da `runtime.configure()` da `options["sga_target"]`/`["picg_target"]` con fallback al default storico in `const.py`. Due nuovi campi testo nello step "Impostazioni" dell'options flow (validati: solo cifre); l'importer rubrica.db, alla conferma, li imposta entrambi automaticamente dal valore rilevato (`SYSTEM.MAGIC_APT_INTERCOM`) invece di limitarsi a segnalarlo. Aggiornati anche gli usi hardcoded residui: `switch.py` (target segreteria/DND), `button.py` (sentinella target "AUTO"), `hub.py` (target di `GET_INIT_STATUS`), e i due servizi HA `send_command`/`open_door` in `__init__.py` (default dinamico invece di `"55001"` fisso). `CAMERA_TARGET` (targa video, `"55100"`) resta fuori scope. `runtime.py`, `hub.py`, `switch.py`, `button.py`, `__init__.py`, `config_flow.py`, `strings.json`, `translations/{it,en}.json`. Nuovi test in `tests/test_runtime.py`; `runtime` aggiunto a `PURE` in `tests/test_smoke.py`.
- **Importer `rubrica.db` nell'options flow** (Milestone 3.1.0, ROADMAP P0): nuovo menu nelle opzioni dell'integrazione con due voci, "Impostazioni di rete e attuatori" (il form esistente, ora sullo step `settings`) e "Importa attuatori da rubrica.db". Quest'ultimo fa caricare il file `rubrica.db` (selettore file nativo HA, `homeassistant.components.file_upload`), lo legge in sola lettura, estrae attuatori + parametri `SYSTEM` (stessa logica read-only di `tools/parse_rubrica.py`, isolata nel nuovo modulo puro `rubrica_import.py`) e mostra un riepilogo di conferma (numero/nome attuatori trovati, SGA rilevato, con avviso se diverso da quello attualmente in uso) prima di sostituire la lista attuatori salvata — senza più copiare a mano l'output JSON del tool. Nuova dipendenza manifest: `file_upload`. `config_flow.py`, `rubrica_import.py` (nuovo), `strings.json`, `translations/{it,en}.json`, `manifest.json`. Test dedicati in `tests/test_rubrica_import.py` (schema completo, filtro per GID, fallback senza `ACTUATOR_RULES`/`ICON_LIST`, file mancante/non-SQLite, sola lettura); aggiunto a `PURE` in `tests/test_smoke.py`.
- Fix logging: il buffer di debug interno (`_debug_log`) alzava il logger `custom_components.vimar_intercom` a DEBUG, e per propagazione ai logger Python questo scavalcava il livello WARNING impostato in `logger:` di HA. Ora il logger ha `propagate = False` e un handler dedicato inoltra al log HA solo WARNING+. `__init__.py`, `README.md`.
- Fix log "Stale response 407" sul keepalive SIP: `_send_options_ping` non registrava il proprio Call-ID tra le risposte attese, quindi la risposta del proxy al keepalive OPTIONS finiva loggata come WARNING anche se normale. `_dispatch_message` ora declassa a DEBUG le risposte con Call-ID `ping-*`. Con questa fix + quella sopra non serve più alcun filtro `logger:` in `configuration.yaml`. `sip_client.py`, `README.md`.

## [3.1.4] - 2026-08-20

- Fix pipeline camera: forward RTP video->ffmpeg, conflitto porte AV risolto (lock + terminazione pulita, no piu 'Address in use'), SDP scritto in executor (no blocking I/O), keyframe INFO al peer reale (non 55001) con gestione 407. Probe: comando call ora riceve e conta i pacchetti RTP (audio/video) con NAT punch

## [3.1.3] - 2026-08-20

- Media in RTP chiaro di default (media_enc off): l'impianto non accetta SRTP; build_sdp offre RTP/AVP senza crypto, media_handler passa RTP non cifrato. Opzione media_enc (SRTP) in options flow. Comando sip_probe call per test chiamata/autoaccensione da terminale

## [3.1.2] - 2026-08-20

- Camera on-demand (autoaccensione): chiama la targa video 55100 (const.CAMERA_TARGET dalla rubrica) invece del PICG 55001 che dava 488 Not Acceptable Here

## [3.1.1] - 2026-08-20

- Fix riaggancia per chiamate in arrivo: il BYE ora punta al chiamante (era R.INTERCOM); card Lovelace citofono base in docs/lovelace_example.yaml (video + rispondi/apri/riaggancia)

## [3.1.0] - 2026-08-20

### Added
- **Stato iniziale via `GET_INIT_STATUS`**: dopo il REGISTER (avvio e reconnect) l'hub invia `GET_INIT_STATUS` (Panda: blue) al PICG (`const.PICG_TARGET = SGA_TARGET = 55001`). La risposta `GET_INIT_STATUS_REPLY;[{PARAM,VALUE}]` è parsata in modo generico e robusto (fallback a regex se il body arriva troncato) e popola `voicemail`, `dnd`, `vm_level`, `rubrica_ver`, `vm_ver` (e `init_status` grezzo per token/altri param). `hub.py`, `const.py`.
- Sensori: `sensor` **Spazio Segreteria** (`vm_level`), **Versione Rubrica** (`rubrica_ver`, attr `vm_ver`), **Ultima Chiamata Persa** (`missed_call_count`, `sip_id`, `ts`). `sensor.py`.
- `binary_sensor` **Nuovo Videomessaggio** (ON su `VM;VIDEO_MESSAGE_CHANGE;NEW`). `binary_sensor.py`.
- Entità `event` **Fuoriporta** (event_type `fuoriporta`) su `FP;{...}`. `event.py`.
- Eventi bus HA in ingresso: `vimar_intercom_missed_call` `{sip_id, ts, name}`, `vimar_intercom_videomessage` `{change, extra, full}`, `vimar_intercom_fuoriporta` `{sip_id, msg}`, `vimar_intercom_call_info` `{sip_id, reason, media_type, video_src}`, `vimar_intercom_phonebook_changed` `{gid, rubrica_ver}` (emesso al cambio di `rubrica_ver` e su `NEW_PHONEBOOK;<gid>;<ver>`). Costanti `EVENT_*` in `const.py`, fire in `__init__.py` via callback dell'hub. Parsing solo in lettura (difensivo JSON/`;`, nessun comando in uscita).
- Test parser `GET_INIT_STATUS_REPLY` (completo, reale, troncato) ed eventi `MISSED_CALL`/`VM;VIDEO_MESSAGE_CHANGE`/`FP`/`CALL_INFO`/`NEW_PHONEBOOK`/`phonebook_changed`. `tests/test_hub_stats.py`.
- Stato iniziale via GET_INIT_STATUS->55001 (segreteria/DND all'avvio, sensori vm_level e rubrica_ver); eventi in arrivo: chiamata persa, videomessaggio, fuoriporta, call_info, phonebook_changed; fix troncamento body MESSAGE (200->4096) per non perdere dnd/voicemail

## [3.0.1] - 2026-08-19

- Fix form opzioni: rimosse graffe ICU nella descrizione attuatori (INVALID_ARGUMENT_TYPE); il test SIP live nelle opzioni ora si esegue solo se cambiano i parametri SIP (non blocca il salvataggio dei soli attuatori)

## [3.0.0] - 2026-08-19

 — in preparazione 3.0.0 (2026-08-19)
### Added
- Attuatori dinamici come bottoni: letti da `options["actuators"]` (lista JSON `{name, msg, target, icon}` prodotta da `tools/parse_rubrica.py`), inviati con `Panda: command`. `button.py` (`VimarActuatorButton`), `runtime.py` (`R.ACTUATORS`), `config_flow.py` (campo/validazione options), `strings.json`+traduzioni.
- `tools/parse_rubrica.py`: legge `rubrica.db` (SQLite, read-only) e ne estrae attuatori JSON per HA + SGA (`SYSTEM.MAGIC_APT_INTERCOM`) + parametri `SYSTEM`.
- `tools/sip_probe.py`: opzione `--cloud` (TLS via SRV `_sips._tcp`, SNI `ipvdes.vimar.cloud`, porta 5070), `VIMAR_SIP_HA1` (usa l'`ha1` senza password in chiaro), `--imei`/`--myname` per test di impersonazione.
- `docs/RUBRICA.md`: schema completo di `rubrica.db` (23 tabelle), metodo di estrazione via root (Samsung A32) e flusso `rubrica.db` → opzioni HA.
### Fixed
- **SGA reale = 55001** (`const.py`): `VOICEMAIL;ON/OFF` e `DND;ON/OFF` (Panda: blue) verso `SYSTEM.MAGIC_APT_INTERCOM = 55001` **accendono/spengono davvero** segreteria e non disturbare sul Tab (verificato sul campo). I precedenti target 55002/61000/60002/101 davano 200 senza effetto.
### Changed
- `README.md` e `ARCHITECTURE.md` riscritti in modo veritiero: stack SIP custom che emula VIEW (header `Panda`, UDP locale/TLS cloud), camera on-demand, **nessun RTSP**. Rimossi i riferimenti errati all'evento bus `vimar_intercom_campanello` (il campanello è un'entità `event`). Aggiornati `docs/STATE.md`, `docs/PROTOCOL.md`, `docs/ROADMAP.md`.
- SGA 55001 (segreteria/DND funzionanti), attuatori dinamici da rubrica, parse_rubrica, sip_probe --cloud/--ha1, docs veritieri

## [2.9.4] - 2026-08-19
### Fixed
- SRTP migrato ad AES-CTR di pycryptodome: rimossa la dipendenza non dichiarata da cryptography (srtp.py la importava senza che fosse nei requirements del manifest). Aggiunti test con i vettori ufficiali RFC 3711 B.3 che verificano keystream e KDF byte per byte.

## [2.9.3] - 2026-08-18
### Changed
- `const.py`: `ACTUATORS = []` — rimossi i token ipotizzati OPEN_F1/OPEN_2 (aprivano la porta). ADR-4.
- Commenti/documentazione dei comandi Segreteria/DND (Panda: blue, SGA).

## [2.9.2] - 2026-08-18
### Fixed
- Modalità cloud: risoluzione SRV `_sips._tcp.ipvdes.vimar.cloud` → `flexiprod{1,2,3}.ipvdes2.vimarsso.cloud:7042`, TLS con SNI. ADR-5.

## [2.9.0] - 2026-08-17
### Added
- Servizio `vimar_intercom.fetch_local` (GET HTTP Digest verso il proxy locale) per tentare il download di rubrica/mailbox.

## [2.8.0] - 2026-08-17
### Security
- Hardening endpoint HTTP: `/audio_ws`, `/push_token`, `/debug` autenticati; `/video`, `/av` solo da LAN (`_is_local_request`). ADR-3.

## [2.2.0 – 2.7.0] - 2026-08-16/17
### Added
- Switch Segreteria e Non disturbare (stato letto dagli annunci `VOICEMAIL;`/`DND;` da 55002).
- Rilevamento modello da header SIP (`model_detect.py`), options flow UDP locale / cloud, statistiche estese.

## [2.1.0] - 2026-08-16
### Added
- Sensori estesi (stato, ultimo chiamante, contatori, durata, ultima apertura, ultimo comando, ultimo messaggio).
- Servizi `send_command`, `call`, `answer`, `hangup`, `open_door`.

## [2.0.0] - 2026-08-15
- Fork di noiseheroes-lab/ha-custom-components `vimar_intercom`; adattamento a Tab 7S 2F+ WiFi (40507), UDP locale.
