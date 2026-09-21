"""Ricerca del PICG con GET_NICKS (servizio find_sga, issue #14).

Il PICG lo dichiara il citofono nella GET_NICKS_REPLY; la scansione serve solo a
trovare un indirizzo che la faccia partire. GET_NICKS e non GET_INIT_STATUS
perché il primo è silenzioso sull'app [VERIFICATO 21/09]. Valori anonimi.
"""
from __future__ import annotations

import asyncio

import pytest

hub_mod = pytest.importorskip("custom_components.vimar_intercom.hub")
from custom_components.vimar_intercom import rest_client, runtime, validate  # noqa: E402
from custom_components.vimar_intercom import sip_client as sip  # noqa: E402

REPLY = ('GET_NICKS_REPLY;[{"ROLE": "PICG", "EXT": "55001", "NAME": "Casa"}, '
         '{"ROLE": "PIM", "EXT": "60993", "NAME": "Tel A"}, {"ROLE": "PIM", "EXT": "60992", "NAME": "Tel B"}]')


# ─── parser ──────────────────────────────────────────────────────────────────

def test_reply_completa():
    nicks = rest_client.parse_nicks_reply(REPLY)
    assert [n["ext"] for n in nicks] == ["55001", "60993", "60992"]
    assert rest_client.find_picg(nicks) == "55001"


def test_reply_troncata_conserva_il_picg():
    nicks = rest_client.parse_nicks_reply(REPLY[:110])
    assert rest_client.find_picg(nicks) == "55001"


@pytest.mark.parametrize("body", ["", "GET_NICKS_REPLY;", "GET_NICKS_REPLY;[{", "spazzatura"])
def test_reply_illeggibile_non_solleva(body):
    assert rest_client.parse_nicks_reply(body) == []


def test_array_senza_prefisso():
    assert rest_client.find_picg(rest_client.parse_nicks_reply(REPLY.split(";", 1)[1])) == "55001"


# ─── indirizzi da interrogare ────────────────────────────────────────────────

def test_intervallo():
    assert validate.scan_targets("55000", "55003") == ["55000", "55001", "55002", "55003"]


def test_lista_ha_precedenza_e_toglie_i_doppioni():
    assert validate.scan_targets("1", "2", "55001, 55002 55001;55100") == ["55001", "55002", "55100"]


def test_zeri_iniziali_conservati():
    assert validate.scan_targets("0098", "0101") == ["0098", "0099", "0100", "0101"]


@pytest.mark.parametrize("args", [
    ("55000", "55100", None),          # 101 indirizzi
    ("55010", "55000", None),          # rovesciato
    ("55a", "55010", None),
    (None, None, "55001, sip:x"),
    (None, None, "55001\r\nX: y"),
])
def test_ingressi_rifiutati(args):
    with pytest.raises(ValueError):
        validate.scan_targets(*args)


def test_limite_anche_sulla_lista():
    lista = ",".join(str(55000 + i) for i in range(validate.MAX_SCAN_TARGETS + 1))
    with pytest.raises(ValueError):
        validate.scan_targets(targets=lista)


# ─── la scansione ────────────────────────────────────────────────────────────

@pytest.fixture
def hub(monkeypatch):
    runtime.configure({"sip_user": "u", "sip_password": "p", "sip_domain": "x.test"})
    h = hub_mod.VimarIntercomHub()
    monkeypatch.setattr(h, "_touch", lambda: None)
    return h


def _impianto(monkeypatch, hub, risposte: dict[str, str], replica_da: str | None = "55001",
              ritardo: float = 0.0):
    """Finto impianto: `risposte` target → esito di do_system_message.
    Se il target è `replica_da`, dopo `ritardo` s arriva la GET_NICKS_REPLY."""
    inviati: list[tuple[str, str, dict]] = []

    async def _dsm(uri, body, extra_headers=None):
        target = uri.split(":", 1)[1].split("@", 1)[0]
        inviati.append((target, body, extra_headers))
        esito = risposte.get(target, "Errore: 404")
        if target == replica_da:
            async def _reply():
                await asyncio.sleep(ritardo)
                hub._update_stats("message", REPLY)
            asyncio.get_running_loop().create_task(_reply())
        return (esito.startswith("OK"), esito)

    monkeypatch.setattr(sip, "do_system_message", _dsm)
    return inviati


def test_si_ferma_al_primo_che_fa_rispondere(monkeypatch, hub):
    inviati = _impianto(monkeypatch, hub, {"55001": "OK (200)", "55002": "OK (200)"})
    r = asyncio.run(hub.async_find_picg(["55000", "55001", "55002"], reply_wait=1, delay=0.01))
    assert r["ok"] and r["picg"] == "55001" and r["reply_after"] == "55001"
    assert [p["outcome"] for p in r["probes"]] == ["absent", "replied"]
    assert [t for t, *_ in inviati] == ["55000", "55001"], "dopo la risposta non deve interrogare altro"
    assert hub.stats["picg_declared"] == "55001"


def test_usa_get_nicks_con_panda_blue_mai_get_init_status(monkeypatch, hub):
    inviati = _impianto(monkeypatch, hub, {"55001": "OK (200)"})
    asyncio.run(hub.async_find_picg(["55000", "55001"], reply_wait=1, delay=0.01))
    assert all(body == "GET_NICKS" and h == {"Panda": "blue"} for _, body, h in inviati)


def test_nessuna_risposta_e_la_regola_dei_tre_esiti(monkeypatch, hub):
    _impianto(monkeypatch, hub, {"55001": "OK (200)", "55003": "Timeout"}, replica_da=None)
    r = asyncio.run(hub.async_find_picg(["55000", "55001", "55003"], reply_wait=1, delay=0.01))
    assert r["ok"] and r["picg"] is None
    assert [p["outcome"] for p in r["probes"]] == ["absent", "exists", "no_response"]


def test_risposta_in_ritardo_viene_raccolta(monkeypatch, hub):
    """La reply arriva dopo la finestra d'attesa della sonda che l'ha provocata."""
    _impianto(monkeypatch, hub, {"55001": "OK (200)", "55002": "OK (200)"}, ritardo=1.3)
    r = asyncio.run(hub.async_find_picg(["55001", "55002", "55003"], reply_wait=1, delay=0.5))
    assert r["picg"] == "55001"
    assert r["probes"][-1].get("late_reply") is True


def test_non_registrato_interrompe(monkeypatch, hub):
    _impianto(monkeypatch, hub, {"55000": "Non registrato"}, replica_da=None)
    # "Non registrato" non inizia con OK: l'esito è False
    r = asyncio.run(hub.async_find_picg(["55000", "55001"], reply_wait=1, delay=0.01))
    assert r["ok"] is False and "Non registrato" in r["error"]


def test_una_ricerca_alla_volta(monkeypatch, hub):
    _impianto(monkeypatch, hub, {"55001": "OK (200)"}, replica_da=None)

    async def scenario():
        prima = asyncio.create_task(hub.async_find_picg(["55001", "55002"], reply_wait=1, delay=0.01))
        await asyncio.sleep(0.05)
        seconda = await hub.async_find_picg(["55001"], reply_wait=1, delay=0.01)
        await prima
        return seconda

    r = asyncio.run(scenario())
    assert r["ok"] is False and "in corso" in r["error"]


def test_reply_fuori_scansione_aggiorna_le_statistiche(hub):
    hub._update_stats("message", REPLY)
    assert hub.stats["picg_declared"] == "55001"
    assert len(hub.stats["nicknames"]) == 3
