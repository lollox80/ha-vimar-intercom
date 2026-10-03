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


def _lost(tx: SRTPContext, rx: SRTPContext, seqs) -> list[int]:
    """The packets of ``seqs`` (indices, the sequence number is the low 16 bits) rx refused."""
    return [seq for seq in seqs if rx.unprotect(tx.protect(_rtp(seq & 0xFFFF))) is None]


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


def _rtp_ssrc(seq: int, ssrc: int) -> bytes:
    return bytes([0x80, 0x00]) + seq.to_bytes(2, "big") + bytes(4) + ssrc.to_bytes(4, "big") + b"x" * 20


def test_rollover_della_sequenza():
    tx, rx = _ctx(), _ctx()
    for seq in list(range(65530, 65536)) + list(range(0, 6)):
        pkt = _rtp(seq)
        assert rx.unprotect(tx.protect(pkt)) == pkt


def test_nuovo_ssrc_con_sequenza_lontana_si_autentica():
    """Un flusso che riparte (nuovo SSRC, sequenza altrove) ha ROC 0 dal mittente:
    con lo stato unico il ricevitore stimava ROC 1 e scartava tutto il flusso."""
    tx_a, tx_b, rx = _ctx(), _ctx(), _ctx()
    for seq in range(40000, 40010):
        assert rx.unprotect(tx_a.protect(_rtp_ssrc(seq, 1))) is not None
    for seq in range(5, 15):
        pkt = _rtp_ssrc(seq, 2)
        assert rx.unprotect(tx_b.protect(pkt)) == pkt


def test_a_captured_packet_sent_again_is_dropped():
    """RFC 3711 §3.3.2: the same index twice, or one older than the window, is
    refused, so a captured voice or video packet cannot be replayed. Old ones
    sent again and again between live packets too: none of them reaches the
    media, which is forwarded before any RTP reordering."""
    tx, rx = _ctx(), _ctx()
    sent = {seq: tx.protect(_rtp(seq)) for seq in range(1, 200)}
    for seq in range(1, 200):
        assert rx.unprotect(sent[seq]) is not None
    assert rx.unprotect(sent[150]) is None
    for seq in range(200, 210):
        assert rx.unprotect(sent[1]) is None and rx.unprotect(sent[2]) is None
        assert rx.unprotect(tx.protect(_rtp(seq))) is not None, seq
    assert rx.resyncs == 0


def test_a_stray_packet_far_ahead_does_not_stall_the_stream():
    """Cloud ring on a 40515 (2 Oct): one authentic packet numbered far ahead on
    the same SSRC moved the window there, and the live stream after it was
    refused as too old: no preview, no photo, no voice. The stray goes through
    once, sent again it is a replay, and it moves neither the window nor the
    rollover estimate (41000 is more than half a wrap from the live numbers)."""
    tx, far, rx = _ctx(), _ctx(), _ctx()  # far: the stray's own sender state
    assert _lost(tx, rx, range(1000, 1100)) == []
    stray = far.protect(_rtp(41000))
    assert rx.unprotect(stray) is not None
    assert _lost(tx, rx, range(1100, 1200)) == []
    assert rx.unprotect(stray) is None
    assert _lost(tx, rx, range(1200, 1300)) == [] and rx.resyncs == 0
    assert _lost(tx, rx, range(1500, 1600)) == [] and rx.resyncs == 1  # a real jump (a loss burst)


def test_strays_in_a_row_half_a_wrap_ahead_do_not_move_the_window():
    """Moving the window there would move the rollover estimate too, and the
    live stream would fail authentication from then on."""
    tx, far, rx = _ctx(), _ctx(), _ctx()
    for seq in range(1000, 1010):
        assert rx.unprotect(tx.protect(_rtp(seq))) is not None
    for seq in range(41000, 41003):
        assert rx.unprotect(far.protect(_rtp(seq))) is not None
    for seq in range(1010, 1100):
        assert rx.unprotect(tx.protect(_rtp(seq))) is not None, seq
    assert rx.resyncs == 0


def test_a_jump_past_half_a_wrap_at_roc_0_moves_the_window_after_a_longer_run():
    """A real jump of 40000 (or a loss burst that long) at ROC 0: the packets go
    through as strays until the window follows, then the sender wraps."""
    tx, rx = _ctx(), _ctx()
    assert _lost(tx, rx, [*range(1000, 1100), *range(41100, 41100 + 30000)]) == []
    assert rx.resyncs == 1


def test_a_jump_at_roc_0_that_wraps_before_the_window_follows_loses_nothing():
    """The wrap comes 36 packets after the jump, before the run moves the window:
    the packets after it authenticate at the ROC of the run, not of the window."""
    tx, rx = _ctx(), _ctx()
    assert _lost(tx, rx, [*range(1000, 1100), *range(65500, 67000)]) == []
    assert rx.resyncs == 1


def test_a_stray_after_the_wrap_does_not_cut_off_an_unconfirmed_jump():
    """The old leg sends one more packet just after the jump wrapped, before
    its run moved the window: the stream after it goes on."""
    tx, old, rx = _ctx(), _ctx(), _ctx()
    assert _lost(tx, rx, [*range(1000, 1100), *range(65500, 65546)]) == []  # a run of 46
    assert rx.unprotect(old.protect(_rtp(1300))) is not None
    assert _lost(tx, rx, range(65546, 66500)) == []


def test_strays_in_a_row_do_not_move_the_window_past_a_reordered_stream():
    """Three strays 20000 ahead, then a keyframe burst arriving in reverse
    order, as the 40515 relay reorders video: the window stays on the stream."""
    tx, far, rx = _ctx(), _ctx(), _ctx()
    assert _lost(tx, rx, range(1000, 1300)) == []
    assert _lost(far, rx, range(21300, 21303)) == []
    assert _lost(tx, rx, [*range(1315, 1299, -1), *range(1316, 1400)]) == []
    assert rx.resyncs == 0


def test_a_sequence_restarted_lower_and_reordered_resyncs_after_three():
    tx, rx = _ctx(), _ctx()
    assert _lost(tx, rx, range(1000, 1300)) == []
    swapped = [seq ^ 1 for seq in range(4, 300)]  # 5, 4, 7, 6, ...
    assert _lost(tx, rx, swapped) == [5, 4, 7, 6] and rx.resyncs == 1


def test_a_stale_far_packet_does_not_hold_back_a_later_restart():
    """A stray left behind early in the call is no run: a restart just under it
    resyncs after three as usual."""
    tx, rx = _ctx(), _ctx()
    assert _lost(tx, rx, range(30000, 30300)) == []
    assert _lost(tx, rx, [10050]) == [10050]
    assert _lost(tx, rx, range(30300, 30400)) == []
    assert _lost(tx, rx, range(10000, 11000)) == [10000, 10001] and rx.resyncs == 1


def test_old_packets_from_before_the_wrap_sent_again_are_refused():
    """With a stray early in the call pending, three packets captured before
    the first wrap, sent again, must not authenticate at the next ROC try and
    move the window back, where the live stream would fail from then on."""
    tx, far, rx = _ctx(), _ctx(), _ctx()
    assert _lost(tx, rx, range(1000, 1100)) == []
    assert rx.unprotect(far.protect(_rtp(4001))) is not None
    caught = {}
    for idx in range(1100, 70000):
        pkt = tx.protect(_rtp(idx & 0xFFFF))
        if idx in (4100, 4150, 4200):
            caught[idx] = pkt
        assert rx.unprotect(pkt) is not None, idx
    assert [rx.unprotect(pkt) for pkt in caught.values()] == [None] * 3
    assert _lost(tx, rx, range(70000, 70100)) == [] and rx.resyncs == 0


def test_a_stray_and_a_jump_after_the_first_wrap_lose_nothing():
    tx, far, rx = _ctx(), _ctx(), _ctx()
    for seq in (30000, 60000, 0, 10000):  # the stray's sender, at ROC 1
        far.protect(_rtp(seq))
    assert _lost(tx, rx, range(65000, 66600)) == []  # across the wrap
    stray = far.protect(_rtp(21000))
    assert rx.unprotect(stray) is not None and rx.unprotect(stray) is None
    assert _lost(tx, rx, range(66600, 66700)) == [] and rx.resyncs == 0
    assert _lost(tx, rx, range(86700, 86800)) == [] and rx.resyncs == 1


def test_a_stray_as_the_first_packet_of_the_call_costs_two_packets():
    tx, far, rx = _ctx(), _ctx(), _ctx()
    assert rx.unprotect(far.protect(_rtp(21000))) is not None
    refused = _lost(tx, rx, range(1000, 1100))
    assert refused == [1000, 1001] and rx.resyncs == 1


def test_a_sequence_restarted_lower_resyncs_after_three_in_a_row():
    """A panel or relay restarting its numbers lower on the same SSRC: the window
    moves there on the third packet in a row, the first two are lost."""
    tx, rx = _ctx(), _ctx()
    for seq in range(1000, 1300):
        assert rx.unprotect(tx.protect(_rtp(seq))) is not None
    refused = _lost(tx, rx, range(5, 300))
    assert refused == [5, 6] and rx.resyncs == 1
    dup = tx.protect(_rtp(300))
    assert rx.unprotect(dup) is not None and rx.unprotect(dup) is None, "still refuses a replay"


def test_late_packets_inside_the_window_are_still_accepted():
    tx, rx = _ctx(), _ctx()
    early, late = tx.protect(_rtp(10)), tx.protect(_rtp(11))
    assert rx.unprotect(late) is not None
    assert rx.unprotect(early) is not None
    # The reorder buffer holds up to 64 packets: one that late must still pass.
    late = tx.protect(_rtp(200))
    for seq in range(201, 301):
        assert rx.unprotect(tx.protect(_rtp(seq))) is not None
    assert rx.unprotect(late) is not None, "100 places late, inside the window"


def test_the_replay_window_follows_the_sequence_wrap_and_each_ssrc():
    tx, rx = _ctx(), _ctx()
    sent = [tx.protect(_rtp(s)) for s in (65534, 65535, 0, 1)]
    assert all(rx.unprotect(p) is not None for p in sent)
    assert all(rx.unprotect(p) is None for p in sent) and rx.replayed == 4
    tx2, rx2 = _ctx(), _ctx()
    pkts = {s: tx2.protect(_rtp(s)) for s in (65533, 65534, 65535, 0, 1)}
    for s in (65533, 65535, 0, 1, 65534):  # 65534 three places late, ROC 0 vs 1
        assert rx2.unprotect(pkts[s]) is not None, s
    assert rx2.unprotect(pkts[65534]) is None
    other = _ctx()
    assert rx.unprotect(other.protect(_rtp_ssrc(1, 2))) is not None
