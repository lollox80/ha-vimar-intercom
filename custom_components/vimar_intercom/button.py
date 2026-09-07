"""Button platform for Vimar Intercom."""

from __future__ import annotations

import logging

import re

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import device_info
from . import runtime as R

_LOGGER = logging.getLogger(__name__)

# Mappatura icona logica → icona mdi per gli attuatori dinamici (parse_rubrica.py)
_ACTUATOR_ICONS = {
    "door":   "mdi:door",
    "light":  "mdi:lightbulb",
    "switch": "mdi:toggle-switch-variant",
}
# Target di default (targa/porta) usato dall'apri-porta quando target == "AUTO".
_AUTO_TARGET_SENTINEL = "AUTO"


def _slug(*parts: str) -> str:
    """Slug deterministico da name+msg per un unique_id stabile."""
    raw = "_".join(p for p in parts if p)
    return re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_") or "act"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]

    entities: list[ButtonEntity] = [
        VimarCallButton(hub, entry.entry_id),
        VimarCallTargetButton(hub, entry.entry_id, "55001", "Chiama Video (esterno)", "call_ext"),
        VimarCallTargetButton(hub, entry.entry_id, "55002", "Chiama Casa (interno)", "call_int"),
        VimarAnswerButton(hub, entry.entry_id),
        VimarHangupButton(hub, entry.entry_id),
        VimarDoorButton(hub, entry.entry_id, "55001", "Apri Porta", "door_street", "mdi:door-open"),
    ]

    # Attuatori dinamici dalla rubrica: options["actuators"] ha la precedenza
    # su entry.data. Lista di dict {name, msg, target, icon}. Default vuoto.
    actuators = (entry.options.get("actuators")
                 or entry.data.get("actuators") or [])
    for act in actuators:
        try:
            entities.append(VimarActuatorButton(hub, entry.entry_id, act))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Attuatore ignorato (config non valida): %s", act)

    async_add_entities(entities)


class VimarCallButton(ButtonEntity):
    """Button to call the intercom (initiate SIP INVITE)."""

    _attr_has_entity_name = False
    _attr_name = "Chiama"
    _attr_icon = "mdi:phone-outgoing"

    def __init__(self, hub, entry_id: str) -> None:
        self._hub = hub
        self._attr_unique_id = f"{entry_id}_call"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        ok, msg = await self._hub.async_call()
        if not ok:
            _LOGGER.error("Call failed: %s", msg)


class VimarCallTargetButton(ButtonEntity):
    """Button to call a specific SIP target (targa interna/esterna)."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:phone-outgoing"

    def __init__(self, hub, entry_id: str, target: str, name: str, key: str) -> None:
        self._hub = hub
        self._target = target
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        ok, msg = await self._hub.async_call(target=self._target)
        if not ok:
            _LOGGER.error("Call to %s failed: %s", self._target, msg)


class VimarAnswerButton(ButtonEntity):
    """Button to answer an incoming intercom call."""

    _attr_has_entity_name = False
    _attr_name = "Rispondi"
    _attr_icon = "mdi:phone-incoming"

    def __init__(self, hub, entry_id: str) -> None:
        self._hub = hub
        self._attr_unique_id = f"{entry_id}_answer"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        ok, msg = await self._hub.async_answer()
        if not ok:
            _LOGGER.error("Answer failed: %s", msg)


class VimarHangupButton(ButtonEntity):
    """Button to hang up the current call (SIP BYE)."""

    _attr_has_entity_name = False
    _attr_name = "Riaggancia"
    _attr_icon = "mdi:phone-hangup"

    def __init__(self, hub, entry_id: str) -> None:
        self._hub = hub
        self._attr_unique_id = f"{entry_id}_hangup"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        await self._hub.async_hangup()


class VimarDoorButton(ButtonEntity):
    """Button to open a door (SIP MESSAGE)."""

    _attr_has_entity_name = False

    def __init__(self, hub, entry_id: str, target: str | None, name: str, key: str, icon: str) -> None:
        self._hub = hub
        self._target = target
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        ok, msg = await self._hub.async_door(target=self._target)
        if not ok:
            _LOGGER.error("Door %s open failed: %s", self._target or "default", msg)


class VimarActuatorButton(ButtonEntity):
    """Attuatore dinamico via SIP MESSAGE (Panda: command).

    Configurato dall'utente nell'options flow (lista prodotta da
    tools/parse_rubrica.py): dict {name, msg, target, icon}.
    Riusa lo stesso meccanismo dell'apri-porta: hub.async_send_command con
    header ``Panda: command`` — non reimplementa nulla dello stack SIP.
    """

    _attr_has_entity_name = True

    def __init__(self, hub, entry_id: str, act: dict) -> None:
        self._hub = hub
        name = str(act["name"])
        self._command = str(act["msg"])
        target = str(act.get("target", _AUTO_TARGET_SENTINEL))
        # target "AUTO" → stesso destinatario di default dell'apri-porta
        # (la targa/porta usata da hub.async_door/servizio open_door, cioè
        # R.SGA_TARGET = R.INTERCOM/DOOR_ESTERNO senza lo schema sip:).
        # Così l'utente non deve conoscere l'id SIP della targa master, e il
        # valore segue eventuali override in options (manuali o da rubrica.db).
        self._target = R.SGA_TARGET if target.upper() == _AUTO_TARGET_SENTINEL else target
        self._attr_name = name
        self._attr_icon = _ACTUATOR_ICONS.get(act.get("icon", ""), "mdi:gesture-tap-button")
        slug = _slug(name, self._command)
        self._attr_unique_id = f"{entry_id}_act_{slug}"
        self._attr_device_info = device_info(entry_id)

    async def async_press(self) -> None:
        ok, msg = await self._hub.async_send_command(
            body=self._command, target=self._target,
            header_name="Panda", header_value="command")
        if not ok:
            _LOGGER.error("Attuatore %s (%s) fallito: %s",
                          self._attr_name, self._command, msg)
