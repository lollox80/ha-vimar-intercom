"""Smoke test: i moduli puri devono importarsi con gli stub HA."""
import importlib

import pytest

PURE = ["const", "qr_decoder", "model_detect", "srtp", "rubrica_import", "runtime"]


@pytest.mark.parametrize("name", PURE)
def test_import_pure_modules(name):
    mod = importlib.import_module(f"custom_components.vimar_intercom.{name}")
    assert mod is not None


def test_const_has_no_guessed_actuators():
    from custom_components.vimar_intercom import const
    # ADR-4: nessun attuatore ipotizzato nel codice attivo
    assert const.ACTUATORS == []
    assert const.DOOR_COMMAND == "OPEN_2F"
    assert const.SEGRETERIA_HEADER_VALUE == "blue"


def test_no_credentials_in_const():
    import inspect

    from custom_components.vimar_intercom import const
    src = inspect.getsource(const)
    for bad in ("PWD=", "password=", "60992:"):
        assert bad not in src
