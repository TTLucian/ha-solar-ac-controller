from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION
from .coordinator import SolarACCoordinator
from homeassistant.helpers.storage import Store


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Solar AC Controller from a config entry."""

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    coordinator = SolarACCoordinator(hass, entry.data, store, stored)

    # Register debug entity
    from .entity import SolarACDebugEntity
    async def _register_entity():
        entity = SolarACDebugEntity(coordinator)
        await hass.helpers.entity_platform.async_add_entities([entity])

    hass.async_create_task(_register_entity())

    # Start coordinator
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
