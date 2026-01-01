from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION
from .coordinator import SolarACCoordinator

PLATFORMS: list[str] = ["sensor"]


async def async_setup(hass: HomeAssistant, config: dict):
    """YAML setup not supported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up Solar AC Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored = await store.async_load() or {}

    coordinator = SolarACCoordinator(hass, entry.data, store, stored)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "store": store,
    }

    # Forward to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ---- Services ----

    async def handle_reset_learning(call: ServiceCall):
        await coordinator.controller.reset_learning()

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    async def handle_force_relearn(call: ServiceCall):
        zone = call.data.get("zone")

        if zone:
            # Reset only one zone
            zone_name = zone.split(".")[-1]
            coordinator.learned_power[zone_name] = 1200
        else:
            # Reset all zones
            for z in coordinator.config["zones"]:
                zn = z.split(".")[-1]
                coordinator.learned_power[zn] = 1200

        coordinator.samples = 0
        await coordinator.controller._save()

    hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload integration."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.data[DOMAIN].pop(entry.entry_id, None)

    # Note: services are global under DOMAIN; if you have multiple entries,
    # you may want reference counting. For single-instance use, this is acceptable.

    return unload_ok
