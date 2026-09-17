# Contributing

Thanks for taking the time to contribute. This integration was reverse-engineered
on a single system (Elvox Tab 7S 2F+ WiFi, art. 40507), so reports and patches from
other hardware are genuinely valuable — especially Tab 5S / 2FV2 / IP plants.

## Ground rules

**1. Never guess SIP commands or tokens.**
This is the single most important rule. A wrong actuator token can physically open
a door or a gate. Anything that goes into the code as an *active* command must be
either documented in `docs/PROTOCOL.md` or extracted from the official VIEW app
(`apk/`). Everything else goes into `docs/ROADMAP.md` marked as a hypothesis.

**2. Don't put credentials in the repo.**
No SIP passwords, QR payloads, HA1 hashes, MAC addresses or cloud tokens — not in
code, not in tests, not in issue attachments. Scrub logs before posting them
(see the redaction list in the issue template). QR payloads must never be logged
above `DEBUG` level.

**3. Keep the HTTP endpoints locked down.**
`/video` and `/av` stay behind `_is_local_request()`; the `/audio_ws` WebSocket
stays `requires_auth = True`. A PR that loosens either needs a very good reason
in its description.

## Development setup

```bash
git clone https://github.com/lollox80/ha-vimar-intercom
cd ha-vimar-intercom
pip install -r requirements-dev.txt
```

Targets: Home Assistant **2024.1+**, Python **3.11+** (HA ships 3.11/3.12/3.13).

Runtime dependencies are deliberately minimal: `pycryptodome` and `requests`.
**No external SIP library** — the stack in `sip_client.py` is custom and stays
custom. Please don't add `cryptography` as a direct import either; `pycryptodome`
covers what we need and HA already pins it.

## Before you open a PR

```bash
python -m pytest tests/ -q          # must be green
python -m py_compile custom_components/vimar_intercom/*.py
```

Tests don't need a real Home Assistant instance: the modules under test are pure
Python. If you touch `sip_client.py`, `hub.py`, `runtime.py` or `qr_decoder.py`,
add a test — those are the parts that break silently on other plants.

`tools/sip_probe.py` lets you test protocol behaviour from a PC without touching
your Home Assistant install:

```bash
python tools/sip_probe.py options --target 55002   # who answers, and with which UA
python tools/sip_probe.py listen --seconds 900     # capture announcements/events
```

## PR checklist

- [ ] Tests pass, and new behaviour has a test
- [ ] `manifest.json` version bumped (semver) and a `CHANGELOG.md` entry added
- [ ] UI strings kept in sync across `strings.json`, `translations/it.json` and `translations/en.json`
- [ ] No credentials, MAC addresses or raw QR payloads anywhere in the diff
- [ ] Says which hardware you tested on: model, article number, firmware, and whether local UDP or cloud TLS
- [ ] Non-obvious design decisions written up in `docs/DECISIONS.md` (short ADR format)

Small, focused PRs get merged faster than big ones. If you're planning something
large — a new transport, a rewrite of the media pipeline, cloud REST support —
open an issue first so we don't both build it.

## Logging

`_LOGGER.debug` for SIP traffic, `_LOGGER.info` for user-facing events, never
`print`. The integration keeps its own circular debug buffer so HA's log stays
readable; don't bypass it.

## Reporting hardware compatibility

Got it working on a model that isn't the Tab 7S? Please open a
**Hardware compatibility report** issue even if everything worked — knowing what
*doesn't* need changing is as useful as knowing what does.
