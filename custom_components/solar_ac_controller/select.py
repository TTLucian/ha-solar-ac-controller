"""
Select entity for manual season mode (heat/cool) for Solar AC Controller.
"""

from functools import cached_property
from typing import TYPE_CHECKING, cast

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SolarACData

if TYPE_CHECKING:
    from .coordinator import SolarACCoordinator

SEASON_OPTIONS = ["heat", "cool"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    domain_data: SolarACData = hass.data[DOMAIN]
    coordinator = domain_data[entry.entry_id]["coordinator"]
    async_add_entities([SeasonModeSelect(coordinator, entry)])


class SeasonModeSelect(  # pyright: ignore[reportIncompatibleVariableOverride]
    CoordinatorEntity, SelectEntity
):
    coordinator: "SolarACCoordinator"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll: bool = False
    _attr_has_entity_name: bool = True
    _attr_name = "Season Mode"
    _attr_icon = "mdi:weather-partly-snowy-rainy"
    _attr_options: list[str] = SEASON_OPTIONS

    @cached_property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name="Solar AC Controller",
        )

    def __init__(self, coordinator: "SolarACCoordinator", entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_season_mode"

    @property
    def current_option(  # pyright: ignore[reportIncompatibleVariableOverride]
        self,
    ) -> str | None:
        return cast(str, getattr(self.coordinator, "season_mode", "cool"))

    async def async_select_option(self, option: str) -> None:
        if option not in SEASON_OPTIONS:
            return
        await self.coordinator.async_set_season_mode(option)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
