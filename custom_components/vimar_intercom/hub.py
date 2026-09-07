"""Vimar Intercom Hub — manages SIP + media lifecycle."""

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

from . import sip_client as sip
from . import media_handler as media
from . import push_sender
from . import const as C
from . import runtime as R

_LOGGER = logging.getLogger(__name__)

STREAM_HANGUP_DELAY = 30

# Nomi "umani" degli indirizzi SIP dell'impianto
SIP_ID_NAMES = {
    "55001": "Targa Esterna",
    "55002": "Targa Interna",
    "60001": "Monitor Interno",
}


def _uri_to_id(uri: str | None) -> str | None:
    """'sip:55001@dominio' → '55001'."""
    if not uri:
        return None
    u = uri.replace("sip:", "").replace("sips:", "")
    return u.split("@")[0].split(";")[0] or None


def sip_id_name(sip_id: str | None) -> str | None:
    if not sip_id:
        return None
    return SIP_ID_NAMES.get(sip_id, sip_id)
MAX_CALL_DURATION = 300  # 5 minutes — auto-hangup safety net

# Interni interrogati con un OPTIONS all'avvio per farsi identificare dal
# citofono quando il modello non è ancora noto (OPTIONS è innocuo: è lo stesso
# messaggio già usato come keepalive).
MODEL_PROBE_TARGETS = ("55001", "55002", "60001")


class VimarIntercomHub:
    """Orchestrates SIP registration, calls, door control, and media."""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._ring_callbacks: list[Callable] = []
        self._state_callbacks: list[Callable] = []
        self._model_callbacks: list[Callable] = []
        self._ws_broadcast_fn: Callable | None = None
        self._has_ws_clients: Callable | None = None
        self._stream_viewers = 0
        self._hangup_task: asyncio.Task | None = None
        self._call_timeout_task: asyncio.Task | None = None
        self._keyframe_task: asyncio.Task | None = None
        self._auto_called = False
        self._auto_call_target: str | None = None

        # ─── Statistiche / stato esteso (esposte da sensor.py) ───────────
        self.stats: dict = {
            "last_ring_time": None,        # datetime UTC ultimo squillo
            "last_caller_id": None,        # es. "55001"
            "last_caller_uri": None,       # es. "sip:55001@dominio"
            "ring_count": 0,               # squilli dall'avvio
            "missed_count": 0,             # squilli non risposti da HA
            "last_call_start": None,
            "last_call_end": None,
            "last_call_duration": None,    # secondi
            "last_call_direction": None,   # "in" | "out"
            "call_count": 0,               # chiamate attive dall'avvio
            "last_door_time": None,
            "last_door_target": None,
            "last_door_result": None,
            "door_count": 0,
            "last_register_time": None,
            "register_failures": 0,
            "last_command_time": None,
            "last_command_body": None,
            "last_command_target": None,
            "last_command_result": None,
            "last_error": None,
            "last_error_time": None,
            "voicemail": None,             # stato segreteria annunciato dal Tab (True/False)
            "dnd": None,                   # stato Non disturbare annunciato dal Tab
            "vm_level": None,              # spazio segreteria, es. "0/100"
            "rubrica_ver": None,           # versione (md5) della rubrica del Tab
            "vm_ver": None,                # versione (md5) del db videomessaggi
            "init_status": {},             # ultimo GET_INIT_STATUS_REPLY grezzo {PARAM: VALUE}
            "last_message_in": None,       # ultimo SIP MESSAGE ricevuto dal citofono
            "last_message_in_time": None,
            # ─── Eventi in ingresso (PROTOCOL.md §4) ─────────────────────────
            "last_missed_call": None,      # dict {sip_id, ts, name}
            "missed_call_count": 0,        # MISSED_CALL ricevuti dall'avvio
            "new_videomessage": False,     # ON su VM;VIDEO_MESSAGE_CHANGE;NEW
            "last_videomessage": None,     # ultimo change grezzo
            "last_fuoriporta": None,       # dict {sip_id, msg}
            "last_call_info": None,        # dict {sip_id, reason, media_type, video_src}
            "started_at": datetime.now(timezone.utc),
        }
        self._call_started_mono: float | None = None
        self._ring_answered = False
        # Callback per emettere eventi bus HA (registrati da __init__.py).
        # Evita di iniettare hass nell'hub, coerente con ring/state callbacks.
        self._event_callbacks: list[Callable] = []
        self._init_status_sent = False

    # ─── helpers stato esteso ────────────────────────────────────────────
    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def _touch(self):
        """Notifica le entità HA che le statistiche sono cambiate."""
        for cb in self._state_callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("State callback error")

    @property
    def calling(self) -> bool:
        return sip.calling

    @property
    def local_ip(self) -> str | None:
        return sip.MY_IP

    @property
    def transport(self) -> str:
        return "udp-local" if R.USE_LOCAL_UDP else "tls-cloud"

    @property
    def proxy(self) -> str:
        return R.LOCAL_PROXY if R.USE_LOCAL_UDP else R.SIP_PROXY

    @property
    def sip_user(self) -> str:
        return R.SIP_USER

    @property
    def sip_domain(self) -> str:
        return R.SIP_DOMAIN

    @property
    def voicemail(self) -> bool | None:
        """Stato segreteria annunciato dal Tab (None finché sconosciuto)."""
        return self.stats.get("voicemail")

    @property
    def dnd(self) -> bool | None:
        """Stato Non disturbare annunciato dal Tab (None finché sconosciuto)."""
        return self.stats.get("dnd")

    @property
    def status(self) -> str:
        """Stato sintetico: offline / ringing / in_call / calling / idle."""
        if not sip.registered:
            return "offline"
        if sip.pending_incoming["active"]:
            return "ringing"
        if sip.in_call:
            return "in_call"
        if sip.calling:
            return "calling"
        return "idle"

    def set_ws_broadcast(self, fn: Callable):
        self._ws_broadcast_fn = fn

    @property
    def registered(self) -> bool:
        return sip.registered

    @property
    def in_call(self) -> bool:
        return sip.in_call

    @property
    def is_ringing(self) -> bool:
        return sip.pending_incoming["active"]

    @property
    def video_frame(self) -> bytes | None:
        return None  # Video sent directly via WebSocket H.264 NALs

    def register_ring_callback(self, callback: Callable) -> None:
        self._ring_callbacks.append(callback)

    def unregister_ring_callback(self, callback: Callable) -> None:
        if callback in self._ring_callbacks:
            self._ring_callbacks.remove(callback)

    def register_state_callback(self, callback: Callable) -> None:
        """Register a callback for SIP state changes (registered, in_call)."""
        self._state_callbacks.append(callback)

    def unregister_state_callback(self, callback: Callable) -> None:
        if callback in self._state_callbacks:
            self._state_callbacks.remove(callback)

    def register_event_callback(self, callback: Callable) -> None:
        """Registra un callback(event_type: str, data: dict) per gli eventi
        in ingresso da esporre sul bus HA (missed_call, videomessage, ...)."""
        self._event_callbacks.append(callback)

    def unregister_event_callback(self, callback: Callable) -> None:
        if callback in self._event_callbacks:
            self._event_callbacks.remove(callback)

    def _fire_event(self, event_type: str, data: dict) -> None:
        """Propaga un evento in ingresso ai callback registrati (bus HA)."""
        for cb in self._event_callbacks:
            try:
                cb(event_type, data)
            except Exception:
                _LOGGER.exception("Event callback error (%s)", event_type)

    @property
    def detected_model(self) -> str:
        """Modello rilevato via SIP (stringa vuota se ancora sconosciuto)."""
        return R.DETECTED_MODEL

    def register_model_callback(self, callback: Callable) -> None:
        """Callback(model, fw, user_agent, priority) sul rilevamento modello."""
        self._model_callbacks.append(callback)

    def unregister_model_callback(self, callback: Callable) -> None:
        if callback in self._model_callbacks:
            self._model_callbacks.remove(callback)

    def _on_model_detected(self, model: str, fw: str, ua: str, priority: int):
        """Chiamata da sip_client quando un peer SIP rivela il modello."""
        for cb in self._model_callbacks:
            try:
                cb(model, fw, ua, priority)
            except Exception:
                _LOGGER.exception("Model callback error")

    async def _probe_model(self):
        """Interroga gli interni con un OPTIONS finché qualcuno si identifica."""
        await asyncio.sleep(3)
        for target in MODEL_PROBE_TARGETS:
            if R.DETECTED_MODEL:
                break
            uri = f"sip:{target}@{R.SIP_DOMAIN}"
            try:
                await sip.do_options(target=uri)
            except Exception as e:
                _LOGGER.debug("Model probe %s fallito: %s", target, e)
            await asyncio.sleep(0.5)

        if R.DETECTED_MODEL:
            _LOGGER.info("Modello citofono: %s", R.DETECTED_MODEL)
        else:
            _LOGGER.info(
                "Modello non rilevato — nessun peer SIP si è identificato. "
                "User-Agent visti finora: %s", sorted(sip._seen_uas) or "nessuno")

    def _on_sip_state_change(self):
        """Called by sip_client when registered/in_call changes."""
        for cb in self._state_callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception("State callback error")
        # Notify WS clients of state change
        if self._ws_broadcast_fn:
            task = asyncio.create_task(self._ws_broadcast_fn({
                "type": "state",
                "registered": sip.registered,
                "in_call": sip.in_call,
            }))
            task.add_done_callback(
                lambda t: _LOGGER.error("WS state broadcast error: %s", t.exception())
                if not t.cancelled() and t.exception() else None
            )

    async def stream_opened(self, target: str | None = None):
        self._stream_viewers += 1
        _LOGGER.info("Stream opened (%d viewers, target=%s)", self._stream_viewers, target)

        if self._hangup_task:
            self._hangup_task.cancel()
            self._hangup_task = None

        if sip.in_call or sip.calling:
            return

        # Don't auto-call when iOS app WS clients are connected —
        # the app sends the call action explicitly via WebSocket.
        if self._has_ws_clients and self._has_ws_clients():
            _LOGGER.info("Stream opened but WS clients connected — skipping auto-call")
            return

        if sip.registered:
            self._auto_called = True
            self._auto_call_target = target
            # Fire auto-call as background task — don't block the HTTP response
            asyncio.create_task(self._do_auto_call(target))

    async def _do_auto_call(self, target: str | None):
        """Background auto-call when video stream opens without active call."""
        try:
            if target:
                uri = f"sip:{target}@{sip.C.SIP_DOMAIN}"
                ok, msg = await sip.do_call(target=uri)
            else:
                # Autoaccensione: chiama la TARGA VIDEO (55100), non il PICG 55001
                # (55001 dava 488 Not Acceptable Here — vedi const.CAMERA_TARGET).
                uri = f"sip:{sip.C.CAMERA_TARGET}@{sip.C.SIP_DOMAIN}"
                ok, msg = await sip.do_call(target=uri)
            if not ok:
                _LOGGER.error("Auto-call failed: %s", msg)
                self._auto_called = False
        except Exception as e:
            _LOGGER.error("Auto-call error: %s", e)
            self._auto_called = False

    async def stream_closed(self):
        self._stream_viewers = max(0, self._stream_viewers - 1)
        _LOGGER.info("Stream viewer disconnected (%d remaining)", self._stream_viewers)

        if self._stream_viewers == 0 and self._auto_called and sip.in_call:
            self._hangup_task = asyncio.create_task(self._delayed_hangup())

    async def _delayed_hangup(self):
        try:
            await asyncio.sleep(STREAM_HANGUP_DELAY)
            if self._stream_viewers == 0 and self._auto_called and sip.in_call:
                _LOGGER.info("No viewers, hanging up auto-call")
                await sip.do_hangup()
                self._auto_called = False
        except asyncio.CancelledError:
            pass

    def _start_call_timeout(self):
        """Start max call duration timer."""
        self._cancel_call_timeout()
        self._call_timeout_task = asyncio.create_task(self._call_timeout())

    def _cancel_call_timeout(self):
        if self._call_timeout_task:
            self._call_timeout_task.cancel()
            self._call_timeout_task = None

    async def _call_timeout(self):
        try:
            await asyncio.sleep(MAX_CALL_DURATION)
            if sip.in_call:
                _LOGGER.info("Max call duration (%ds) reached, hanging up", MAX_CALL_DURATION)
                await sip.do_hangup()
                self._auto_called = False
        except asyncio.CancelledError:
            pass

    def _start_keyframe_loop(self):
        """Send periodic keyframe requests during calls for video recovery."""
        self._cancel_keyframe_loop()
        self._keyframe_task = asyncio.create_task(self._keyframe_loop())

    def _cancel_keyframe_loop(self):
        if self._keyframe_task:
            self._keyframe_task.cancel()
            self._keyframe_task = None

    async def _keyframe_loop(self):
        """Keyframe burst at start, then slow periodic refresh.

        Il burst serve a ottenere SPS/PPS+IDR appena parte il video. Dopo,
        se il video sta effettivamente arrivando (pkt_count cresce) rallentiamo
        molto: un INFO ogni 2s spammava il proxy (e i 407) senza utilità.
        """
        def _video_flowing():
            vp = media.video_proto
            return bool(vp and vp.pkt_count > 0)

        try:
            # Immediate first request — no delay
            if sip.in_call:
                await sip.send_keyframe_request()
            # Rapid burst: 8 requests at 150ms intervals to grab the first IDR
            for _ in range(8):
                await asyncio.sleep(0.15)
                if not sip.in_call:
                    return
                if _video_flowing():
                    break
                await sip.send_keyframe_request()
            # Then slow refresh: every 5s, only while the call lasts.
            while sip.in_call:
                await asyncio.sleep(5)
                if sip.in_call:
                    await sip.send_keyframe_request()
        except asyncio.CancelledError:
            pass

    async def async_start(self):
        if self._running:
            return

        sip.init(self._handle_broadcast)
        sip.set_state_callback(self._on_sip_state_change)
        sip.set_model_callback(self._on_model_detected)
        media.init(self._handle_broadcast)

        sip.MY_IP = sip.get_local_ip()
        sip.incoming_requests = asyncio.Queue()
        _LOGGER.info("Local IP: %s", sip.MY_IP)

        await media.setup_transports()
        _LOGGER.info("RTP transports ready")

        await sip.connect()

        self._tasks.append(asyncio.create_task(sip.reader_task()))
        self._tasks.append(asyncio.create_task(sip.request_processor()))
        self._tasks.append(asyncio.create_task(self._auto_startup()))
        self._tasks.append(asyncio.create_task(self._keepalive_loop()))
        if R.USE_LOCAL_UDP:
            self._tasks.append(asyncio.create_task(sip.udp_register_refresh_task()))
        self._running = True

    async def async_stop(self):
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        if self._hangup_task:
            self._hangup_task.cancel()
        self._cancel_call_timeout()
        self._cancel_keyframe_loop()
        await media.stop_media()
        media.close_transports()
        if sip.writer:
            try:
                sip.writer.close()
            except Exception:
                pass
        if sip._udp_sock:
            try:
                sip._udp_sock.close()
            except Exception:
                pass
        _LOGGER.info("Hub stopped")

    async def async_call(self, target: str | None = None) -> tuple[bool, str]:
        self._auto_called = False
        if target:
            uri = f"sip:{target}@{R.SIP_DOMAIN}"
            return await sip.do_call(target=uri)
        return await sip.do_call()

    async def async_answer(self) -> tuple[bool, str]:
        ok, msg = await sip.do_answer_incoming()
        if ok:
            self._ring_answered = True
            self.stats["last_call_direction"] = "in"
        self._touch()
        return ok, msg

    async def async_decline(self):
        await sip.do_decline_incoming()
        self._touch()


    async def async_hangup(self):
        self._auto_called = False
        self._cancel_call_timeout()
        await sip.do_hangup()

    async def async_door(self, target: str | None = None, command: str | None = None) -> tuple[bool, str]:
        """Open door via SIP MESSAGE to targa (PE) address.

        From Tab5S rubrica ACTUATOR_LIST:
          55001 (targa master)  → OPEN_2F = Portone Esterno
          55002 (targa interna) → OPEN_2F = Portone Interno
        The targa forwards the command to its local relay.
        No active call required.
        """
        if target:
            uri = f"sip:{target}@{R.SIP_DOMAIN}"
            body = command or C.DOOR_COMMAND
        else:
            uri = R.DOOR_ESTERNO
            body = C.DOOR_COMMAND

        _LOGGER.info("Door command: uri=%s body=%s registered=%s", uri, body, sip.registered)

        ok, msg = await sip.do_system_message(
            uri, body, extra_headers={"Panda": "command"})

        self.stats["last_door_time"] = self._now()
        self.stats["last_door_target"] = target or "55001"
        self.stats["last_door_result"] = msg
        if ok:
            self.stats["door_count"] += 1
        self._touch()

        if ok:
            _LOGGER.info("Door open OK: %s", msg)
            return ok, msg


        # Retry once after re-registration — handles stale connection
        _LOGGER.warning("Door command failed (%s), retrying after re-register...", msg)
        try:
            reg_ok = await sip.do_register()
            if reg_ok:
                ok2, msg2 = await sip.do_system_message(
                    uri, body, extra_headers={"Panda": "command"})
                self.stats["last_door_result"] = msg2
                if ok2:
                    self.stats["door_count"] += 1
                    self._touch()
                    _LOGGER.info("Door open OK on retry: %s", msg2)
                    return ok2, msg2
                self._touch()
                _LOGGER.error("Door retry also failed: %s", msg2)
                return ok2, msg2
            else:
                _LOGGER.error("Re-registration failed, cannot retry door")
                return False, "Re-registrazione fallita"
        except Exception as e:
            _LOGGER.error("Door retry error: %s", e)
            return False, str(e)

    async def async_send_command(
        self,
        body: str,
        target: str = "55001",
        header_name: str | None = "Panda",
        header_value: str | None = "command",
    ) -> tuple[bool, str]:
        """Invia un SIP MESSAGE arbitrario al citofono (per test / comandi non ancora mappati).

        target può essere un ID (es. "55001") oppure un URI sip: completo.
        """
        if target.startswith("sip:"):
            uri = target
        else:
            uri = f"sip:{target}@{R.SIP_DOMAIN}"
        headers = {header_name: header_value} if header_name else None
        _LOGGER.info("Custom command: uri=%s body=%r headers=%s", uri, body, headers)
        try:
            ok, msg = await sip.do_system_message(uri, body, extra_headers=headers)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, str(e)
        self.stats["last_command_time"] = self._now()
        self.stats["last_command_body"] = body
        self.stats["last_command_target"] = target
        self.stats["last_command_result"] = msg
        self._touch()
        return ok, msg

    async def async_probe(self, target: str) -> tuple[bool, str]:
        uri = f"sip:{target}@{R.SIP_DOMAIN}"
        return await sip.do_options(target=uri)

    async def async_scan(self, start: int, end: int) -> list[dict]:
        results = []
        for addr in range(start, end + 1):
            uri = f"sip:{addr}@{R.SIP_DOMAIN}"
            try:
                ok, msg = await sip.do_options(target=uri)
                results.append({"addr": addr, "ok": ok, "msg": msg})
            except Exception as e:
                results.append({"addr": addr, "ok": False, "msg": str(e)})
            await asyncio.sleep(0.3)
        return results

    async def _handle_broadcast(self, msg_type, msg):
        _LOGGER.debug("[%s] %s", msg_type, msg)
        self._update_stats(msg_type, msg)

        if msg_type in ("ring", "ring_ended", "call_started", "call_ended", "registered", "error"):
            # Don't broadcast "ring" to WS clients if we initiated the call
            if msg_type == "ring" and (self._auto_called or sip.in_call or sip.calling):
                pass  # Will be handled below (suppress + decline)
            elif self._ws_broadcast_fn:
                try:
                    payload = {
                        "type": msg_type, "msg": msg,
                        "registered": sip.registered, "in_call": sip.in_call,
                    }
                    # Include caller URI so clients can identify which panel is ringing
                    if msg_type == "ring" and sip.pending_incoming.get("caller_uri"):
                        payload["caller_uri"] = sip.pending_incoming["caller_uri"]
                    await self._ws_broadcast_fn(payload)
                except Exception:
                    _LOGGER.exception("WS broadcast error")

        if msg_type == "call_started":
            self._start_call_timeout()
            self._start_keyframe_loop()
        elif msg_type == "call_ended":
            self._cancel_call_timeout()
            self._cancel_keyframe_loop()

        if msg_type == "ring":
            # If we initiated the call (tap to view / auto-call), the Tab5S
            # sends an INVITE back to us. Suppress ring + push — this is NOT
            # a doorbell ring, just the PBX echoing our outgoing call.
            if self._auto_called or sip.in_call or sip.calling:
                _LOGGER.info("Suppressing ring — we initiated this call (auto_called=%s, in_call=%s, calling=%s)",
                             self._auto_called, sip.in_call, sip.calling)
                asyncio.create_task(sip.do_decline_incoming())
                return

            for cb in self._ring_callbacks:
                try:
                    cb()
                except Exception:
                    _LOGGER.exception("Ring callback error")

            # Send VoIP push to wake iOS devices
            sender = push_sender.get_sender()
            if sender:
                caller = sip.pending_incoming.get("caller_uri", "55001")
                # Extract SIP user from URI (e.g. "sip:55001@domain" → "55001")
                if "@" in caller:
                    caller = caller.split("@")[0].replace("sip:", "")
                panel = "esterna"  # TODO: detect panel from caller
                asyncio.create_task(sender.send_voip_push(caller=caller, panel=panel))

    def _update_stats(self, msg_type: str, msg):
        """Aggiorna le statistiche in base agli eventi SIP."""
        st = self.stats
        now = self._now()
        try:
            if msg_type == "ring":
                # Squillo reale solo se non l'abbiamo originato noi
                if not (self._auto_called or sip.in_call or sip.calling):
                    caller = sip.pending_incoming.get("caller_uri") or ""
                    st["last_ring_time"] = now
                    st["last_caller_uri"] = caller or None
                    st["last_caller_id"] = _uri_to_id(caller)
                    st["ring_count"] += 1
                    self._ring_answered = False
            elif msg_type == "ring_ended":
                if not self._ring_answered and st["last_ring_time"]:
                    st["missed_count"] += 1
            elif msg_type == "call_started":
                self._call_started_mono = time.monotonic()
                st["last_call_start"] = now
                st["call_count"] += 1
                if not self._ring_answered:
                    st["last_call_direction"] = "out"
            elif msg_type == "call_ended":
                st["last_call_end"] = now
                if self._call_started_mono is not None:
                    st["last_call_duration"] = round(time.monotonic() - self._call_started_mono, 1)
                    self._call_started_mono = None
                self._ring_answered = False
            elif msg_type == "registered":
                st["last_register_time"] = now
            elif msg_type == "error":
                st["last_error"] = str(msg)[:200]
                st["last_error_time"] = now
            elif msg_type == "message":
                st["last_message_in"] = str(msg)[:200]
                st["last_message_in_time"] = now
                self._handle_incoming_message(str(msg))
        except Exception:
            _LOGGER.exception("stats update error")
        self._touch()

    # ─── Parsing dei SIP MESSAGE in ingresso (Panda: blue) ───────────────────
    # Qui si LEGGE soltanto: nessun comando in uscita. Parsing difensivo: alcuni
    # body sono JSON, altri delimitati da ';'. Se non combacia → debug, no crash.
    def _handle_incoming_message(self, body: str) -> None:
        st = self.stats
        raw = (body or "").strip()
        upper = raw.upper()

        # Annunci di stato: "VOICEMAIL;ON|OFF" / "DND;ON|OFF" [VERIFICATO]
        if upper.startswith("VOICEMAIL;"):
            st["voicemail"] = ("ON" in upper and "OFF" not in upper)
            return
        if upper.startswith("DND;"):
            st["dnd"] = ("ON" in upper and "OFF" not in upper)
            return

        # GET_INIT_STATUS_REPLY;<json array [{PARAM,VALUE}]>
        if upper.startswith("GET_INIT_STATUS_REPLY"):
            self._parse_init_status_reply(raw)
            return

        # MISSED_CALL;{json}  [da confermare sul campo — PROTOCOL.md §4]
        if upper.startswith("MISSED_CALL"):
            self._handle_missed_call(raw)
            return

        # VM;VIDEO_MESSAGE_CHANGE;NEW[;<n>] | ;UPDATE  [da confermare sul campo]
        if upper.startswith("VM;VIDEO_MESSAGE_CHANGE"):
            self._handle_videomessage(raw)
            return

        # FP;{json}  fuoriporta  [da confermare sul campo]
        if upper.startswith("FP;") or upper.startswith("FP{"):
            self._handle_fuoriporta(raw)
            return

        # CALL_INFO;{json}  [da confermare sul campo]
        if upper.startswith("CALL_INFO"):
            self._handle_call_info(raw)
            return

        # NEW_PHONEBOOK;<gid>;<ver>  [da confermare sul campo]
        if upper.startswith("NEW_PHONEBOOK"):
            self._handle_new_phonebook(raw)
            return

        _LOGGER.debug("MESSAGE in ingresso non mappato: %r", raw[:120])

    @staticmethod
    def _split_json_payload(raw: str, prefix_parts: int):
        """Restituisce (json_str | None) dopo aver saltato `prefix_parts`
        segmenti separati da ';'. Es. raw='MISSED_CALL;{...}' → prefix_parts=1."""
        parts = raw.split(";", prefix_parts)
        if len(parts) <= prefix_parts:
            return None
        return parts[prefix_parts].strip()

    def _parse_init_status_reply(self, raw: str) -> None:
        """Parsa GET_INIT_STATUS_REPLY;[{PARAM,VALUE}] in modo generico e robusto.

        NB: il body dei MESSAGE arriva TRONCATO a 200 char da sip_client.broadcast,
        quindi il JSON può essere incompleto → usiamo un fallback a regex sui
        segmenti {PARAM..VALUE} presenti, così estraiamo tutto ciò che c'è.
        """
        import json
        import re

        payload = self._split_json_payload(raw, 1) or ""
        pairs: dict[str, str] = {}
        try:
            arr = json.loads(payload)
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict) and "PARAM" in item:
                        pairs[str(item["PARAM"])] = item.get("VALUE")
        except Exception:
            # JSON incompleto/troncato: estrai le coppie PARAM/VALUE via regex.
            for m in re.finditer(
                r'"PARAM"\s*:\s*"([^"]+)"\s*,\s*"VALUE"\s*:\s*"([^"]*)"', payload
            ):
                pairs[m.group(1)] = m.group(2)
            if not pairs:
                _LOGGER.debug("GET_INIT_STATUS_REPLY non parsabile: %r", payload[:120])

        if not pairs:
            return

        st = self.stats
        st["init_status"] = {**st.get("init_status", {}), **pairs}

        def _as_bool(v):
            return str(v).strip() in ("1", "true", "True", "ON", "on")

        if "voicemail" in pairs:
            st["voicemail"] = _as_bool(pairs["voicemail"])
        if "dnd" in pairs:
            st["dnd"] = _as_bool(pairs["dnd"])
        if "vm_level" in pairs:
            st["vm_level"] = pairs["vm_level"]
        if "vm_ver" in pairs:
            st["vm_ver"] = pairs["vm_ver"]
        # token / altri param restano in init_status per usi futuri (phonebook cloud)
        if "rubrica_ver" in pairs:
            self._update_rubrica_ver(pairs["rubrica_ver"])

        _LOGGER.info(
            "GET_INIT_STATUS_REPLY: voicemail=%s dnd=%s vm_level=%s rubrica_ver=%s",
            st.get("voicemail"), st.get("dnd"), st.get("vm_level"), st.get("rubrica_ver"),
        )

    def _update_rubrica_ver(self, new_ver, gid: str | None = None) -> None:
        """Aggiorna rubrica_ver; se CAMBIA (dopo il primo) emette phonebook_changed."""
        new_ver = None if new_ver is None else str(new_ver)
        old = self.stats.get("rubrica_ver")
        self.stats["rubrica_ver"] = new_ver
        if old is not None and new_ver is not None and new_ver != old:
            _LOGGER.info("Rubrica cambiata: %s → %s", old, new_ver)
            self._fire_event(
                C.EVENT_PHONEBOOK_CHANGED,
                {"gid": gid or R.SIP_USER, "rubrica_ver": new_ver},
            )

    def _handle_missed_call(self, raw: str) -> None:
        import json
        data = {"sip_id": None, "ts": None}
        payload = self._split_json_payload(raw, 1)
        if payload:
            try:
                j = json.loads(payload)
                if isinstance(j, dict):
                    data["sip_id"] = j.get("SIP_ID") or j.get("sip_id")
                    data["ts"] = j.get("TS") or j.get("ts")
            except Exception:
                _LOGGER.debug("MISSED_CALL payload non-JSON: %r", payload[:120])
        data["name"] = sip_id_name(str(data["sip_id"])) if data["sip_id"] is not None else None
        self.stats["last_missed_call"] = data
        self.stats["missed_call_count"] += 1
        _LOGGER.info("Chiamata persa: %s", data)
        self._fire_event(C.EVENT_MISSED_CALL, data)

    def _handle_videomessage(self, raw: str) -> None:
        # VM;VIDEO_MESSAGE_CHANGE;NEW[;<n>] | ;UPDATE
        parts = raw.split(";")
        change = parts[2].strip().upper() if len(parts) > 2 else "NEW"
        extra = parts[3].strip() if len(parts) > 3 else None
        is_new = change == "NEW"
        self.stats["new_videomessage"] = is_new
        self.stats["last_videomessage"] = raw[:120]
        _LOGGER.info("Videomessaggio: change=%s extra=%s", change, extra)
        self._fire_event(
            C.EVENT_VIDEOMESSAGE, {"change": change, "extra": extra, "full": raw[:120]}
        )

    def _handle_fuoriporta(self, raw: str) -> None:
        import json
        data = {"sip_id": None, "msg": None}
        payload = self._split_json_payload(raw, 1)
        if payload:
            try:
                j = json.loads(payload)
                if isinstance(j, dict):
                    data["sip_id"] = j.get("SIP_ID") or j.get("sip_id")
                    data["msg"] = j.get("MSG") or j.get("msg")
            except Exception:
                _LOGGER.debug("FP payload non-JSON: %r", payload[:120])
        self.stats["last_fuoriporta"] = data
        _LOGGER.info("Fuoriporta: %s", data)
        self._fire_event(C.EVENT_FUORIPORTA, data)

    def _handle_call_info(self, raw: str) -> None:
        import json
        data = {"sip_id": None, "reason": None, "media_type": None, "video_src": None}
        payload = self._split_json_payload(raw, 1)
        if payload:
            try:
                j = json.loads(payload)
                if isinstance(j, dict):
                    data["sip_id"] = j.get("SIP_ID") or j.get("sip_id")
                    data["reason"] = j.get("REASON") or j.get("reason")
                    data["media_type"] = j.get("MEDIA_TYPE") or j.get("media_type")
                    data["video_src"] = j.get("VIDEO_SRC") or j.get("video_src")
            except Exception:
                _LOGGER.debug("CALL_INFO payload non-JSON: %r", payload[:120])
        self.stats["last_call_info"] = data
        _LOGGER.info("Call info: %s", data)
        self._fire_event(C.EVENT_CALL_INFO, data)

    def _handle_new_phonebook(self, raw: str) -> None:
        # NEW_PHONEBOOK;<gid>;<ver>
        parts = raw.split(";")
        gid = parts[1].strip() if len(parts) > 1 else None
        ver = parts[2].strip() if len(parts) > 2 else None
        _LOGGER.info("NEW_PHONEBOOK gid=%s ver=%s", gid, ver)
        # Aggiorna rubrica_ver ed emette phonebook_changed (anche se primo valore,
        # NEW_PHONEBOOK è per definizione un cambio → forziamo l'evento).
        if ver is not None:
            old = self.stats.get("rubrica_ver")
            self.stats["rubrica_ver"] = str(ver)
            if old != str(ver):
                self._fire_event(
                    C.EVENT_PHONEBOOK_CHANGED, {"gid": gid or R.SIP_USER, "rubrica_ver": str(ver)}
                )
        else:
            self._fire_event(
                C.EVENT_PHONEBOOK_CHANGED, {"gid": gid or R.SIP_USER, "rubrica_ver": None}
            )

    async def _request_init_status(self):
        """Chiede lo stato iniziale al PICG (GET_INIT_STATUS, Panda: blue).

        La risposta arriva in modo asincrono come SIP MESSAGE
        (GET_INIT_STATUS_REPLY) → parsata in _handle_incoming_message.
        Funziona sia in UDP locale sia in cloud TLS.
        """
        try:
            ok, msg = await self.async_send_command(
                body=C.GET_INIT_STATUS,
                target=R.PICG_TARGET,
                header_name="Panda",
                header_value="blue",
            )
            self._init_status_sent = True
            _LOGGER.info("GET_INIT_STATUS → %s: ok=%s msg=%s", R.PICG_TARGET, ok, msg)
        except Exception:
            _LOGGER.exception("GET_INIT_STATUS invio fallito")

    async def _auto_startup(self):
        await asyncio.sleep(2)
        try:
            _LOGGER.info("Auto startup: registering SIP...")
            ok = await sip.do_register()
            _LOGGER.info("Auto startup: register result=%s", ok)
            if ok:
                self.stats["last_register_time"] = self._now()
            else:
                self.stats["register_failures"] += 1
            self._touch()
            if ok:
                # Stato iniziale (voicemail/dnd/rubrica_ver/vm_level) dal PICG.
                await self._request_init_status()
                # Identificazione del modello: passiva (header dei messaggi in
                # arrivo) + una sonda OPTIONS se non lo conosciamo ancora.
                self._tasks.append(asyncio.create_task(self._probe_model()))
            if ok and not R.USE_LOCAL_UDP:
                # connectProfiles è solo per la modalità cloud (push notifications)
                await asyncio.sleep(1)
                try:
                    ok2, msg2 = await sip.do_connect_profiles()
                    _LOGGER.info("connectProfiles: ok=%s msg=%s", ok2, msg2)
                except Exception as e:
                    _LOGGER.error("connectProfiles error: %s", e)
            elif not ok:
                _LOGGER.error("SIP registration failed")
        except Exception as e:
            _LOGGER.error("Auto startup error: %s", e, exc_info=True)

    async def _keepalive_loop(self):
        while self._running:
            await asyncio.sleep(120)
            if sip.registered:
                try:
                    ok = await sip.do_register()
                    _LOGGER.debug("Keepalive: %s", "OK" if ok else "FAILED")
                    if ok:
                        self.stats["last_register_time"] = self._now()
                        # Se lo stato iniziale non è mai stato ottenuto (primo
                        # invio fallito / reconnect dopo offline), riprova ora.
                        if not self._init_status_sent:
                            await self._request_init_status()
                    else:
                        self.stats["register_failures"] += 1
                    self._touch()
                except Exception as e:
                    _LOGGER.error("Keepalive error: %s", e)
