"""SRTP — vettori di test ufficiali RFC 3711 e round-trip protect/unprotect.

I vettori di §B.3 verificano la KDF (e quindi AES-CM) in modo indipendente dalla
libreria crypto usata: servono a garantire che la migrazione da `cryptography` a
`pycryptodome` non abbia cambiato un solo byte del keystream.
"""
from __future__ import annotations

import base64

from custom_components.vimar_intercom.srtp import SRTPContext, _kdf

# RFC 3711 §B.3 — Key Derivation Test Vectors (index 0, key_derivation_rate 0)
MASTER_KEY = bytes.fromhex("E1F97A0D3E018BE0D64FA32C06DE4139")
MASTER_SALT = bytes.fromhex("0EC675AD498AFEEBB6960B3AABE6")

EXPECTED_CIPHER_KEY = bytes.fromhex("C61E7A93744F39EE10734AFE3FF7A087")
EXPECTED_SALT = bytes.fromhex("30CBBC08863D8C85D49DB34A9AE1")
EXPECTED_AUTH_KEY = bytes.fromhex("CEBE321F6FF7716B6FD4AB49AF256A156D38BAA4")


def test_kdf_cipher_key_matches_rfc3711():
    assert _kdf(MASTER_KEY, MASTER_SALT, 0x00, 16) == EXPECTED_CIPHER_KEY


def test_kdf_salt_matches_rfc3711():
    assert _kdf(MASTER_KEY, MASTER_SALT, 0x02, 14) == EXPECTED_SALT


def test_kdf_auth_key_matches_rfc3711():
    assert _kdf(MASTER_KEY, MASTER_SALT, 0x01, 20) == EXPECTED_AUTH_KEY


def _ctx() -> SRTPContext:
    return SRTPContext(base64.b64encode(MASTER_KEY + MASTER_SALT).decode())


def test_context_derives_the_three_session_keys():
    ctx = _ctx()
    assert ctx.cipher_key == EXPECTED_CIPHER_KEY
    assert ctx.salt == EXPECTED_SALT
    assert ctx.auth_key == EXPECTED_AUTH_KEY


def _rtp(seq: int, payload: bytes = b"payload-di-prova") -> bytes:
    """Pacchetto RTP minimo: V=2, PT=0 (PCMU), SSRC fisso, nessun CSRC."""
    return (
        bytes([0x80, 0x00])
        + seq.to_bytes(2, "big")
        + (12345 * seq).to_bytes(4, "big")   # timestamp
        + bytes.fromhex("DEADBEEF")          # SSRC
        + payload
    )


def test_protect_then_unprotect_restituisce_il_pacchetto_originale():
    tx, rx = _ctx(), _ctx()
    pkt = _rtp(1000)
    assert rx.unprotect(tx.protect(pkt)) == pkt


def test_protect_cifra_il_payload_ma_non_l_header():
    tx = _ctx()
    pkt = _rtp(1001)
    protected = tx.protect(pkt)
    assert protected[:12] == pkt[:12]            # header in chiaro
    assert protected[12:-10] != pkt[12:]         # payload cifrato
    assert len(protected) == len(pkt) + 10       # + auth tag da 80 bit


def test_unprotect_scarta_un_pacchetto_manomesso():
    tx, rx = _ctx(), _ctx()
    protected = bytearray(tx.protect(_rtp(1002)))
    protected[15] ^= 0x01                        # flip di un bit nel payload
    assert rx.unprotect(bytes(protected)) is None


def test_sequenza_di_pacchetti_in_ordine():
    tx, rx = _ctx(), _ctx()
    for seq in range(2000, 2010):
        pkt = _rtp(seq)
        assert rx.unprotect(tx.protect(pkt)) == pkt


def test_pacchetto_troppo_corto_non_solleva_eccezioni():
    assert _ctx().unprotect(b"\x80\x00\x00\x01") is None
