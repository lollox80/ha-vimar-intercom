"""Runtime credentials — popolate da HA config entry all'avvio.

Espone le stesse costanti di const.py per le credenziali dinamiche,
così sip_client.py può fare «from . import runtime as R» senza cambiare
la struttura del codice.

L'inizializzazione avviene in __init__.py → async_setup_entry():
    runtime.configure(entry.data)
"""

from __future__ import annotations

from . import const as _const

# ─── Valori di default (vuoti) ───────────────────────────────────────────────
SIP_USER:     str = ""
SIP_PASSWORD: str = ""
SIP_DOMAIN:   str = ""
SIP_HA1:      str = ""
SIP_PROXY:    str = "ipvdes.vimar.cloud"   # cloud proxy (da QR cproxy)
LOCAL_PROXY:  str = ""                      # IP citofono locale
GID:          str = ""
PLANT_TYPE:   str = ""
MAC_CITOFONO: str = ""

# ─── Impostazioni di rete (da config entry / options flow) ───────────────────
USE_LOCAL_UDP:  bool = True   # True = UDP locale; False = TLS/TCP cloud
LOCAL_UDP_PORT: int  = 5060   # porta UDP locale su HA

# ─── Media encryption ────────────────────────────────────────────────────────
# False (default) = RTP in chiaro (RTP/AVP, nessun a=crypto). Verificato sul
# campo 20/08/2026: la targa baresip di questo impianto NON accetta SRTP.
# True = SRTP AES_CM_128_HMAC_SHA1_80 (RTP/SAVP + a=crypto) per impianti che lo
# negoziano (media_enc). In futuro ricavabile da GET_INIT_STATUS_REPLY.
MEDIA_ENC: bool = False

# ─── Attuatori dinamici (da options flow, ricavati dalla rubrica) ────────────
# Lista di dict {"name","msg","target","icon"} prodotta da tools/parse_rubrica.py
# e incollata dall'utente nell'options flow. Default vuoto → nessun bottone.
ACTUATORS: list = []

# ─── SGA / PICG (da options flow, ricavati dalla rubrica o inseriti a mano) ──
# SGA = destinatario di VOICEMAIL;/DND; (Panda: blue) e dell'apri-porta/AUTO.
# PICG = destinatario di GET_INIT_STATUS. Sugli impianti verificati finora
# coincidono (55001), ma sono due valori distinti nella rubrica (SYSTEM.
# MAGIC_APT_INTERCOM vs PID_LIST ruolo PICG) e vanno tenuti configurabili
# separatamente. Default = const.SGA_TARGET/PICG_TARGET finché non
# sovrascritti in options (manualmente o dall'importer rubrica.db).
SGA_TARGET:  str = _const.SGA_TARGET
PICG_TARGET: str = _const.PICG_TARGET

# ─── URI calcolati da SIP_DOMAIN (popolati in configure) ─────────────────────
INTERCOM:     str = ""   # sip:<SGA_TARGET>@<domain> — targa esterna citofono
DOOR_ESTERNO: str = ""   # stesso target per comando apertura porta

# ─── Modello rilevato via SIP (vedi model_detect.py) ─────────────────────────
# Popolato all'avvio dal config entry (ultimo valore rilevato) e aggiornato a
# runtime appena il citofono si presenta con il suo User-Agent SIP.
DETECTED_MODEL:    str = ""   # es. "Elvox Tab 7S"
DETECTED_FW:       str = ""   # versione firmware, se presente nello User-Agent
DETECTED_UA:       str = ""   # User-Agent grezzo, per diagnostica
DETECTED_PRIORITY: int = 99   # indice del pattern che ha rilevato il modello


def configure(data: dict) -> None:
    """Popola il modulo con i dati del config entry.

    Chiamato da async_setup_entry() prima di avviare hub/sip_client.
    «data» è il dizionario salvato nel config entry da config_flow.
    """
    global SIP_USER, SIP_PASSWORD, SIP_DOMAIN, SIP_HA1
    global SIP_PROXY, LOCAL_PROXY
    global GID, PLANT_TYPE, MAC_CITOFONO
    global USE_LOCAL_UDP, LOCAL_UDP_PORT, MEDIA_ENC
    global INTERCOM, DOOR_ESTERNO
    global DETECTED_MODEL, DETECTED_FW, DETECTED_UA, DETECTED_PRIORITY
    global ACTUATORS
    global SGA_TARGET, PICG_TARGET

    SIP_USER     = data.get("sip_user", "")
    SIP_PASSWORD = data.get("sip_password", "")
    SIP_DOMAIN   = data.get("sip_domain", "")
    SIP_HA1      = data.get("sip_ha1", "")
    SIP_PROXY    = data.get("cloud_proxy", "ipvdes.vimar.cloud")
    LOCAL_PROXY  = data.get("local_proxy", "")
    GID          = data.get("gid", "")
    PLANT_TYPE   = data.get("plant_type", "")
    MAC_CITOFONO = data.get("mac", "")

    USE_LOCAL_UDP  = bool(data.get("use_local_udp", True))
    LOCAL_UDP_PORT = int(data.get("local_udp_port", 5060))
    MEDIA_ENC      = bool(data.get("media_enc", False))

    # Attuatori dinamici: lista già validata dall'options flow (o default vuoto).
    acts = data.get("actuators", [])
    ACTUATORS = acts if isinstance(acts, list) else []

    # SGA/PICG: valore in options (manuale o da importer rubrica.db) se presente
    # e non vuoto, altrimenti il default storico in const.py (55001).
    SGA_TARGET  = (str(data.get("sga_target") or "").strip()) or _const.SGA_TARGET
    PICG_TARGET = (str(data.get("picg_target") or "").strip()) or _const.PICG_TARGET

    # URI calcolati — devono essere aggiornati dopo SIP_DOMAIN e SGA_TARGET
    INTERCOM     = f"sip:{SGA_TARGET}@{SIP_DOMAIN}"
    DOOR_ESTERNO = f"sip:{SGA_TARGET}@{SIP_DOMAIN}"

    # Modello rilevato in una sessione precedente: riparte da lì, così le
    # entità mostrano subito il valore giusto anche prima del primo dialogo SIP.
    DETECTED_MODEL    = data.get("detected_model", "") or ""
    DETECTED_FW       = data.get("detected_fw", "") or ""
    DETECTED_UA       = data.get("detected_ua", "") or ""
    DETECTED_PRIORITY = 99 if not DETECTED_MODEL else int(data.get("detected_priority", 98))


def is_configured() -> bool:
    """True se le credenziali obbligatorie sono state caricate."""
    return bool(SIP_USER and SIP_DOMAIN and (SIP_HA1 or SIP_PASSWORD))
