"""Vimar QR code decoder — AES-256-CBC, chiave self-contained nel payload Base64."""

from __future__ import annotations

import base64
import hashlib
import logging

_LOGGER = logging.getLogger(__name__)

# ─── Costanti chiavi payload (da QRComputationResult.java) ──────────────────
QR_ID       = "id"         # SIP user-id (es. "12345")
QR_PWD      = "pwd"        # SIP password
QR_DOMAIN   = "domain"     # SIP domain locale del citofono
QR_CDOMAIN  = "cdomain"    # Cloud domain (uguale a domain per Tab5S IP)
QR_CPROXY   = "cproxy"     # Cloud proxy (es. "ipvdes.vimar.cloud")
QR_PROXY    = "proxy"      # Proxy locale (se presente)
QR_GID      = "gid"        # Group ID impianto
QR_MAC      = "mac"        # MAC citofono
QR_PLANTTYPE = "planttype" # "2F", "2FV2" o "IP"
QR_VIDEO    = "video"      # Anteprima video abilitata ("0"/"1")
QR_CLOUD    = "cloud"      # Tipo connessione ("0"/"1"/"2")
QR_PC       = "pc"         # Product code


class QRDecodeError(ValueError):
    """Errore di decodifica QR Vimar."""


def decode(qr_text: str) -> dict[str, str]:
    """Decodifica un QR Vimar e restituisce un dizionario con i campi.

    Formato QR Vimar (da SipComputationStrategy.java / QrUtil.java):
        Base64( AES_KEY[32] | AES_CBC_CIPHERTEXT | IV[16] )

    Il payload decrittato è testo UTF-8, righe «key=value» separate da «\\n»
    (da StringUtil.getPairs() nel APK originale).

    Raises:
        QRDecodeError: se il testo non è un QR Vimar valido o la decodifica fallisce.
    """
    qr_text = qr_text.strip()
    if not qr_text:
        raise QRDecodeError("Testo QR vuoto")

    # 1. Base64 decode
    try:
        data = base64.b64decode(qr_text)
    except Exception as exc:
        raise QRDecodeError(f"Base64 non valido: {exc}") from exc

    if len(data) < 32 + 16 + 1:
        raise QRDecodeError(
            f"Payload troppo corto ({len(data)} byte), minimo 49 byte richiesti"
        )

    # 2. Estrai key (32 byte) | ciphertext | IV (ultimi 16 byte)
    key        = data[:32]
    ciphertext = data[32 : len(data) - 16]
    iv         = data[len(data) - 16 :]

    if not ciphertext:
        raise QRDecodeError("Ciphertext vuoto dopo estrazione key/IV")

    # 3. AES-256-CBC decrypt
    try:
        plaintext = _aes_decrypt(key, ciphertext, iv)
    except Exception as exc:
        raise QRDecodeError(f"Decriptazione AES fallita: {exc}") from exc

    _LOGGER.debug("QR payload decrittato: %s", plaintext[:200])

    # 4. Parse righe «key=value»
    fields: dict[str, str] = {}
    for line in plaintext.splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip().lower()] = v.strip()

    if not fields:
        raise QRDecodeError("Nessun campo trovato nel payload decrittato")

    # Verifica minima: deve contenere almeno id e domain (SIP QR)
    if QR_ID not in fields or (QR_DOMAIN not in fields and QR_CDOMAIN not in fields):
        raise QRDecodeError(
            "Il QR non contiene i campi SIP obbligatori (id, domain). "
            "Assicurati di usare il QR di abbinamento SIP dal pannello del citofono."
        )

    _LOGGER.info(
        "QR decodificato: id=%s domain=%s planttype=%s mac=%s",
        fields.get(QR_ID), fields.get(QR_DOMAIN) or fields.get(QR_CDOMAIN),
        fields.get(QR_PLANTTYPE), fields.get(QR_MAC),
    )
    return fields


def extract_sip_credentials(fields: dict[str, str]) -> dict[str, str]:
    """Estrae le credenziali SIP utili dal dizionario di campi QR decodificato.

    Returns un dict con le chiavi:
        sip_user, sip_password, sip_domain, sip_ha1,
        cloud_proxy, gid, plant_type, mac
    """
    sip_user   = fields.get(QR_ID, "")
    sip_pass   = fields.get(QR_PWD, "")
    # domain può essere in "domain" o "cdomain"
    sip_domain = fields.get(QR_DOMAIN) or fields.get(QR_CDOMAIN, "")
    cloud_proxy = fields.get(QR_CPROXY) or fields.get(QR_PROXY, "ipvdes.vimar.cloud")

    # Pre-calcola HA1 per evitare di tenere la password in memoria durante auth
    sip_ha1 = hashlib.md5(
        f"{sip_user}:{sip_domain}:{sip_pass}".encode()
    ).hexdigest() if sip_user and sip_domain and sip_pass else ""

    return {
        "sip_user":     sip_user,
        "sip_password": sip_pass,
        "sip_domain":   sip_domain,
        "sip_ha1":      sip_ha1,
        "cloud_proxy":  cloud_proxy,
        "gid":          fields.get(QR_GID, ""),
        "plant_type":   fields.get(QR_PLANTTYPE, ""),
        "mac":          fields.get(QR_MAC, ""),
    }


# ─── Backend AES: prova pycryptodome, poi cryptography ──────────────────────

def _aes_decrypt(key: bytes, ciphertext: bytes, iv: bytes) -> str:
    """AES-256-CBC decrypt, restituisce stringa UTF-8 senza padding."""
    try:
        from Crypto.Cipher import AES  # pycryptodome / pycryptodomex
        cipher = AES.new(key, AES.MODE_CBC, iv)
        plaintext = cipher.decrypt(ciphertext)
    except ImportError:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            cipher_obj = Cipher(
                algorithms.AES(key), modes.CBC(iv), backend=default_backend()
            )
            decryptor = cipher_obj.decryptor()
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        except ImportError as exc:
            raise QRDecodeError(
                "Nessuna libreria AES disponibile. "
                "Installa pycryptodome: pip install pycryptodome"
            ) from exc

    # Rimuovi PKCS7 padding
    if plaintext:
        pad = plaintext[-1]
        if 1 <= pad <= 16:
            plaintext = plaintext[:-pad]

    return plaintext.decode("utf-8", errors="replace")
