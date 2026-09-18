"""Parsing del record mDNS `_eipvdes._tcp` (discovery del citofono).

I due record di riferimento sono quelli realmente osservati:
- Tab 7S 2F+ (40507): quattro chiavi, `domain` = dominio cloud dell'impianto
- Tab 5S UP (40515) fw 2.1.0203: undici chiavi, `domain` = indirizzo LAN del Tab

Il parser deve reggere entrambi senza pretendere le chiavi che mancano.
"""

from __future__ import annotations

from custom_components.vimar_intercom import discovery

HOST = "192.0.2.10"

# Record "corto" (firmware più vecchio): valori in bytes, come li consegna
# python-zeroconf.
TXT_SHORT = {
    b"mac": b"AA:BB:CC:DD:EE:FF",
    b"domain": b"aabbccddeeff.aabbccddeeff1234567890.ipvdes.vimar.cloud",
    b"proxy": b"192.0.2.10",
    b"timestemp": b"1789696090",
}

# Record "lungo" (fw 2.1.0203): qui `domain` è l'indirizzo del Tab.
TXT_LONG = {
    b"dev": b"40515",
    b"mac": b"AA:BB:CC:DD:EE:FF",
    b"rec": b"0",
    b"sta": b"2",
    b"man": b"0",
    b"fver": b"2.1.0203",
    b"rver": b"7.1",
    b"sver": b"10.305",
    b"hws": b"2",
    b"proxy": b"192.0.2.20",
    b"domain": b"192.0.2.20",
}


# ─── normalize_mac ───────────────────────────────────────────────────────────

def test_mac_normalization_ignores_separators_and_case():
    assert discovery.normalize_mac("aa:bb:cc:dd:ee:ff") == "AABBCCDDEEFF"
    assert discovery.normalize_mac("AA-BB-CC-DD-EE-FF") == "AABBCCDDEEFF"
    assert discovery.normalize_mac("AABBCCDDEEFF") == "AABBCCDDEEFF"


def test_mac_normalization_survives_missing_value():
    assert discovery.normalize_mac(None) == ""
    assert discovery.normalize_mac("") == ""


# ─── parse_service_txt ───────────────────────────────────────────────────────

def test_txt_is_decoded_from_bytes():
    txt = discovery.parse_service_txt(TXT_SHORT)
    assert txt["mac"] == "AA:BB:CC:DD:EE:FF"
    assert txt["proxy"] == "192.0.2.10"


def test_txt_accepts_plain_strings_too():
    txt = discovery.parse_service_txt({"mac": "AA:BB:CC:DD:EE:FF"})
    assert txt["mac"] == "AA:BB:CC:DD:EE:FF"


def test_txt_keys_are_lowercased():
    assert discovery.parse_service_txt({b"MAC": b"x"})["mac"] == "x"


def test_empty_txt_is_not_an_error():
    assert discovery.parse_service_txt(None) == {}
    assert discovery.parse_service_txt({}) == {}


# ─── extract_discovery: record corto ─────────────────────────────────────────

def test_short_record_yields_proxy_and_cloud_domain():
    d = discovery.extract_discovery(HOST, TXT_SHORT)
    assert d["local_proxy"] == "192.0.2.10"
    assert d["sip_domain"].endswith(".ipvdes.vimar.cloud")
    assert d["mac_normalized"] == "AABBCCDDEEFF"


def test_short_record_has_no_model_or_firmware():
    """`dev` e `fver` non esistono sui firmware più vecchi: campi vuoti, non errori."""
    d = discovery.extract_discovery(HOST, TXT_SHORT)
    assert d["model"] == ""
    assert d["firmware"] == ""


# ─── extract_discovery: record lungo ─────────────────────────────────────────

def test_long_record_yields_model_and_firmware():
    d = discovery.extract_discovery(HOST, TXT_LONG)
    assert d["model"] == "40515"
    assert d["firmware"] == "2.1.0203"


def test_long_record_domain_is_the_tab_address():
    """Su questo impianto il Tab dichiara sé stesso come dominio SIP: è il
    valore che si aspetta nelle richieste, e non è quello del QR."""
    d = discovery.extract_discovery(HOST, TXT_LONG)
    assert d["sip_domain"] == "192.0.2.20"
    assert d["local_proxy"] == "192.0.2.20"


def test_undocumented_fields_are_ignored():
    """`rec`, `sta`, `man` non sono documentati: non devono finire nel risultato."""
    d = discovery.extract_discovery(HOST, TXT_LONG)
    assert set(d) == {
        "mac", "mac_normalized", "local_proxy", "sip_domain", "model", "firmware",
    }


# ─── casi degeneri ───────────────────────────────────────────────────────────

def test_loopback_domain_is_dropped():
    """Un `domain` di loopback non è utilizzabile: meglio vuoto che sbagliato."""
    txt = dict(TXT_LONG)
    txt[b"domain"] = b"127.0.0.1"
    assert discovery.extract_discovery(HOST, txt)["sip_domain"] == ""


def test_missing_proxy_falls_back_to_the_announcing_host():
    txt = {k: v for k, v in TXT_SHORT.items() if k != b"proxy"}
    assert discovery.extract_discovery(HOST, txt)["local_proxy"] == HOST


def test_loopback_proxy_falls_back_to_the_announcing_host():
    txt = dict(TXT_SHORT)
    txt[b"proxy"] = b"127.0.0.1"
    assert discovery.extract_discovery(HOST, txt)["local_proxy"] == HOST


def test_record_without_any_txt_still_gives_the_host():
    d = discovery.extract_discovery(HOST, None)
    assert d["local_proxy"] == HOST
    assert d["mac"] == "" and d["sip_domain"] == ""
