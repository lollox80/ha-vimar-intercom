# Vimar Intercom — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

🇮🇹 *[Leggi questa pagina in italiano](README.it.md)*

Brings the **Vimar Elvox** video intercom (2 Fili Plus / IP / 2FV2) into Home Assistant: get the
doorbell ring, open the door or gate, view the camera **on demand**, control **voicemail** and
**do not disturb**, and drive your plant's **actuators** (F1/F2, stair lights, relays) as buttons.

> **How it actually works.** This integration does **not** use RTSP. It implements a **custom SIP
> stack in Python/asyncio** that emulates the official **Vimar VIEW** app ("TOGA"): same `User-Agent`,
> same identity headers (`Mobile-IMEI`, `MyName`) and the proprietary **`Panda`** header. It talks
> either to the **Flexisip instance running on the Tab** (local UDP :5060) or to the **Vimar cloud over
> TLS** (SRV `_sips._tcp`). Video arrives **on demand** from the SIP call (H.264 RTP, decoded to MJPEG
> through ffmpeg), not from an always-on RTSP stream.

---

## Compatibility

Developed on the **Elvox Tab 7S 2F+ WiFi (art. 40507)**. Other Vimar 2F / 2FV2 / IP Tabs should work
too — the configuration comes from the pairing QR code — and the table below is what has actually been
reported so far.

| Model | Art. | Plant | Firmware | Connection | Status |
|---|---|---|---|---|---|
| Elvox Tab 7S 2F+ WiFi | 40507 | 2F | — | local UDP | Development platform: ring, call, answer/hang up, door open, on-demand video, actuators |
| Elvox Tab 5S UP 2 Wire WiFi | 40515 | 2FV2 | 2.1.0203 | cloud TLS | Working, reported by @CPietro — see the notes below |
| Elvox Tab 5S UP 2 Wire WiFi | 40515 | — | — | cloud TLS | Cloud registration working after the 1.0.1 fix, reported by @gtarraran992 ([#1](../../issues/1)) |

**What differs between plants.** The two Tab 5S reports made one thing clear: *the SIP state commands
are plant-dependent, not universal*.

- On the 40515 / 2FV2 plant, **DND works** — the Tab accepts the command and the official VIEW app
  even shows a notification. **Voicemail turns on but not off** (still under investigation). And
  `GET_INIT_STATUS` **does** get a reply, e.g.
  `GET_INIT_STATUS_REPLY;[{"PARAM":"dnd","VALUE":"0"},{"PARAM":"voicemail","VALUE":"…"}]`.
- On the 40507 / 2F plant used for development, none of that happens: every state command returns a
  bare 200 OK with no effect, and `GET_INIT_STATUS` is never answered. Packet captures there show the
  VIEW app sending no SIP at all when you toggle voicemail — the Tab only *announces* its new state.

So if the voicemail/DND switches do nothing on your plant, that is expected behaviour for some
installations rather than a misconfiguration on your side.

Cloud-mode plants also need their SGA/PICG addresses set to match their own numbering — the defaults
come from the development plant. See the options table below and `docs/RUBRICA.md`.

Got it running on a different model, or on the same one with different results? Please open a
[hardware compatibility report](../../issues/new?template=compatibility_report.yml) — reports where
everything just worked are as useful as the ones where something broke.

---

## Requirements

- Home Assistant **2024.1** or later, Python 3.11+.
- ffmpeg on the Home Assistant host (declared in the manifest) for the camera.
- The plant's **pairing QR code** (from the VIEW app) **or** the SIP parameters entered by hand
  (id, password, domain, cloud proxy).
- Python requirements: only `pycryptodome` and `requests` — no external SIP library, the stack is custom.

---

## Installation

### Through HACS
1. HACS → Integrations → ⋮ menu → *Custom repositories* → add this repo with category *Integration*.
2. Install **Vimar Intercom**.
3. Restart Home Assistant.

### Manual
Copy `custom_components/vimar_intercom/` into your Home Assistant `config/custom_components/` folder
and restart.

> **If you reinstall or update by hand**: `__init__.py` and `sip_client.py` carry local logging
> patches (not present upstream — see the *Logging* section below). If you overwrite those files with
> a copy from somewhere else, reapply the patches: outside HACS nothing preserves them for you.

---

## Configuration

Settings → Devices & services → Add integration → **Vimar Intercom**.

- **QR** (recommended): paste the text of the Vimar pairing QR code. The integration decodes it
  (`qr_decoder.py`: Base64(AESkey|AES-CBC|IV) → `KEY=VALUE` pairs) and fills in id, password, domain,
  cloud/local proxy, GID, MAC and plant type.
- **Manual**: enter `sip_user`, `sip_password`, `sip_domain` and `cloud_proxy` yourself.

### Options (after adding the integration)

Settings → Vimar Intercom → **Configure**:

| Option | Description |
|---|---|
| **Intercom IP** (`local_proxy`) | IP address of the Flexisip instance on the Tab |
| **Use local SIP UDP** (`use_local_udp`) | ON = local UDP; OFF = cloud TLS |
| **Local UDP port** (`local_udp_port`) | default 5060 |
| **Actuators (JSON)** (`actuators`) | JSON list of `{name, msg, target, icon}`; creates dynamic buttons. Empty = no buttons |
| **SGA** (`sga_target`) | Recipient of `VOICEMAIL;`/`DND;` and of the "AUTO" door open. Empty = default `55001` |
| **PICG** (`picg_target`) | Recipient of `GET_INIT_STATUS`. On every plant verified so far it matches the SGA. Empty = default `55001` |

The actuator list and the SGA/PICG values come from your plant's **phonebook** (`rubrica.db`): in the
options menu pick **"Import actuators from rubrica.db"**, upload the file (you can get it through the
VIEW app or with root access, see `docs/RUBRICA.md`) and confirm — actuators, SGA and PICG are then
set automatically. You can also enter the values by hand in the "Settings" step, which is handy if you
already know your plant's SGA or want to tweak the imported actuator list.

---

## Entities

| Entity | Platform | Description |
|---|---|---|
| Intercom | `camera` | **On-demand** video: opening the stream makes the hub place the SIP call, and the H.264 RTP video is decoded to MJPEG through ffmpeg (no RTSP) |
| Doorbell | `event` | `event` entity (device class DOORBELL), event type `ring`, fired on ring (incoming INVITE) |
| Lock | `lock` | Opens the door (`OPEN_2F` → outdoor unit); auto-relocks after 5 s (there is no physical feedback) |
| Call | `button` | SIP call to the default outdoor unit |
| Call Video (outdoor) / Call Home (indoor) | `button` | Call to 55001 / 55002 |
| Answer / Hang up | `button` | Answer (200 OK) / end the call (BYE) |
| Open Door | `button` | `OPEN_2F` to the outdoor unit |
| *Dynamic actuators* | `button` | One per entry in `options["actuators"]` (F1/F2, stair lights, relays…); sends `MSG` with `Panda: command` |
| Voicemail | `switch` | `VOICEMAIL;ON/OFF` (Panda: blue) to the SGA; state read from the Tab's announcements |
| Do Not Disturb | `switch` | `DND;ON/OFF` (Panda: blue) to the SGA; real state |
| Intercom SIP | `binary_sensor` | SIP registration active (connectivity) |
| Intercom In Call | `binary_sensor` | A call is up |
| Intercom Ringing | `binary_sensor` | ON while an outdoor unit is calling (attribute: caller) |
| Intercom Outgoing Call | `binary_sensor` | ON while Home Assistant is calling |
| Intercom State | `sensor` (enum) | offline / idle / ringing / in_call / calling (plus network attributes) |
| Intercom Last Caller | `sensor` | Outdoor unit or monitor of the last ring |
| Intercom Last Ring | `sensor` (timestamp) | Time of the last ring |
| Intercom Rings | `sensor` (counter) | Rings since startup |
| Intercom Calls | `sensor` (counter) | Connected calls |
| Intercom Last Call Duration | `sensor` (s) | Duration of the last call |
| Intercom Last Door Open | `sensor` (timestamp) | Last door opening (attributes: unit, outcome, counter) |
| Intercom Last Command | `sensor` | Outcome of the last `send_command` |
| Intercom Last Received Message | `sensor` | Last SIP MESSAGE from the intercom |

---

## Services (`services.yaml`)

| Service | Description | Fields |
|---|---|---|
| `vimar_intercom.send_command` | Arbitrary SIP MESSAGE (for testing) | `body`, `target`, `header_name`, `header_value` |
| `vimar_intercom.call` | SIP call to an outdoor unit or monitor | `target` |
| `vimar_intercom.answer` | Answers the incoming call | — |
| `vimar_intercom.hangup` | Ends the active call | — |
| `vimar_intercom.open_door` | Door open command (`OPEN_2F`) | `target`, `command` |
| `vimar_intercom.fetch_local` | HTTP Digest GET against the Tab's local interface (home mode) | `path`, `save_as`, `host`, `scheme` |

Example (Developer tools → Actions):

```yaml
action: vimar_intercom.send_command
data:
  body: OPEN_2F
  target: "55001"
  header_name: Panda
  header_value: command
```

---

## Events

The doorbell is exposed as an **`event` entity** (`event.<...>_doorbell`, event type `ring`), not as a
bus event. In automations, use a state trigger on the `event` entity (or on the ringing binary sensor).

On top of that, from the `MESSAGE`s the Tab sends, the integration fires these events on the Home
Assistant bus: `vimar_intercom_missed_call`, `vimar_intercom_videomessage`,
`vimar_intercom_fuoriporta`, `vimar_intercom_call_info`, `vimar_intercom_phonebook_changed`.
They are read-only — nothing is sent back. Example trigger:

```yaml
automation:
  - alias: "Intercom - Missed call"
    trigger:
      - platform: event
        event_type: vimar_intercom_missed_call
    action:
      - service: notify.mobile_app_phone
        data: { message: "Missed call at the intercom" }
```

---

## Example automations

`packages/vimar_intercom.yaml` (copy it into `config/packages/`) contains a
"ring → snapshot + 15 s clip → notification" automation, driven by the state change of the `event`
entity:

```yaml
automation:
  - alias: "Intercom - Ring → snapshot and clip"
    trigger:
      - platform: state
        entity_id: event.vimar_intercom_doorbell
    action:
      - service: camera.snapshot
        target: { entity_id: camera.vimar_intercom_intercom }
        data: { filename: "/media/vimar/intercom_{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg" }
      # ... clip + notify (see packages/vimar_intercom.yaml)
```

---

## Known limitations

- **Cloud-only plants**: the Tab's local HTTP interface (:80) may accept the TCP connection and then
  stay silent, so there is no local phonebook to read. Camera, actuators and door opening still work
  over SIP. Reading the initial state with `GET_INIT_STATUS` depends on the plant: some answer with a
  `GET_INIT_STATUS_REPLY`, others never do (see *Compatibility*).
- **Voicemail / DND**: whether these can be *commanded* at all depends on the plant — see
  *Compatibility* above. Where they work, the command goes to the **SGA**
  (`SYSTEM.MAGIC_APT_INTERCOM` in the phonebook, `55001` on the plant used for development).
  Configurable in the options (**SGA**/**PICG**) or through the automatic `rubrica.db` import.
- **By-me actuators** (e.g. stair lights on By-me home automation): these may not respond over SIP even
  when they are listed in the phonebook.
- **Lock**: no physical state feedback (optimistic auto-relock after 5 s).
- **Phonebook**: on cloud-only plants it has to be extracted once (see `docs/RUBRICA.md`); the
  automatic import over the cloud depends on a token provisioned by the account.

---

## Logging

The component keeps an internal circular buffer (`_debug_log`, in `__init__.py`) for its own
diagnostics, and raises its logger to `DEBUG` to fill it. By default that would propagate every
`DEBUG` line to the Home Assistant log too, overriding the level set in `logger:` in
`configuration.yaml` (Python loggers propagate to the root).

The patch: the `custom_components.vimar_intercom` logger stays at `DEBUG` for the internal buffer, but
with `propagate = False`; a dedicated handler forwards only `WARNING` and above to the HA log. Result:
internal diagnostics intact, HA log clean.

**"Stale response 407" on the SIP keepalive**: the periodic OPTIONS (`_send_options_ping` in
`sip_client.py`) did not register its own Call-ID among the expected responses, so the proxy's reply
(typically a `407`) was logged as `WARNING "Stale response ..."` even though it is the normal outcome
of the keepalive. `_dispatch_message` now recognises Call-IDs prefixed with `ping-` and logs them at
`DEBUG` instead. With this fix and the one above, **no** `logger:` filter in `configuration.yaml` is
needed any more to silence these messages.

If you update `__init__.py` or `sip_client.py` from an external source (not HACS, not versioned for
this component), check that both patches are still in place — see the note under
*Installation → Manual*.

---

## Security

- SIP credentials (password / `ha1`) are stored **encrypted** in the Home Assistant config entry, never
  in plain text in the repo.
- Internal HTTP endpoints: `/video` and `/av` are **LAN-only** (`_is_local_request`); the `/audio_ws`
  WebSocket requires Home Assistant authentication. The QR payload is never logged at INFO level.
- No mandatory cloud dependency when running in local UDP mode.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: never guess SIP commands or tokens (a wrong
actuator token can physically open a door), keep credentials out of the repo and out of your logs, run
`pytest` before opening a PR, and say which hardware you tested on.

---

## Disclaimer

This project is **not affiliated with or endorsed by Vimar S.p.A.**. "Vimar", "Elvox" and "VIEW" are
trademarks of their respective owners. You supply your own credentials for your own plant.

## License

**MIT** — © [Noise Heroes](https://github.com/noiseheroes-lab) (upstream project) and the fork's
contributors.
