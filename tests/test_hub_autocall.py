"""Regressioni sul percorso di autoaccensione e sullo stato iniziale (v1.0.3).

Tre bug che convivevano nello stesso file e che si vedevano solo sul campo:

* l'URI dell'auto-call veniva costruito su `sip.C.SIP_DOMAIN`, che non esiste:
  il dominio vive in `runtime`, non in `const`;
* `_auto_called` non veniva azzerato quando la chiamata finiva, e da lì in poi
  ogni squillo vero veniva scambiato per l'eco della nostra chiamata;
* `_init_status_sent` veniva messo a True anche quando l'invio falliva, il che
  rendeva irraggiungibile il retry previsto nel keepalive.
"""
from __future__ import annotations

import asyncio

import pytest

hub_mod = pytest.importorskip("custom_components.vimar_intercom.hub")

sip = hub_mod.sip
C = hub_mod.C
R = hub_mod.R


@pytest.fixture
def hub(monkeypatch):
    h = hub_mod.VimarIntercomHub()
    monkeypatch.setattr(h, "_touch", lambda: None)
    return h


# ─── A1: il dominio SIP si legge da runtime, mai da const ────────────────────

def test_const_non_espone_un_dominio_sip():
    """Se un giorno `SIP_DOMAIN` ricomparisse in const, il bug potrebbe tornare
    silenziosamente: l'auto-call userebbe di nuovo un valore statico invece di
    quello attivo (che cambia fra modalità locale e cloud)."""
    assert not hasattr(C, "SIP_DOMAIN")


def test_auto_call_usa_il_dominio_attivo(hub, monkeypatch):
    chiamate = []

    async def _fake_do_call(target=None):
        chiamate.append(target)
        return True, "200"

    monkeypatch.setattr(sip, "do_call", _fake_do_call)
    monkeypatch.setattr(R, "SIP_DOMAIN", "impianto.example", raising=False)

    asyncio.run(hub._do_auto_call("55100"))

    assert chiamate == ["sip:55100@impianto.example"]


def test_auto_call_senza_target_chiama_la_targa_video(hub, monkeypatch):
    """Senza target esplicito si chiama CAMERA_TARGET (55100), non il PICG."""
    chiamate = []

    async def _fake_do_call(target=None):
        chiamate.append(target)
        return True, "200"

    monkeypatch.setattr(sip, "do_call", _fake_do_call)
    monkeypatch.setattr(R, "SIP_DOMAIN", "impianto.example", raising=False)

    asyncio.run(hub._do_auto_call(None))

    assert chiamate == [f"sip:{C.CAMERA_TARGET}@impianto.example"]


# ─── A2: fine chiamata → gli squilli successivi tornano veri ─────────────────

def test_call_ended_azzera_auto_called(hub):
    """Il watchdog `_delayed_hangup` non copre questo caso: la sua guardia
    richiede `sip.in_call`, che dopo call_ended è già False."""
    hub._auto_called = True

    asyncio.run(hub._handle_broadcast("call_ended", ""))

    assert hub._auto_called is False


def test_squillo_dopo_una_chiamata_chiusa_non_viene_soppresso(hub, monkeypatch):
    """Il sintomo osservato: chiusa una chiamata dal citofono, ogni squillo
    successivo veniva rifiutato con 603 Decline finché non si riavviava HA."""
    declines = []

    async def _fake_decline():
        declines.append(True)

    monkeypatch.setattr(sip, "do_decline_incoming", _fake_decline)
    monkeypatch.setattr(sip, "in_call", False, raising=False)
    monkeypatch.setattr(sip, "calling", False, raising=False)

    squilli = []
    hub.register_ring_callback(lambda: squilli.append(True))

    hub._auto_called = True
    asyncio.run(hub._handle_broadcast("call_ended", ""))
    asyncio.run(hub._handle_broadcast("ring", ""))

    assert squilli, "lo squillo è stato soppresso"
    assert not declines


# ─── A3: il retry dello stato iniziale deve restare raggiungibile ────────────

def test_init_status_non_si_marca_inviato_se_fallisce(hub, monkeypatch):
    async def _fail(**kwargs):
        return False, "timeout"

    monkeypatch.setattr(hub, "async_send_command", _fail)

    asyncio.run(hub._request_init_status())

    assert hub._init_status_sent is False, "il retry del keepalive non scatterebbe mai"


def test_init_status_si_marca_inviato_se_riesce(hub, monkeypatch):
    async def _ok(**kwargs):
        return True, "200"

    monkeypatch.setattr(hub, "async_send_command", _ok)

    asyncio.run(hub._request_init_status())

    assert hub._init_status_sent is True
