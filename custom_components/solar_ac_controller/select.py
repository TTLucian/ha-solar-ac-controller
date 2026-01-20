"""
Select entity for manual season mode (heat/cool) for Solar AC Controller.
"""
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, CONF_SEASON_MODE

SEASON_OPTIONS = ["heat", "cool"]

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([SeasonModeSelect(coordinator, entry)])

class SeasonModeSelect(CoordinatorEntity, SelectEntity):
    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": "Solar AC Controller",
        }
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Season Mode"
    _attr_icon = "mdi:weather-partly-snowy-rainy"
    _attr_options = SEASON_OPTIONS

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_season_mode"

    @property
    def current_option(self):
        return getattr(self.coordinator, "season_mode", "cool")

    async def async_select_option(self, option: str):
        if option not in SEASON_OPTIONS:
            return
        self.coordinator.season_mode = option
        # Persist to config entry options
        options = dict(self.entry.options)
        options[CONF_SEASON_MODE] = option
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        self.coordinator.async_update_listeners()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
