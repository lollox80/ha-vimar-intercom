"""Nessun valore specifico di un impianto può tornare costante nel sorgente.

L'integrazione è stata sviluppata su un solo impianto: ogni valore che vale solo
lì (indirizzo SGA, dominio/proxy cloud del QR, identità dispositivo) deve stare
nel config entry. Questi test sono guardie di regressione: falliscono se uno di
quei valori rientra nel codice come costante.
"""

from pathlib import Path

import pytest

from custom_components.vimar_intercom import const

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "vimar_intercom"


@pytest.mark.parametrize("name", ["SIP_SNI", "SIP_ROUTE"])
def test_const_exposes_no_cloud_host(name):
    """SNI e Route valgono <cproxy>, che arriva dal QR (runtime.SIP_PROXY)."""
    assert not hasattr(const, name), (
        f"const.{name} è tornata: su un impianto con cproxy diverso manderebbe "
        f"in handshake TLS il nome sbagliato"
    )


@pytest.mark.parametrize("token", ["C.SIP_SNI", "C.SIP_ROUTE"])
def test_sip_client_takes_the_cloud_host_from_the_entry(token):
    src = (COMPONENT / "sip_client.py").read_text(encoding="utf-8")
    assert token not in src, f"sip_client.py usa ancora {token} invece di R.SIP_PROXY"


def test_door_stats_do_not_hardcode_the_sga():
    """`SGA_TARGET` è configurabile dalla 1.0.0: anche le stats devono usarlo."""
    src = (COMPONENT / "hub.py").read_text(encoding="utf-8")
    assert 'target or "55001"' not in src, (
        "hub.py registra un SGA fisso nelle statistiche: usa R.SGA_TARGET"
    )
