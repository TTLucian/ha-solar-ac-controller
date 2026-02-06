from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_AGGRESSIVENESS, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: Any
) -> None:
    domain_data = hass.data[DOMAIN]
    coordinator = domain_data[entry.entry_id]["coordinator"]
    async_add_entities([AggressivenessNumber(coordinator, entry)])


class AggressivenessNumber(CoordinatorEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator: Any, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = "Aggressiveness"
        self._attr_unique_id = f"{entry.entry_id}_aggressiveness"

    @property
    def native_value(self) -> float:
        return float(
            getattr(self.coordinator, "aggressiveness", DEFAULT_AGGRESSIVENESS)
        )

    @property
    def native_min_value(self) -> float:
        return 0.0

    @property
    def native_max_value(self) -> float:
        return 1.0

    @property
    def native_step(self) -> float:
        return 0.01

    async def async_set_native_value(self, value: float) -> None:
        # Delegate to coordinator which persists option and notifies listeners
        await self.coordinator.async_set_aggressiveness(value)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
