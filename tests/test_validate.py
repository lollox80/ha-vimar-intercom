"""Validazione degli input che arrivano da fuori (v1.0.6).

Ogni caso qui sotto corrisponde a una strada che, fino alla 1.0.5, portava un
valore non controllato da una query string o da un campo di servizio fino a un
messaggio SIP o a una scrittura su disco.
"""
from __future__ import annotations

import pytest

v = pytest.importorskip("custom_components.vimar_intercom.validate")


# ─── sip_target: finisce nella request line di un INVITE ─────────────────────

@pytest.mark.parametrize("buono", ["55001", "55100", "1", "1234567890"])
def test_un_id_numerico_passa(buono):
    assert v.sip_target(buono) == buono


def test_gli_spazi_intorno_non_contano():
    assert v.sip_target("  55001  ") == "55001"


@pytest.mark.parametrize("cattivo", [
    "55001\r\nSubject: iniettato",   # CRLF injection nel messaggio SIP
    "sip:evil@example.com",
    "55001@altro.dominio",
    "../../x",
    "5500a",
    "",
    "   ",
    "12345678901",                    # troppo lungo
    "٥٥٠٠١",                          # cifre non ASCII: isdigit() le accetterebbe
])
def test_tutto_il_resto_viene_rifiutato(cattivo):
    assert v.sip_target(cattivo) is None


def test_none_resta_none():
    assert v.sip_target(None) is None


def test_uno_spazio_bianco_in_coda_viene_tolto_non_rifiutato():
    """`strip()` toglie anche un newline finale: `"55001\n"` diventa `"55001"`.

    È sicuro — quello che esce non contiene separatori — ma va detto, perché a
    prima vista sembra che un CR/LF passi il controllo. A passare è la stringa
    ripulita, non quella originale.
    """
    assert v.sip_target("55001\n") == "55001"
    assert v.sip_target("55001\r\n") == "55001"


@pytest.mark.parametrize("qualsiasi", [
    "55001", "55001\n", "  55001  ", "55001\r\nX: y", "sip:a@b", "", "abc", None,
])
def test_cio_che_esce_non_puo_mai_spezzare_una_request_line(qualsiasi):
    """L'invariante che conta davvero, comunque sia scritto l'input."""
    out = v.sip_target(qualsiasi)
    assert out is None or ("\r" not in out and "\n" not in out)


# ─── safe_filename: finiva in hass.config.path() così com'era ────────────────

def test_un_nome_semplice_passa():
    assert v.safe_filename("rubrica.db") == "rubrica.db"


@pytest.mark.parametrize("percorso, atteso", [
    ("../../configuration.yaml", "configuration.yaml"),
    ("/etc/passwd", "passwd"),
    ("C:\\Windows\\system.ini", "system.ini"),
    ("sub/dir/file.db", "file.db"),
])
def test_il_percorso_viene_buttato_via(percorso, atteso):
    """Resta il nome; la cartella la decide il chiamante, non l'input."""
    assert v.safe_filename(percorso) == atteso


@pytest.mark.parametrize("cattivo", ["", "..", ".", "a" * 65, "file;rm -rf", ".nascosto"])
def test_nomi_non_accettabili(cattivo):
    assert v.safe_filename(cattivo) is None


# ─── is_private_host: la richiesta porta la password SIP in Digest ───────────

@pytest.mark.parametrize("host", [
    "192.168.1.50", "10.0.0.1", "172.16.0.1", "127.0.0.1",
    "192.168.1.50:8080", "[::1]", "169.254.1.1",
])
def test_indirizzi_di_lan_accettati(host):
    assert v.is_private_host(host) is True


@pytest.mark.parametrize("host", [
    "8.8.8.8", "1.1.1.1:80", "", None,
])
def test_indirizzi_pubblici_rifiutati(host):
    assert v.is_private_host(host) is False


@pytest.mark.parametrize("host", ["example.com", "localhost", "intercom.local"])
def test_i_nomi_dns_non_bastano(host):
    """Risolverli qui vorrebbe dire fidarsi di una risoluzione che può cambiare
    fra il controllo e la richiesta vera."""
    assert v.is_private_host(host) is False


# ─── http_scheme ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dato, atteso", [
    ("http", "http"), ("https", "https"), ("HTTPS", "https"),
    ("file", "http"), ("gopher", "http"), ("", "http"), (None, "http"),
])
def test_solo_http_e_https(dato, atteso):
    assert v.http_scheme(dato) == atteso
