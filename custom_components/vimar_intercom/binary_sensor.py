"""Binary sensor platform for Vimar Intercom."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub = hass.data[DOMAIN][entry.entry_id]["hub"]
    async_add_entities([
        VimarSIPRegistrationSensor(hub, entry.entry_id),
        VimarInCallSensor(hub, entry.entry_id),
        VimarRingingSensor(hub, entry.entry_id),
        VimarCallingSensor(hub, entry.entry_id),
        VimarNewVideomessageSensor(hub, entry.entry_id),
    ])


class VimarSIPRegistrationSensor(BinarySensorEntity):
    """Shows whether SIP registration is active."""

    _attr_has_entity_name = False
    _attr_name = "Intercom SIP"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:lan-connect"

    def __init__(self, hub, entry_id: str) -> None:
        self._hub = hub
        self._attr_unique_id = f"{entry_id}_sip_registered"
        self._attr_device_info = device_info(entry_id)

    @property
    def is_on(self) -> bool:
        return self._hub.registered

    async def async_added_to_hass(self) -> None:
        self._hub.register_state_callback(self._on_state_change)

    async def async_will_remove_from_hass(self) -> None:
        self._hub.unregister_state_callback(self._on_state_change)

    @callback
    def _on_state_change(self) -> None:
        self.async_write_ha_state()


class VimarInCallSensor(BinarySensorEntity):
    """Shows whether there is an active SIP call."""

    _attr_has_entity_name = False
    _attr_name = "Intercom In Call"
    _attr_icon = "mdi:phone-in-talk"

    def __init__(self, hub, entry_id: str) -> None:
        self._hub = hub
        self._attr_unique_id = f"{entry_id}_in_call"
        self._attr_device_info = device_info(entry_id)

    @property
    def is_on(self) -> bool:
        return self._hub.in_call

    async def async_added_to_hass(self) -> None:
        self._hub.register_state_callback(self._on_state_change)

    async def async_will_remove_from_hass(self) -> None:
        self._hub.unregister_state_callback(self._on_state_change)

    @callback
    def _on_state_change(self) -> None:
        self.async_write_ha_state()


class _VimarHubBinarySensor(BinarySensorEntity):
    """Base: binary sensor agganciato ai callback di stato dell'hub."""

    _attr_has_entity_name = False

    def __init__(self, hub, entry_id: str, key: str, name: str, icon: str) -> None:
        self._hub = hub
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info(entry_id)

    async def async_added_to_hass(self) -> None:
        self._hub.register_state_callback(self._on_state_change)

    async def async_will_remove_from_hass(self) -> None:
        self._hub.unregister_state_callback(self._on_state_change)

    @callback
    def _on_state_change(self) -> None:
        self.async_write_ha_state()


class VimarRingingSensor(_VimarHubBinarySensor):
    """ON mentre il citofono sta squillando (INVITE in arrivo non ancora gestito)."""

    def __init__(self, hub, entry_id: str) -> None:
        super().__init__(hub, entry_id, "ringing", "Intercom Squillo", "mdi:bell-ring")

    @property
    def is_on(self) -> bool:
        return self._hub.is_ringing

    @property
    def extra_state_attributes(self) -> dict:
        st = self._hub.stats
        from .hub import sip_id_name
        return {
            "chiamante": sip_id_name(st.get("last_caller_id")),
            "chiamante_id": st.get("last_caller_id"),
        }


class VimarCallingSensor(_VimarHubBinarySensor):
    """ON mentre HA sta chiamando (INVITE in uscita non ancora connesso)."""

    def __init__(self, hub, entry_id: str) -> None:
        super().__init__(hub, entry_id, "calling", "Intercom Chiamata In Uscita", "mdi:phone-outgoing")

    @property
    def is_on(self) -> bool:
        return self._hub.calling


class VimarNewVideomessageSensor(_VimarHubBinarySensor):
    """ON quando il Tab annuncia un nuovo videomessaggio (VM;VIDEO_MESSAGE_CHANGE;NEW)."""

    def __init__(self, hub, entry_id: str) -> None:
        super().__init__(hub, entry_id, "new_videomessage",
                         "Intercom Nuovo Videomessaggio", "mdi:message-video")

    @property
    def is_on(self) -> bool:
        return bool(self._hub.stats.get("new_videomessage"))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "ultimo_cambio": self._hub.stats.get("last_videomessage"),
            "spazio": self._hub.stats.get("vm_level"),
        }
