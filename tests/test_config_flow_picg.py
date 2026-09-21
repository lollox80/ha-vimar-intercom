"""La conferma dell'import rubrica: il PICG che promette è quello che salva.

Regressione dalla PR #16. Con la rubrica caricata **da file** il riepilogo diceva
«resta quello già configurato», ma il salvataggio sovrascriveva `picg_target`
con l'SGA letto dalla rubrica: un `60001` messo a mano per un impianto 2FV2
diventava `55001`. Il file non dice nulla sul PICG, quindi non deve toccarlo.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest


def _stub(name: str, **attrs) -> None:
    module = sys.modules.get(name) or types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


@pytest.fixture(scope="module")
def cf():
    # Moduli di HA che config_flow importa e che conftest.py non fornisce.
    _stub("homeassistant.components.file_upload", process_uploaded_file=None)
    _stub("homeassistant.data_entry_flow", FlowResult=dict)
    _stub("homeassistant.helpers.selector")

    # `class VimarIntercomConfigFlow(ConfigFlow, domain=DOMAIN)`: la base deve
    # accettare l'argomento di classe, cosa che lo stub generico non fa.
    class _ConfigFlow:
        def __init_subclass__(cls, **kwargs):
            super().__init_subclass__()

    ce = sys.modules["homeassistant.config_entries"]
    if getattr(sys.modules["homeassistant"], "_is_stub", False):
        ce.ConfigFlow = _ConfigFlow
    return pytest.importorskip("custom_components.vimar_intercom.config_flow")


class _Entry:
    def __init__(self, options: dict):
        self.data = {"sip_user": "12345", "sip_password": "x", "local_proxy": "192.0.2.1", "gid": "101"}
        self.options = options


def _flow(cf, options: dict, picg_from_rest: str | None = None):
    flow = cf.OptionsFlowHandler(_Entry(options))
    flow._imported = {
        "actuators": [{"name": "Serratura", "msg": "OPEN", "target": "55001", "icon": "door"}],
        "sga": "55001",
        "system": {},
    }
    flow._imported_gid = "101"
    flow._picg_from_rest = picg_from_rest
    # Le due chiamate di HA che il passo usa: restituiscono ciò che ricevono.
    flow.async_show_form = lambda **kw: {"type": "form", **kw}
    flow.async_create_entry = lambda **kw: {"type": "create_entry", **kw}
    return flow


def _confirm(cf, options, picg_from_rest=None):
    form = asyncio.run(_flow(cf, options, picg_from_rest).async_step_import_confirm(None))
    saved = asyncio.run(_flow(cf, options, picg_from_rest).async_step_import_confirm({}))
    return form["description_placeholders"]["picg_info"], saved["data"]["picg_target"]


def test_da_file_il_picg_configurato_a_mano_resta(cf):
    info, saved = _confirm(cf, {"sga_target": "55001", "picg_target": "60001"})
    assert saved == "60001"
    assert "resta" in info and "60001" in info


def test_da_file_senza_picg_configurato_prende_l_sga(cf):
    """Comportamento storico dell'importer, che resta valido quando non c'è altro."""
    info, saved = _confirm(cf, {"sga_target": "55001"})
    assert saved == "55001"
    assert "uguale all'SGA" in info


def test_dal_citofono_vince_il_picg_dichiarato(cf):
    info, saved = _confirm(cf, {"sga_target": "55001", "picg_target": "60001"}, picg_from_rest="55009")
    assert saved == "55009"
    assert "dichiarato dal citofono" in info


def test_il_riepilogo_e_il_salvataggio_non_divergono_mai(cf):
    """Qualunque combinazione: il valore nel riepilogo è quello salvato."""
    for options in ({}, {"picg_target": "60001"}, {"sga_target": "55001"}):
        for rest in (None, "55001", "55009"):
            info, saved = _confirm(cf, options, rest)
            assert saved in info, (options, rest, info, saved)
