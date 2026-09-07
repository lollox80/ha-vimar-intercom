"""Switch platform for Vimar Intercom — Segreteria e Non disturbare.

Comandi SIP (MESSAGE, header Panda: blue, verso l'SGA = SYSTEM.MAGIC_APT_INTERCOM,
55001 sull'impianto di riferimento, ma configurabile via options — vedi
runtime.SGA_TARGET / const.SGA_TARGET) [VERIFICATO 19/08/2026]:
  Segreteria      → VOICEMAIL;ON / VOICEMAIL;OFF
  Non disturbare  → DND;ON / DND;OFF

Lo stato è REALE: il Tab annuncia i cambi via SIP MESSAGE (sia da UI locale
che da app), quindi lo switch riflette lo stato effettivo e non è ottimistico.
Il comando è confermato sul campo: `VOICEMAIL;ON` → 55001 accende la segreteria
sul Tab (i vecchi tentativi verso 55002 davano 200 senza effetto = target sbagliato).
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    DOMAIN,
    SEGRETERIA_ON, SEGRETERIA_OFF,
    SEGRETERIA_HEADER_NAME, SEGRETERIA_HEADER_VALUE,
    DND_ON, DND_OFF,
)
from .device import device_info
from . import runtime as R

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]
    # Target letto da runtime (SGA_TARGET): valore da options — manuale o
    # importato da rubrica.db — con fallback al default storico in const.py.
    # runtime.configure() è già stato chiamato da async_setup_entry() prima
    # di avviare le piattaforme, quindi il valore qui è già quello corrente.
    async_add_entities([
        VimarModeSwitch(
            hub, entry.entry_id, key="segreteria", name="Segreteria",
            icon="mdi:voicemail", target=R.SGA_TARGET,
            cmd_on=SEGRETERIA_ON, cmd_off=SEGRETERIA_OFF,
            state_attr="voicemail",
            hname=SEGRETERIA_HEADER_NAME, hvalue=SEGRETERIA_HEADER_VALUE),
        VimarModeSwitch(
            hub, entry.entry_id, key="dnd", name="Non Disturbare",
            icon="mdi:bell-off", target=R.SGA_TARGET,
            cmd_on=DND_ON, cmd_off=DND_OFF, state_attr="dnd",
            hname="Panda", hvalue="blue"),
    ])


class VimarModeSwitch(SwitchEntity, RestoreEntity):
    """Switch per una modalità del Tab (segreteria / non disturbare)."""

    _attr_has_entity_name = False

    def __init__(self, hub, entry_id: str, *, key: str, name: str, icon: str,
                 target: str, cmd_on: str, cmd_off: str, state_attr: str,
                 hname: str | None, hvalue: str | None) -> None:
        self._hub = hub
        self._key = key
        self._target = target
        self._cmd_on = cmd_on
        self._cmd_off = cmd_off
        self._state_attr = state_attr
        self._hname = hname
        self._hvalue = hvalue
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info(entry_id)
        self._is_on = False
        self._last_result: str | None = None

    def _real(self) -> bool | None:
        return self._hub.stats.get(self._state_attr)

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None:
            self._is_on = last.state == "on"
        self._hub.register_state_callback(self._on_state_change)

    async def async_will_remove_from_hass(self) -> None:
        self._hub.unregister_state_callback(self._on_state_change)

    @callback
    def _on_state_change(self) -> None:
        real = self._real()
        if real is not None:
            self._is_on = real
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        real = self._real()
        return real if real is not None else self._is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "target": self._target,
            "comando_on": self._cmd_on,
            "comando_off": self._cmd_off,
            "stato_reale": self._real(),
            "ultimo_esito": self._last_result,
            "nota": "Comando via SIP MESSAGE (Panda: blue) verso l'SGA; stato letto dagli annunci del Tab.",
        }

    async def async_turn_on(self, **kwargs) -> None:
        await self._send(self._cmd_on, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._send(self._cmd_off, False)

    async def _send(self, body: str, new_state: bool) -> None:
        ok, msg = await self._hub.async_send_command(
            body=body, target=self._target,
            header_name=self._hname or None, header_value=self._hvalue or None)
        self._last_result = msg
        if self._real() is None:
            self._is_on = new_state  # ottimistico solo finché non arriva l'annuncio
        _LOGGER.info("%s %s → ok=%s msg=%s", self._attr_name,
                     "ON" if new_state else "OFF", ok, msg)
        self.async_write_ha_state()
