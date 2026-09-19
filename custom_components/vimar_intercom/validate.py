"""Validazione degli input che arrivano da fuori (HTTP, servizi HA).

Modulo **puro**: nessun import di Home Assistant, così resta testabile senza HA
(vedi `tests/test_smoke.py`).

Raccoglie i controlli sui valori che, prima della 1.0.6, passavano senza verifica
da una query string o da un campo di servizio fino a un messaggio SIP o a una
scrittura su disco. Ogni funzione restituisce il valore ripulito, oppure `None`
se non è accettabile: chi chiama decide se rifiutare o usare un default, ma non
deve mai passare oltre il valore grezzo.
"""

from __future__ import annotations

import ipaddress
import re

# Un indirizzo SIP dell'impianto è un numero. Il valore finisce nella request
# line di un INVITE (`sip:<target>@<dominio>`): senza questo controllo un
# parametro arbitrario permetteva di far chiamare qualunque indirizzo, e un CRLF
# spezzava il messaggio SIP in due (header iniettati nella seconda metà).
_SIP_TARGET = re.compile(r"\A[0-9]{1,10}\Z")

# Nome di file: niente separatori, niente "..", niente caratteri esotici.
_FILENAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

_ALLOWED_SCHEMES = ("http", "https")


def sip_target(raw: str | None) -> str | None:
    """Un id SIP numerico, o `None` se non lo è."""
    if raw is None:
        return None
    t = str(raw).strip()
    return t if _SIP_TARGET.match(t) else None


def safe_filename(raw: str | None) -> str | None:
    """Nome di file senza percorso.

    `save_as` del servizio `fetch_local` finiva in `hass.config.path()` così
    com'era: un `../` scriveva ovunque sotto l'utente di Home Assistant, e da lì
    a eseguire codice (automations.yaml, un custom component) è un passo.
    Qui si tiene solo il nome, e solo se è un nome.
    """
    if raw is None:
        return None
    name = str(raw).replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name if _FILENAME.match(name) else None


def is_private_host(raw: str | None) -> bool:
    """True solo per un indirizzo IP privato o di loopback, scritto per esteso.

    `fetch_local` parla con l'interfaccia HTTP del citofono usando la password
    SIP in Digest. Con un host qualsiasi, chiunque potesse invocare il servizio
    poteva farsi mandare quelle credenziali da un server suo. Il citofono è in
    LAN: fuori dalla LAN il servizio non ha motivo di andare.

    Un nome DNS non è accettato di proposito: risolverlo qui significherebbe
    fidarsi di una risoluzione che può cambiare fra il controllo e la richiesta.
    """
    if not raw:
        return False
    host = str(raw).strip()
    if host.startswith("[") and "]" in host:          # [::1]:8080
        host = host[1:host.index("]")]
    elif host.count(":") == 1:                         # 192.168.1.5:8080
        host = host.split(":")[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def http_scheme(raw: str | None, default: str = "http") -> str:
    """Solo http/https: qualsiasi altro schema torna al default."""
    s = (str(raw).strip().lower() if raw else "") or default
    return s if s in _ALLOWED_SCHEMES else default
