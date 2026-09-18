# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [semver](https://semver.org/).
Newest entries on top. **Entries are written in English from 1.0.1 onwards**; earlier ones are in
Italian and are kept as they were written.

## [Unreleased]

## [1.0.5] - 2026-09-19

- **The lock and the "Apri Porta" button ignored the configured SGA.** `sga_target` has
  been configurable since 1.0.0 — from the options flow or imported from `rubrica.db` —
  and `const.py` claimed that every platform read it from `runtime`. That was not true:
  `lock.py` passed the literal `"55001"` and did not even import `runtime`, and `button.py`
  did the same for the door and call buttons.

  On the plant this was developed against, `SYSTEM.MAGIC_APT_INTERCOM` happens to be
  `55001`, so nothing looked wrong. On a plant where it differs, the switches and the
  actuators imported from the phonebook followed the configured address while **the lock
  entity — the one exposed to Apple Home — and the "Apri Porta" button kept sending
  `OPEN_2F` to 55001**. As far as we know that returns a bare `200 OK` with no effect,
  which `hub.async_door` counts as success: the lock showed *unlocked* for five seconds
  while the door stayed shut. Failing while reporting success is the worst of the options.

  Both now pass no target at all, so `hub.async_door` resolves it from
  `runtime.DOOR_ESTERNO` like every other path. The "Chiama Video (esterno)" button uses
  `runtime.SGA_TARGET`.

- The internal panel address used by "Chiama Casa (interno)" moved to
  `const.INTERNAL_PANEL_TARGET`. It is **not** configurable: there is no config entry field
  and no `rubrica.db` key to derive it from, and guessing it (SGA+1) is exactly the kind of
  assumption this project does not make. On a different plant that button will call an
  address that does not exist and the call will fail — no side effect, unlike the door.

`tests/test_no_hardcoded_plant_values.py` now fails if a plant address reappears as a
literal in `lock.py`, `button.py` or `switch.py`, and `tests/test_hub_stats.py` covers the
targetless `async_door()` path the two entities now rely on.

## [1.0.4] - 2026-09-19

Security fix. Anyone who paired with a QR code should update.

- **The SIP password was readable over HTTP by any logged-in Home Assistant user.**
  Three pieces, each harmless on its own. `qr_decoder` logged the decrypted pairing
  payload at DEBUG — and that payload contains `PWD=<your SIP password>`. The internal
  ring buffer raises the `vimar_intercom` logger to DEBUG unconditionally, so that line
  was captured whether or not you had configured `logger:`. And `/api/vimar_intercom/debug`
  serves that buffer to any authenticated user, guest accounts included.

  All three are now closed: the payload is never logged (only how many fields were found
  and their names, which is what actually helps diagnose a wrong QR), the debug endpoint
  requires an **administrator**, and every line entering the buffer passes through a new
  `log_redact` module that masks credential-shaped values — `pwd`/`password`/`ha1`/`token`
  assignments, `Authorization` and `Proxy-Authorization` headers, Digest `response=`
  fields, and the `{"PARAM":"token","VALUE":"…"}` form carried by
  `GET_INIT_STATUS_REPLY`. The masking is a safety net, not the rule: credentials must not
  be logged in the first place.

  If your Home Assistant has non-administrator users, or you have ever shared a debug
  dump, treat the SIP password as exposed and re-pair from the intercom panel to rotate it.

New tests in `tests/test_log_redact.py`, plus two in `tests/test_qr_decoder.py` that fail
if the password ever reaches a log record again.

## [1.0.3] - 2026-09-18

Three bugs in `hub.py`, all found by a code audit rather than in the field, and all of the
kind that fails without looking like a failure.

- **Video auto-start never worked.** Opening the camera stream is supposed to place a SIP
  call to the video entry panel. The URI was built from `sip.C.SIP_DOMAIN` — but `sip_client`
  imports `const as C`, and `SIP_DOMAIN` does not exist there: the active domain lives in
  `runtime`, because it differs between local UDP and cloud mode. Every auto-call therefore
  raised `AttributeError`, which the surrounding `except` turned into a single
  `Auto-call error:` line, so the stream simply showed nothing. `CAMERA_TARGET` was reached
  through the same wrong module. Both now read from where the value actually lives.
- **After a call ended, the doorbell stopped ringing.** `_auto_called` marks a call we
  placed ourselves, so that the INVITE the plant echoes back is not announced as a doorbell
  ring. It was cleared on six paths but not when the call ended — and the watchdog that
  would have cleared it requires `sip.in_call`, which is already `False` by then. From that
  point on every genuine ring matched the "we started this" test and was answered with
  `603 Decline`, until Home Assistant was restarted. It is now cleared on `call_ended`.
- **A failed `GET_INIT_STATUS` was never retried.** The flag marking the request as sent was
  set regardless of the outcome, which made the retry branch in the keepalive
  (`if not self._init_status_sent`) unreachable. One transient failure left `rubrica_ver`,
  `vm_ver`, `vm_level` and the real voicemail/DND states at `None` until a restart. The flag
  now follows the result of the send.

New regression tests in `tests/test_hub_autocall.py`, including a guard that fails if a
`SIP_DOMAIN` constant reappears in `const.py`.

Note for plants that answer `200 OK` to `GET_INIT_STATUS` but never send the reply
([#1](https://github.com/lollox80/ha-vimar-intercom/issues/1)): this release does not change
that behaviour — the send succeeds, so the retry is not triggered. That case is tracked
separately.

## [1.0.2] - 2026-09-17

- **Per-installation device identity.** `const.py` shipped `DEVICE_IMEI = "351234567890123"` (and `DEVICE_UUID = DEVICE_IMEI`), a constant every installation sent in the SIP `Mobile-IMEI` header and in the `+sip.instance` contact parameter. The Vimar cloud ties a registration — and its push routing — to the device identity, so two plants presenting the same one compete for the same registration. The identity is now generated once per installation by `runtime.new_device_identity()` (15-digit IMEI, random UUIDv4) and stored in the config entry; existing entries are migrated silently on the next start, and `runtime.configure()` falls back to a fresh ephemeral identity when none is stored, so no code path can reuse a shared value. `const.py`, `runtime.py`, `__init__.py`, `sip_client.py`. New tests in `tests/test_device_identity.py`, including a regression guard that fails if a hardcoded identity reappears in `const.py`.
- **The cloud SNI and `Route` header come from the config entry.** `const.SIP_SNI` and `const.SIP_ROUTE` were fixed to `ipvdes.vimar.cloud` even though the pairing QR carries the plant's own `cproxy` (already stored as `runtime.SIP_PROXY`). On an installation whose `cproxy` differs, the TLS handshake presented the wrong server name and the `Route` header pointed at the wrong proxy. Both now use `runtime.SIP_PROXY`, and the two constants are gone. `const.py`, `sip_client.py`.
- **Options flow keeps what you typed.** All three option steps rebuilt their form from the saved entry data, so a validation error on one field silently discarded every other edit made alongside it — the rubrica GID included. The redisplayed form now starts from the submitted values; the saved ones remain the fallback, and the change-detection used to decide whether to re-run the live SIP test still compares against what is actually stored. `config_flow.py`.
- Door statistics no longer record a hardcoded `"55001"` as the target when none is given: `SGA_TARGET` has been configurable since 1.0.0, and `hub.py` now reports the configured one. New guards in `tests/test_no_hardcoded_plant_values.py` fail if any of these plant-specific values reappear as constants.
- **`do_call()` no longer strands the `calling` flag.** The INVITE transaction cleaned up on each `return` path but had no `finally`: if `parse_sdp()` or `media.setup_media()` raised after the 200 OK, `pending_responses` kept the queue and `calling` stayed `True` forever — and the guard at the top of `do_call()` then refused every later call until Home Assistant was restarted. The response loop is now wrapped in `try`/`finally` that always pops the queue and clears `calling`. `sip_client.py`.

## [1.0.1] - 2026-09-17

- **Fixed cloud SIP registration on Tab 5S UP** ([#1](https://github.com/lollox80/ha-vimar-intercom/issues/1), reported by @gtarraran992): the pairing QR code carries two distinct SIP domains — `domain` (the intercom's local domain) and `cdomain` (the cloud one) — but the decoder only kept the first, and on some Tab 5S UP units that field is `127.0.0.1`. The result was `sip:<user>@127.0.0.1` URIs and cloud registration failing every time. `qr_decoder.extract_sip_credentials()` now keeps both domains (`local_domain`/`cloud_domain`) and picks the local one as the pairing-time default only when it is actually routable — loopback, `0.0.0.0` and `localhost` fall back to `cdomain` — while `runtime.configure()` selects the active domain based on `use_local_udp`, recomputing HA1 when the active mode's domain differs from the saved one. Config entries created by earlier versions have neither of the new keys and keep using `sip_domain` as before. `qr_decoder.py`, `runtime.py`. New tests in `tests/test_qr_domain_selection.py`.
- Added `CONTRIBUTING.md` and issue templates (bug report, hardware compatibility report).

## [1.0.0] - 2026-09-07

**Prima release pubblica** su `github.com/lollox80/ha-vimar-intercom` (HACS custom repository).
Le versioni precedenti (2.0.0–3.1.4, più sotto) sono state sviluppo privato su un singolo impianto,
mai distribuite pubblicamente: restano nel changelog come storico/riferimento "beta" pre-1.0.

- **`sga_target`/`picg_target` configurabili** (gap principale pre-pubblicazione): erano hardcoded a `"55001"` in giro per il codice, un valore verificato solo su questo impianto — impianti diversi possono avere un SGA/PICG diverso. Ora sono in `runtime.SGA_TARGET`/`runtime.PICG_TARGET`, popolati da `runtime.configure()` da `options["sga_target"]`/`["picg_target"]` con fallback al default storico in `const.py`. Due nuovi campi testo nello step "Impostazioni" dell'options flow (validati: solo cifre); l'importer rubrica.db, alla conferma, li imposta entrambi automaticamente dal valore rilevato (`SYSTEM.MAGIC_APT_INTERCOM`) invece di limitarsi a segnalarlo. Aggiornati anche gli usi hardcoded residui: `switch.py` (target segreteria/DND), `button.py` (sentinella target "AUTO"), `hub.py` (target di `GET_INIT_STATUS`), e i due servizi HA `send_command`/`open_door` in `__init__.py` (default dinamico invece di `"55001"` fisso). `CAMERA_TARGET` (targa video, `"55100"`) resta fuori scope. `runtime.py`, `hub.py`, `switch.py`, `button.py`, `__init__.py`, `config_flow.py`, `strings.json`, `translations/{it,en}.json`. Nuovi test in `tests/test_runtime.py`; `runtime` aggiunto a `PURE` in `tests/test_smoke.py`.
- **Importer `rubrica.db` nell'options flow**: nuovo menu nelle opzioni dell'integrazione con due voci, "Impostazioni di rete e attuatori" (il form esistente, ora sullo step `settings`) e "Importa attuatori da rubrica.db". Quest'ultimo fa caricare il file `rubrica.db` (selettore file nativo HA, `homeassistant.components.file_upload`), lo legge in sola lettura, estrae attuatori + parametri `SYSTEM` (stessa logica read-only di `tools/parse_rubrica.py`, isolata nel nuovo modulo puro `rubrica_import.py`) e mostra un riepilogo di conferma (numero/nome attuatori trovati, SGA rilevato, con avviso se diverso da quello attualmente in uso) prima di sostituire la lista attuatori salvata — senza più copiare a mano l'output JSON del tool. Nuova dipendenza manifest: `file_upload`. `config_flow.py`, `rubrica_import.py` (nuovo), `strings.json`, `translations/{it,en}.json`, `manifest.json`. Test dedicati in `tests/test_rubrica_import.py` (schema completo, filtro per GID, fallback senza `ACTUATOR_RULES`/`ICON_LIST`, file mancante/non-SQLite, sola lettura); aggiunto a `PURE` in `tests/test_smoke.py`.
- Fix logging: il buffer di debug interno (`_debug_log`) alzava il logger `custom_components.vimar_intercom` a DEBUG, e per propagazione ai logger Python questo scavalcava il livello WARNING impostato in `logger:` di HA. Ora il logger ha `propagate = False` e un handler dedicato inoltra al log HA solo WARNING+. `__init__.py`, `README.md`.
- Fix log "Stale response 407" sul keepalive SIP: `_send_options_ping` non registrava il proprio Call-ID tra le risposte attese, quindi la risposta del proxy al keepalive OPTIONS finiva loggata come WARNING anche se normale. `_dispatch_message` ora declassa a DEBUG le risposte con Call-ID `ping-*`. Con questa fix + quella sopra non serve più alcun filtro `logger:` in `configuration.yaml`. `sip_client.py`, `README.md`.

---

## Storico pre-1.0.0 — sviluppo privato, versioni "beta" mai pubblicate

> Numerazione interna usata durante lo sviluppo su un solo impianto privato, prima della release
> pubblica. Tenuta per riferimento/tracciabilità, non corrisponde a versioni mai distribuite.

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
