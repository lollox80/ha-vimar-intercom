"""Constants for Vimar Intercom integration.

Questo file contiene SOLO costanti statiche non sensibili.
Le credenziali SIP (user, password, domain, HA1) sono gestite dal config
flow e memorizzate cifrate in HA config entries — NON hardcodate qui.
"""

import os

DOMAIN = "vimar_intercom"

# ─── Device Info ──────────────────────────────────────────────────────────────
MANUFACTURER = "Vimar"
# Fallback generico: il modello reale viene rilevato dagli header SIP del
# citofono (vedi model_detect.py) e scritto nel device registry di HA.
MODEL        = "Elvox Tab 7S 2F+ WiFi"   # 40507 — dal QR: planttype=2F (Due Fili Plus), non IP

# ─── SIP — valori di default per cloud (override da config entry) ─────────────
SIP_PORT = 7042                          # porta TLS cloud
SIP_SNI  = "ipvdes.vimar.cloud"
SIP_ROUTE = "ipvdes.vimar.cloud"

# ─── Porta SIP locale (Flexisip sul citofono) ─────────────────────────────────
LOCAL_SIP_PORT = 5060

# ─── Transport mode — default, può essere sovrascritto da config entry ─────────
# Modificabile via Options Flow in HA: Impostazioni → Integrazioni → Vimar Intercom
USE_LOCAL_UDP  = True    # True = UDP locale; False = TLS/TCP cloud
LOCAL_UDP_PORT = 5060    # porta UDP locale su HA

# ─── Door targets ──────────────────────────────────────────────────────────────
# Costruiti a runtime da hub.py usando le credenziali del config entry.
# Il comando ATT_ID 8 = modulo 2F (Serratura)
DOOR_COMMAND = "OPEN_2F"

# ─── RTP / Media ──────────────────────────────────────────────────────────────
RTP_AUDIO_PORT     = 7200
RTP_VIDEO_PORT     = 9200
FFMPEG_VIDEO_PORT  = 19200    # MJPEG ffmpeg legge video qui
FFMPEG_AV_VIDEO_PORT = 19201  # AV ffmpeg video
FFMPEG_AV_AUDIO_PORT = 19202  # AV ffmpeg audio

# ─── Push Notifications — opzionale, non necessario per UDP locale ────────────
PN_APP_ID = "toga-prod"
PN_TYPE   = "firebase"

# ─── User-Agent — stesso dell'app originale per compatibilità Flexisip ─────────
USER_AGENT = "TOGA_Googlesdk_gphone64_arm64_Android34/1.0|AppVer:2.4.0|ProtVer:1.0|"

# ─── Identità dispositivo HA (non sensibile — non è un IMEI reale) ────────────
MY_NAME     = "Home Assistant"
DEVICE_IMEI = "351234567890123"   # fake IMEI statico — serve solo per l'header SIP
DEVICE_UUID = DEVICE_IMEI

# ─── Push notifications (opzionale — lascia vuoto per disabilitare) ───────────
# Riempi solo se vuoi ricevere push Firebase/FCM su dispositivi Android.
# In modalità UDP locale non è necessario.
PN_TOKEN = ""

# ─── Certificato CA Vimar (per modalità TLS cloud) ───────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CA_PATH    = os.path.join(SCRIPT_DIR, "vimar_rootca.pem")

# ─── APNs VoIP Push (opzionale — solo per push iOS) ──────────────────────────
APNS_KEY_PATH  = os.path.join(SCRIPT_DIR, "AuthKey.p8")
APNS_KEY_ID    = ""
APNS_TEAM_ID   = ""
APNS_BUNDLE_ID = "noiseheroes.Home"
APNS_SANDBOX   = True

# ─── Segreteria (answering machine) — comando DA CONFERMARE ──────────────────
# SGA (MAGIC_APT_INTERCOM) = destinatario di VOICEMAIL;ON/OFF e DND;ON/OFF con Panda: blue.
# CONFERMATO 19/08/2026 dalla rubrica.db reale (SYSTEM.MAGIC_APT_INTERCOM = "55001", PICG "Casa CG")
# e verificato sul campo: `VOICEMAIL;ON` → 55001 fa accendere la segreteria sul Tab.
# (I vecchi tentativi verso 55002/61000/60002/101 davano 200 senza effetto: target sbagliato.)
#
# Da qui in poi questi due valori sono SOLO IL DEFAULT DI FALLBACK per il primo
# impianto verificato: il valore effettivo usato a runtime è configurabile via
# options (manualmente o dall'importer rubrica.db) e vive in
# runtime.SGA_TARGET / runtime.PICG_TARGET (vedi runtime.configure()). Il resto
# del codice (hub.py, switch.py, button.py, __init__.py) legge da lì, mai da
# queste costanti direttamente.
SGA_TARGET              = "55001"
# PICG (capogruppo appartamento) — destinatario di GET_INIT_STATUS / GET_NICKS.
# Su questo impianto coincide con l'SGA (55001). [VERIFICATO 20/08/2026]
PICG_TARGET             = SGA_TARGET
# SEGRETERIA_TARGET/DND_TARGET non sono più letti dal codice (switch.py usa
# runtime.SGA_TARGET): restano solo come alias storici/di comodo.
SEGRETERIA_TARGET       = SGA_TARGET
SEGRETERIA_ON           = "VOICEMAIL;ON"
SEGRETERIA_OFF          = "VOICEMAIL;OFF"
SEGRETERIA_HEADER_NAME  = "Panda"
SEGRETERIA_HEADER_VALUE = "blue"   # dall'app VIEW: i messaggi di stato usano Panda: blue

# Non disturbare — "DND;ON" / "DND;OFF" verso l'SGA (Panda: blue).
DND_TARGET              = SGA_TARGET
DND_ON                  = "DND;ON"
DND_OFF                 = "DND;OFF"

# ─── Attuatori aggiuntivi (visti nell'app VIEW → Videocitofonia) ─────────────
# Stessa famiglia di OPEN_2F (header Panda: command). I token OPEN_F1/OPEN_F2
# sono quelli standard dei relè aux della targa; LUCE SCALA / Attuatore 02 /
# TIRO sono IPOTESI da confermare col test o con la cattura del MESSAGE.
# Formato: (key, nome, target, comando, icona)
ACTUATORS = []  # RIMOSSI 18/08/2026: i token ipotizzati (OPEN_F1/OPEN_2/OPEN_2F)
# aprivano la PORTA anziché comandare F1/F2/Luce Scala/Attuatore 02.
# Luce Scala e Attuatore 02 sono oggetti By-me (solo cloud, non SIP).
# Ripristinare solo con i comandi reali ricavati dall'APK decifrata.

# ─── Targa video (autoaccensione camera on-demand) ───────────────────────────
# La camera on-demand chiama QUESTA targa per accendere il video (autoaccensione),
# NON il PICG 55001 (che dava 488 Not Acceptable Here). Dalla rubrica.db:
# PHONEBOOK GID=55100 TYPE='PE' NAME='Video'. [da rubrica 20/08/2026]
# TODO: rendere configurabile in options / ricavare da PHONEBOOK (TYPE PE).
CAMERA_TARGET = "55100"

# ─── Comandi di stato (in USCITA, Panda: blue) ───────────────────────────────
GET_INIT_STATUS = "GET_INIT_STATUS"   # → PICG_TARGET; risposta GET_INIT_STATUS_REPLY

# ─── Eventi bus HA (in INGRESSO — vedi PROTOCOL.md §4) ────────────────────────
# Payload documentati in README §Eventi. Namespace = DOMAIN.
EVENT_MISSED_CALL       = f"{DOMAIN}_missed_call"        # {sip_id, ts, name}
EVENT_VIDEOMESSAGE      = f"{DOMAIN}_videomessage"       # {change: NEW|UPDATE, full}
EVENT_FUORIPORTA        = f"{DOMAIN}_fuoriporta"         # {sip_id, msg}
EVENT_CALL_INFO         = f"{DOMAIN}_call_info"          # {sip_id, reason, media_type, video_src}
EVENT_PHONEBOOK_CHANGED = f"{DOMAIN}_phonebook_changed"  # {gid, rubrica_ver}

