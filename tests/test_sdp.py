"""Test di build_sdp/parse_sdp: RTP in chiaro (default) vs SRTP (media_enc=True).

Verifica sul campo (20/08/2026): la targa baresip di questo impianto NON accetta
SRTP, quindi build_sdp deve offrire RTP/AVP senza a=crypto quando MEDIA_ENC=False.
"""
import pytest

sip = pytest.importorskip("custom_components.vimar_intercom.sip_client")
from custom_components.vimar_intercom import runtime as R


@pytest.fixture(autouse=True)
def _restore_media_enc():
    prev = getattr(R, "MEDIA_ENC", False)
    yield
    R.MEDIA_ENC = prev


def test_build_sdp_plain_rtp_default():
    """MEDIA_ENC=False → RTP/AVP, nessuna riga a=crypto, chiavi locali None."""
    R.MEDIA_ENC = False
    sdp = sip.build_sdp()
    assert "m=audio" in sdp and "RTP/AVP" in sdp
    assert "m=video" in sdp
    assert "RTP/SAVP" not in sdp
    assert "a=crypto" not in sdp
    # audio offre PCMU/PCMA/telephone-event
    assert "m=audio" in sdp
    assert "RTP/AVP 0 8 101" in sdp
    assert "RTP/AVP 96" in sdp  # video H.264
    # nessuna chiave SRTP generata → setup_media non creerà i contesti
    assert sip._local_crypto_key is None
    assert sip._local_video_crypto_key is None


def test_build_sdp_srtp_when_enabled():
    """MEDIA_ENC=True → RTP/SAVP con a=crypto e chiavi base64 generate."""
    R.MEDIA_ENC = True
    sdp = sip.build_sdp()
    assert "RTP/SAVP 0 8 101" in sdp
    assert "RTP/SAVP 96" in sdp
    assert "RTP/AVP" not in sdp
    assert sdp.count("a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:") == 2
    assert sip._local_crypto_key is not None
    assert sip._local_video_crypto_key is not None


def test_parse_sdp_plain_answer_no_crypto():
    """SDP di risposta della targa (RTP/AVP, senza crypto) → nessuna crypto_key."""
    answer = (
        "v=0\r\n"
        "o=- 0 0 IN IP4 192.168.0.60\r\n"
        "s=baresip\r\n"
        "c=IN IP4 192.168.0.60\r\n"
        "t=0 0\r\n"
        "m=audio 53304 RTP/AVP 0 8 101\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=rtpmap:8 PCMA/8000\r\n"
        "a=rtpmap:101 telephone-event/8000\r\n"
        "a=ptime:20\r\n"
        "m=video 9300 RTP/AVP 96\r\n"
        "a=rtpmap:96 H264/90000\r\n"
    )
    r = sip.parse_sdp(answer)
    assert r["audio"]["port"] == 53304
    assert r["audio"]["ip"] == "192.168.0.60"
    assert "crypto_key" not in r["audio"]
    assert r["video"]["port"] == 9300
    assert "crypto_key" not in r["video"]


def test_parse_sdp_srtp_answer_extracts_key():
    answer = (
        "v=0\r\n"
        "c=IN IP4 192.168.0.60\r\n"
        "m=audio 53304 RTP/SAVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:AbCdEf0123456789AbCdEf0123456789AbCdEf01\r\n"
    )
    r = sip.parse_sdp(answer)
    assert r["audio"]["crypto_key"] == "AbCdEf0123456789AbCdEf0123456789AbCdEf01"
