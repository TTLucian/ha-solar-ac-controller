"""
Switch entity for enabling/disabling the Solar AC Controller integration.
"""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

INTEGRATION_ENABLE_SWITCH = "integration_enable"


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([IntegrationEnableSwitch(coordinator, entry)])


class IntegrationEnableSwitch(CoordinatorEntity, SwitchEntity):
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Solar AC Controller",
        }

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Integration Enable"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_integration_enable"

    @property
    def is_on(self):
        return getattr(self.coordinator, "integration_enabled", True)

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_set_integration_enabled(True)

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_set_integration_enabled(False)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
