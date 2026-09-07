"""Test per runtime.py: risoluzione di sga_target/picg_target da options flow.

runtime.py è un modulo puro (nessun import homeassistant), popolato da
configure() con i dati salvati nel config entry. Qui si verifica solo la
logica di risoluzione SGA/PICG (fallback al default storico in const.py
quando l'utente non ha impostato nulla in options), non l'intero modulo.
"""
from __future__ import annotations

from custom_components.vimar_intercom import const
from custom_components.vimar_intercom import runtime


def _base_data(**overrides) -> dict:
    data = {
        "sip_user": "u",
        "sip_password": "p",
        "sip_domain": "example.test",
    }
    data.update(overrides)
    return data


def test_default_fallback_when_not_set():
    runtime.configure(_base_data())
    assert runtime.SGA_TARGET == const.SGA_TARGET
    assert runtime.PICG_TARGET == const.PICG_TARGET


def test_sga_target_override_only():
    runtime.configure(_base_data(sga_target="12345"))
    assert runtime.SGA_TARGET == "12345"
    # picg_target non impostato → resta il default storico
    assert runtime.PICG_TARGET == const.PICG_TARGET


def test_sga_and_picg_target_independent_override():
    runtime.configure(_base_data(sga_target="12345", picg_target="67890"))
    assert runtime.SGA_TARGET == "12345"
    assert runtime.PICG_TARGET == "67890"


def test_blank_values_fall_back_to_default():
    # Stringa vuota o solo spazi = "non impostato", non un valore valido.
    runtime.configure(_base_data(sga_target="   ", picg_target=""))
    assert runtime.SGA_TARGET == const.SGA_TARGET
    assert runtime.PICG_TARGET == const.PICG_TARGET


def test_intercom_and_door_esterno_use_resolved_sga_target():
    runtime.configure(_base_data(sga_target="99999", sip_domain="sip.example.test"))
    assert runtime.INTERCOM == "sip:99999@sip.example.test"
    assert runtime.DOOR_ESTERNO == "sip:99999@sip.example.test"


def test_reconfigure_resets_previous_override():
    """Un configure() successivo senza sga_target non deve trascinarsi dietro
    il valore della chiamata precedente: deve tornare al default."""
    runtime.configure(_base_data(sga_target="11111"))
    assert runtime.SGA_TARGET == "11111"
    runtime.configure(_base_data())
    assert runtime.SGA_TARGET == const.SGA_TARGET
