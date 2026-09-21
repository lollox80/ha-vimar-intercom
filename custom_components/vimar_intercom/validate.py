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


# Ricerca del PICG: quanti indirizzi al massimo in una chiamata. Ogni sonda è un
# MESSAGE vero verso l'impianto, con una pausa tra l'uno e l'altro: un intervallo
# ampio non è una scansione, è traffico. Cinquanta bastano per i vicini di un
# indirizzo noto, che è il caso d'uso.
MAX_SCAN_TARGETS = 50


def scan_targets(
    start: str | None = None,
    end: str | None = None,
    targets: str | None = None,
) -> list[str]:
    """Gli indirizzi da interrogare, o `ValueError` con il motivo.

    `targets` (lista separata da virgole o spazi) ha la precedenza; altrimenti
    l'intervallo `start`–`end` compreso. Ogni voce deve passare `sip_target`.
    Gli zeri iniziali di `start` vengono conservati. Niente duplicati.
    """
    out: list[str] = []
    if targets and str(targets).strip():
        for item in re.split(r"[\s,;]+", str(targets).strip()):
            if not item:
                continue
            t = sip_target(item)
            if t is None:
                raise ValueError(f"indirizzo non valido: {item!r}")
            if t not in out:
                out.append(t)
    else:
        a, b = sip_target(start), sip_target(end if end not in (None, "") else start)
        if a is None or b is None:
            raise ValueError("start/end devono essere indirizzi SIP numerici")
        lo, hi = int(a), int(b)
        if hi < lo:
            raise ValueError("end è minore di start")
        if hi - lo + 1 > MAX_SCAN_TARGETS:
            raise ValueError(f"al massimo {MAX_SCAN_TARGETS} indirizzi per ricerca")
        width = len(a)
        out = [str(n).zfill(width) for n in range(lo, hi + 1)]
    if not out:
        raise ValueError("nessun indirizzo da interrogare")
    if len(out) > MAX_SCAN_TARGETS:
        raise ValueError(f"al massimo {MAX_SCAN_TARGETS} indirizzi per ricerca")
    return out
