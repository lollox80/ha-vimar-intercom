"""Vimar Intercom integration for Home Assistant."""

import asyncio
import ipaddress
import json
import logging

from aiohttp import web

import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .hub import VimarIntercomHub
from . import media_handler as media
from . import push_sender
from . import sip_client as sip
from . import runtime

_LOGGER = logging.getLogger(__name__)

# Ring buffer for debug logs
_debug_log: list[str] = []
_MAX_DEBUG_LOG = 200


class _DebugHandler(logging.Handler):
    """Captures vimar_intercom logs into a ring buffer."""
    def emit(self, record):
        try:
            msg = self.format(record)
            _debug_log.append(msg)
            if len(_debug_log) > _MAX_DEBUG_LOG:
                del _debug_log[:len(_debug_log) - _MAX_DEBUG_LOG]
        except Exception:
            pass


# Attach debug handler to all vimar loggers
_dh = _DebugHandler()
_dh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
_vlog = logging.getLogger("custom_components.vimar_intercom")
_vlog.addHandler(_dh)
_vlog.setLevel(logging.DEBUG)  # serve al buffer interno di debug (_debug_log)
_vlog.propagate = False  # non propagare i DEBUG al logger root/HA (vedi CHANGELOG/patch logging)


class _ForwardToRootHandler(logging.Handler):
    """Inoltra al log di Home Assistant solo WARNING+ (i DEBUG restano nel buffer interno)."""

    def emit(self, record):
        logging.getLogger().handle(record)


_fwd = _ForwardToRootHandler(level=logging.WARNING)
_vlog.addHandler(_fwd)

PLATFORMS = ["camera", "lock", "button", "event", "binary_sensor", "sensor", "switch"]

# ─── Servizi ──────────────────────────────────────────────────────────────────
SERVICE_SEND_COMMAND = "send_command"
SERVICE_CALL = "call"
SERVICE_ANSWER = "answer"
SERVICE_HANGUP = "hangup"
SERVICE_OPEN_DOOR = "open_door"
SERVICE_FETCH_LOCAL = "fetch_local"

def _default_sga_target() -> str:
    """Default dinamico per i servizi: valore SGA correntemente configurato
    (options manuali o importer rubrica.db), non più fisso a 55001."""
    return runtime.SGA_TARGET or "55001"


SEND_COMMAND_SCHEMA = vol.Schema({
    vol.Required("body"): cv.string,
    vol.Optional("target", default=_default_sga_target): cv.string,
    vol.Optional("header_name", default="Panda"): cv.string,
    vol.Optional("header_value", default="command"): cv.string,
})
CALL_SCHEMA = vol.Schema({vol.Optional("target"): cv.string})
FETCH_LOCAL_SCHEMA = vol.Schema({
    vol.Required("path"): cv.string,                    # es. rest/get_info.php?action=status
    vol.Optional("save_as"): cv.string,                 # nome file in /config (opzionale)
    vol.Optional("host"): cv.string,                    # default: local_proxy
    vol.Optional("scheme", default="http"): cv.string,
})
OPEN_DOOR_SCHEMA = vol.Schema({
    vol.Optional("target", default=_default_sga_target): cv.string,
    vol.Optional("command", default="OPEN_2F"): cv.string,
})

# Active audio WebSocket clients
_audio_ws_clients: set[web.WebSocketResponse] = set()
_hub_ref: VimarIntercomHub | None = None


async def _ws_send_bytes_to_clients(data: bytes):
    """Send binary audio data to all connected iOS/web audio clients."""
    dead = set()
    for ws in _audio_ws_clients:
        try:
            await ws.send_bytes(data)
        except Exception:
            dead.add(ws)
    _audio_ws_clients.difference_update(dead)


async def _broadcast_text(data: dict):
    """Send JSON text message to all audio WS clients."""
    text = json.dumps(data)
    dead = set()
    for ws in _audio_ws_clients:
        try:
            await ws.send_str(text)
        except Exception:
            dead.add(ws)
    _audio_ws_clients.difference_update(dead)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vimar Intercom from a config entry."""
    global _hub_ref

    # Popola il modulo runtime con i dati del config entry.
    # Le options (impostazioni rete modificate da OptionsFlow) sovrascrivono
    # i valori di default presenti in entry.data.
    runtime.configure({**entry.data, **entry.options})
    _LOGGER.info(
        "Runtime configurato: user=%s domain=%s local_proxy=%s udp=%s",
        runtime.SIP_USER, runtime.SIP_DOMAIN,
        runtime.LOCAL_PROXY, entry.data.get("use_local_udp", True),
    )

    hub = VimarIntercomHub()
    _hub_ref = hub

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {"hub": hub}

    @callback
    def _on_model_detected(model: str, fw: str, ua: str, priority: int) -> None:
        """Propaga al device registry il modello rilevato via SIP."""
        registry = dr.async_get(hass)
        device = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        if device:
            updates: dict[str, str] = {}
            if device.model != model:
                updates["model"] = model
            if fw and device.sw_version != fw:
                updates["sw_version"] = fw
            if updates:
                registry.async_update_device(device.id, **updates)
                _LOGGER.info("Device registry aggiornato: %s", updates)

        # Persisti nel config entry: al prossimo avvio il modello è noto subito
        stored = {
            "detected_model":    model,
            "detected_fw":       fw,
            "detected_ua":       ua,
            "detected_priority": priority,
        }
        if any(entry.data.get(k) != v for k, v in stored.items()):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, **stored})

    hub.register_model_callback(_on_model_detected)

    @callback
    def _on_hub_event(event_type: str, data: dict) -> None:
        """Propaga gli eventi in ingresso del citofono sul bus di HA.

        event_type è già uno dei vimar_intercom_* di const.EVENT_*.
        Payload documentato in README §Eventi.
        """
        try:
            hass.bus.async_fire(event_type, data or {})
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Fire bus event %s failed", event_type)

    hub.register_event_callback(_on_hub_event)

    await hub.async_start()

    # Wire up audio broadcast to WebSocket clients
    media.ws_send_bytes = _ws_send_bytes_to_clients
    hub.set_ws_broadcast(_broadcast_text)
    hub._has_ws_clients = lambda: len(_audio_ws_clients) > 0

    # Initialize APNs VoIP push sender
    from .const import APNS_KEY_PATH, APNS_KEY_ID, APNS_TEAM_ID, APNS_BUNDLE_ID, APNS_SANDBOX
    if APNS_KEY_ID and APNS_TEAM_ID:
        push_sender.init(APNS_KEY_PATH, APNS_KEY_ID, APNS_TEAM_ID, APNS_BUNDLE_ID, APNS_SANDBOX)
        _LOGGER.info("APNs VoIP push sender initialized")
    else:
        _LOGGER.warning("APNs push not configured — set APNS_KEY_ID and APNS_TEAM_ID in const.py")

    _register_services(hass)

    hass.http.register_view(VimarMjpegView(hub))
    hass.http.register_view(VimarAVStreamView(hub))
    hass.http.register_view(VimarAudioWSView(hub))
    hass.http.register_view(VimarPushTokenView())
    hass.http.register_view(VimarDebugView())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Ricarica l'entry quando cambiano le options (es. lista attuatori):
    # così i bottoni dinamici vengono ricreati con la nuova configurazione.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ricarica l'integrazione al salvataggio delle options."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    global _hub_ref
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["hub"].async_stop()
        _hub_ref = None
        if not hass.data[DOMAIN]:
            for svc in (SERVICE_SEND_COMMAND, SERVICE_CALL, SERVICE_ANSWER,
                        SERVICE_HANGUP, SERVICE_OPEN_DOOR, SERVICE_FETCH_LOCAL):
                hass.services.async_remove(DOMAIN, svc)
    return ok


def _is_local_request(request) -> bool:
    """True se la richiesta arriva da rete locale/loopback.

    Blocca l'accesso agli stream video da Internet (es. remote UI / port
    forwarding). I consumatori legittimi (camera HA, HomeKit) girano sull'host
    HA stesso, quindi vedono IP loopback o privato.
    """
    peer = getattr(request, "remote", None)
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _get_hub() -> VimarIntercomHub:
    if _hub_ref is None:
        raise RuntimeError("Vimar Intercom non inizializzato")
    return _hub_ref


def _register_services(hass: HomeAssistant) -> None:
    """Registra i servizi vimar_intercom.* (una sola volta)."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        return

    async def _svc_send_command(call: ServiceCall):
        hub = _get_hub()
        ok, msg = await hub.async_send_command(
            body=call.data["body"],
            target=call.data.get("target", "55001"),
            header_name=call.data.get("header_name") or None,
            header_value=call.data.get("header_value") or None,
        )
        _LOGGER.info("Service send_command → ok=%s msg=%s", ok, msg)
        return {"ok": ok, "result": msg}

    async def _svc_call(call: ServiceCall):
        hub = _get_hub()
        ok, msg = await hub.async_call(target=call.data.get("target"))
        return {"ok": ok, "result": msg}

    async def _svc_answer(call: ServiceCall):
        hub = _get_hub()
        ok, msg = await hub.async_answer()
        return {"ok": ok, "result": msg}

    async def _svc_hangup(call: ServiceCall):
        hub = _get_hub()
        await hub.async_hangup()
        return {"ok": True, "result": "Chiamata terminata"}

    async def _svc_fetch_local(call: ServiceCall):
        """GET HTTP (Digest sipID/password) verso l'interfaccia locale del citofono.

        Replica ciò che fa l'app in "home mode": /rest/get_info.php?action=status|nickname,
        /rest/get_file.php?name=rubrica|mailbox. Restituisce stato+anteprima e può salvare il file.
        """
        import requests as _rq
        from requests.auth import HTTPDigestAuth
        host = call.data.get("host") or runtime.LOCAL_PROXY
        url = f"{call.data.get('scheme','http')}://{host}/{call.data['path'].lstrip('/')}"
        save_as = call.data.get("save_as")

        def _do():
            r = _rq.get(url, auth=HTTPDigestAuth(runtime.SIP_USER, runtime.SIP_PASSWORD),
                        timeout=15, headers={"User-Agent": "TOGA/2.4.0"})
            data = r.content
            saved = None
            if save_as:
                dst = hass.config.path(save_as)
                with open(dst, "wb") as f:
                    f.write(data)
                saved = dst
            return r.status_code, dict(r.headers), data, saved

        try:
            code, hdrs, data, saved = await hass.async_add_executor_job(_do)
        except Exception as e:  # noqa: BLE001
            _LOGGER.error("fetch_local %s failed: %s", url, e)
            return {"ok": False, "url": url, "error": str(e)}
        preview = data[:600].decode("utf-8", errors="replace")
        _LOGGER.info("fetch_local %s → %s (%d bytes) saved=%s", url, code, len(data), saved)
        return {"ok": 200 <= code < 300, "url": url, "status": code,
                "content_type": hdrs.get("Content-Type"), "size": len(data),
                "saved": saved, "preview": preview}

    async def _svc_open_door(call: ServiceCall):
        hub = _get_hub()
        ok, msg = await hub.async_door(
            target=call.data.get("target"), command=call.data.get("command"))
        return {"ok": ok, "result": msg}

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_COMMAND, _svc_send_command,
        schema=SEND_COMMAND_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_CALL, _svc_call,
        schema=CALL_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_ANSWER, _svc_answer,
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_HANGUP, _svc_hangup,
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_OPEN_DOOR, _svc_open_door,
        schema=OPEN_DOOR_SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_FETCH_LOCAL, _svc_fetch_local,
        schema=FETCH_LOCAL_SCHEMA, supports_response=SupportsResponse.OPTIONAL)


class VimarAudioWSView(HomeAssistantView):
    """WebSocket endpoint for bidirectional audio + intercom control.

    Binary messages:
      Server → Client: 0x01 + PCM16LE (intercom audio, 8kHz mono)
      Client → Server: 0x02 + PCM16LE (mic audio, 8kHz mono)

    Text messages (JSON):
      Client → Server: {"action": "call"|"hangup"|"door"|"register"|"status"}
      Server → Client: {"type": "state"|"call_started"|"call_ended"|"ring"|"door"|"error", ...}
    """

    url = "/api/vimar_intercom/audio_ws"
    name = "api:vimar_intercom:audio_ws"
    # HARDENING: richiede autenticazione HA. Le azioni di controllo (door, call,
    # ecc.) non sono più raggiungibili senza un token valido.
    requires_auth = True

    def __init__(self, hub: VimarIntercomHub):
        self._hub = hub

    async def get(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        _audio_ws_clients.add(ws)
        _LOGGER.info("Audio WS client connected (%d total)", len(_audio_ws_clients))

        # Send initial state
        await ws.send_str(json.dumps({
            "type": "state",
            "registered": self._hub.registered,
            "in_call": self._hub.in_call,
        }))

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await self._handle_text(ws, msg.data)
                elif msg.type == web.WSMsgType.BINARY:
                    # Client sending mic audio: 0x02 prefix + PCM16LE
                    if len(msg.data) > 1 and msg.data[0] == 0x02 and self._hub.in_call:
                        media.send_audio(msg.data[1:])
                elif msg.type in (web.WSMsgType.ERROR, web.WSMsgType.CLOSE):
                    break
        except Exception as e:
            _LOGGER.error("Audio WS error: %s", e)
        finally:
            _audio_ws_clients.discard(ws)
            _LOGGER.info("Audio WS client disconnected (%d remaining)", len(_audio_ws_clients))

        return ws

    async def _handle_text(self, ws: web.WebSocketResponse, text: str):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return

        action = data.get("action")
        _LOGGER.info("WS action received: %s (data=%s)", action, data)
        hub = self._hub

        if action == "status":
            await ws.send_str(json.dumps({
                "type": "state",
                "registered": hub.registered,
                "in_call": hub.in_call,
            }))

        elif action == "call":
            target = data.get("target")  # optional: "55002" etc.
            try:
                ok, m = await hub.async_call(target=target)
                if ok:
                    await _broadcast_text({"type": "call_started", "msg": m,
                                           "target": target,
                                           "registered": hub.registered, "in_call": True})
                elif sip.in_call:
                    # Already connected — tell the app immediately
                    _LOGGER.info("Call request: already in call, notifying client")
                    await _broadcast_text({"type": "call_started", "msg": "Already in call",
                                           "target": target,
                                           "registered": hub.registered, "in_call": True})
                elif sip.calling:
                    # Call in progress (connecting) — SIP broadcast will notify when connected
                    _LOGGER.info("Call request: already calling, will notify on connect")
                else:
                    await ws.send_str(json.dumps({"type": "error", "msg": m}))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "hangup":
            try:
                await hub.async_hangup()
                await _broadcast_text({"type": "call_ended", "msg": "Call ended",
                                       "registered": hub.registered, "in_call": False})
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "switch":
            # Atomic panel switch: BYE current + INVITE new (like official app)
            target = data.get("target")
            if not target:
                await ws.send_str(json.dumps({"type": "error", "msg": "No target"}))
            else:
                try:
                    # Suppress broadcast during switch — do hangup silently
                    sip._suppress_broadcast = True
                    await hub.async_hangup()
                    sip._suppress_broadcast = False
                    await asyncio.sleep(0.05)  # Minimal — just enough for BYE to send
                    ok, m = await hub.async_call(target=target)
                    if ok:
                        await _broadcast_text({"type": "call_started", "msg": m,
                                               "target": target,
                                               "registered": hub.registered, "in_call": True})
                    else:
                        await _broadcast_text({"type": "call_ended", "msg": f"Switch failed: {m}",
                                               "registered": hub.registered, "in_call": False})
                except Exception as e:
                    sip._suppress_broadcast = False
                    await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "door":
            target = data.get("target")  # "55001" (esterno) or "55002" (interno)
            _LOGGER.info("Door action: target=%s", target)
            try:
                ok, m = await hub.async_door(target=target)
                t = "door" if ok else "error"
                await _broadcast_text({"type": t, "msg": m})
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "command":
            # Comando SIP MESSAGE arbitrario: {"action":"command","body":"...","target":"55001"}
            try:
                ok, m = await hub.async_send_command(
                    body=data.get("body", ""),
                    target=data.get("target", "55001"),
                    header_name=data.get("header_name", "Panda"),
                    header_value=data.get("header_value", "command"),
                )
                await ws.send_str(json.dumps({"type": "command_result", "ok": ok, "msg": m,
                                              "body": data.get("body")}))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "register":
            try:
                ok = await sip.do_register()
                if ok:
                    await _broadcast_text({"type": "registered", "msg": "SIP registered",
                                           "registered": True, "in_call": hub.in_call})
                else:
                    await ws.send_str(json.dumps({"type": "error", "msg": "Registration failed"}))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "probe":
            target = data.get("target", "")
            try:
                ok, m = await hub.async_probe(target)
                await ws.send_str(json.dumps({"type": "probe_result",
                                               "target": target, "ok": ok, "msg": m}))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "scan":
            start = data.get("start", 55001)
            end = data.get("end", 55020)
            try:
                results = await hub.async_scan(start, end)
                await ws.send_str(json.dumps({"type": "scan_result", "results": results}))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "answer":
            try:
                ok, m = await hub.async_answer()
                if ok:
                    # Broadcast ring_ended FIRST so other devices stop ringing
                    await _broadcast_text({"type": "ring_ended", "msg": "Answered on another device",
                                           "registered": hub.registered, "in_call": True})
                    await _broadcast_text({"type": "call_started", "msg": m,
                                           "registered": hub.registered, "in_call": True})
                else:
                    await ws.send_str(json.dumps({"type": "error", "msg": m}))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "decline":
            try:
                await hub.async_decline()
                await _broadcast_text({"type": "ring_ended", "msg": "Declined",
                                       "registered": hub.registered, "in_call": False})
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))

        elif action == "reconnect":
            _LOGGER.info("Force reconnect requested via WS")
            try:
                ok = await sip.reconnect()
                await ws.send_str(json.dumps({
                    "type": "state",
                    "registered": hub.registered,
                    "in_call": hub.in_call,
                    "msg": "Reconnected" if ok else "Reconnect failed",
                }))
            except Exception as e:
                await ws.send_str(json.dumps({"type": "error", "msg": str(e)}))


class VimarPushTokenView(HomeAssistantView):
    """REST endpoint for iOS app to register/unregister VoIP push tokens."""

    url = "/api/vimar_intercom/push_token"
    name = "api:vimar_intercom:push_token"
    requires_auth = True

    async def post(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        token = data.get("token")
        if not token:
            return web.json_response({"error": "Missing token"}, status=400)

        sender = push_sender.get_sender()
        if not sender:
            return web.json_response({"error": "Push not configured"}, status=503)

        device_name = data.get("device_name", "unknown")
        sender.register_token(token, device_name)
        return web.json_response({"status": "ok", "devices": len(sender.registered_devices)})

    async def delete(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        token = data.get("token")
        if not token:
            return web.json_response({"error": "Missing token"}, status=400)

        sender = push_sender.get_sender()
        if sender:
            sender.unregister_token(token)
        return web.json_response({"status": "ok"})


class VimarDebugView(HomeAssistantView):
    """Debug endpoint — returns recent vimar_intercom logs as plain text."""

    url = "/api/vimar_intercom/debug"
    name = "api:vimar_intercom:debug"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        try:
            n = int(request.query.get("lines", "100"))
        except ValueError:
            n = 100
        text = "\n".join(_debug_log[-n:])
        return web.Response(text=text, content_type="text/plain")


class VimarMjpegView(HomeAssistantView):
    """Serve MJPEG stream at /api/vimar_intercom/video."""

    url = "/api/vimar_intercom/video"
    name = "api:vimar_intercom:video"
    requires_auth = False

    def __init__(self, hub: VimarIntercomHub):
        self._hub = hub

    async def get(self, request: web.Request) -> web.StreamResponse:
        if not _is_local_request(request):
            return web.Response(status=403, text="Forbidden (local network only)")
        target = request.query.get("target")
        await self._hub.stream_opened(target=target)

        response = web.StreamResponse()
        response.content_type = "multipart/x-mixed-replace; boundary=frame"
        await response.prepare(request)
        try:
            waited = 0
            while not self._hub.video_frame and waited < 25:
                await asyncio.sleep(0.5)
                waited += 0.5

            while True:
                frame = self._hub.video_frame
                if frame:
                    await response.write(
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n"
                        + frame + b"\r\n"
                    )
                await asyncio.sleep(0.04)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            await self._hub.stream_closed()
        return response


class VimarAVStreamView(HomeAssistantView):
    """Serve MPEG-TS stream (H264 video + PCMU audio) at /api/vimar_intercom/av."""

    url = "/api/vimar_intercom/av"
    name = "api:vimar_intercom:av"
    requires_auth = False

    def __init__(self, hub: VimarIntercomHub):
        self._hub = hub

    async def get(self, request: web.Request) -> web.StreamResponse:
        if not _is_local_request(request):
            return web.Response(status=403, text="Forbidden (local network only)")
        _LOGGER.info("AV stream requested — triggering auto-call")
        await self._hub.stream_opened()

        waited = 0
        while not self._hub.in_call and waited < 15:
            await asyncio.sleep(0.5)
            waited += 0.5

        if not self._hub.in_call:
            _LOGGER.warning("AV stream: call not established after 15s")
            await self._hub.stream_closed()
            return web.Response(status=503, text="Call not established")

        await media.start_av_ffmpeg()
        if not media.av_ffmpeg_proc:
            await self._hub.stream_closed()
            return web.Response(status=503, text="ffmpeg failed to start")

        response = web.StreamResponse()
        response.content_type = "video/mp2t"
        await response.prepare(request)

        loop = asyncio.get_event_loop()
        try:
            while media.av_ffmpeg_proc and media.av_ffmpeg_proc.poll() is None:
                chunk = await loop.run_in_executor(
                    None, media.av_ffmpeg_proc.stdout.read, 4096)
                if not chunk:
                    break
                await response.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            await media.stop_av_ffmpeg()
            await self._hub.stream_closed()
        return response
