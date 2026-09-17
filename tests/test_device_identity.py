"""Identità dispositivo: una per installazione, mai una costante nel sorgente.

Il cloud Vimar associa registrazione e push all'identità del dispositivo
(header `Mobile-IMEI`, parametro `+sip.instance`). Fino alla 1.0.1 era una
costante in const.py uguale per tutte le installazioni: due impianti si
contendevano la stessa registrazione. Questi test bloccano la regressione.
"""

import re
import uuid

import pytest

from custom_components.vimar_intercom import const, runtime

BASE = {
    "sip_user": "12345",
    "sip_password": "segreto",
    "sip_domain": "example.invalid",
}


def test_identity_has_expected_shape():
    ident = runtime.new_device_identity()
    assert re.fullmatch(r"\d{15}", ident["device_imei"])
    assert uuid.UUID(ident["device_uuid"]).version == 4


def test_identity_is_unique_per_call():
    a = runtime.new_device_identity()
    b = runtime.new_device_identity()
    assert a["device_imei"] != b["device_imei"]
    assert a["device_uuid"] != b["device_uuid"]


def test_configure_uses_stored_identity():
    stored = runtime.new_device_identity()
    runtime.configure({**BASE, **stored})
    assert runtime.DEVICE_IMEI == stored["device_imei"]
    assert runtime.DEVICE_UUID == stored["device_uuid"]


def test_configure_without_identity_falls_back_to_a_fresh_one():
    """Entry pre-1.0.2 (o probe senza entry): mai vuoto, mai condiviso."""
    runtime.configure(dict(BASE))
    first = (runtime.DEVICE_IMEI, runtime.DEVICE_UUID)
    runtime.configure(dict(BASE))
    second = (runtime.DEVICE_IMEI, runtime.DEVICE_UUID)

    assert all(first) and all(second)
    assert first != second


@pytest.mark.parametrize("name", ["DEVICE_IMEI", "DEVICE_UUID"])
def test_const_exposes_no_hardcoded_identity(name):
    assert not hasattr(const, name), (
        f"const.{name} è tornata: l'identità deve stare nel config entry"
    )
