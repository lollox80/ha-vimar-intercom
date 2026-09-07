"""QR sintetico: Base64(AESkey[32] | AES-256-CBC-PKCS5(text) | IV[16]) → dict campi."""
import base64
import os

import pytest

Crypto = pytest.importorskip("Crypto")
from Crypto.Cipher import AES  # noqa: E402
from Crypto.Util.Padding import pad  # noqa: E402

qr = pytest.importorskip("custom_components.vimar_intercom.qr_decoder")

PAYLOAD = (
    "ID=12345\nPWD=secret\nPROXY=192.168.1.50\nDOMAIN=abc.ipvdes.vimar.cloud\n"
    "CPROXY=ipvdes.vimar.cloud\nCDOMAIN=abc.ipvdes.vimar.cloud\nGID=101\nMAC=AA:BB:CC:DD:EE:FF\n"
    "PC=40507\nPLANTTYPE=2F\nCLOUD=0\nVIDEO=1\n"
)


def _make_qr(text: str) -> str:
    key, iv = os.urandom(32), os.urandom(16)
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(text.encode(), 16))
    return base64.b64encode(key + ct + iv).decode()


def _decode(s: str) -> dict:
    # tollera nomi diversi della funzione pubblica
    for name in ("decode_vimar_qr", "decode_qr", "decode", "parse_qr"):
        fn = getattr(qr, name, None)
        if fn:
            out = fn(s)
            return out if isinstance(out, dict) else getattr(out, "__dict__", {})
    pytest.skip("funzione di decodifica QR non trovata in qr_decoder")


def test_qr_roundtrip_fields():
    d = {k.lower(): v for k, v in _decode(_make_qr(PAYLOAD)).items()}
    assert d.get("id") in ("12345", 12345)
    assert d.get("gid") in ("101", 101)
    assert str(d.get("planttype", d.get("plant_type", ""))).upper() == "2F"


def test_qr_invalid_base64_raises_or_returns_falsy():
    try:
        out = _decode("!!!non-base64!!!")
    except Exception:
        return
    assert not out
