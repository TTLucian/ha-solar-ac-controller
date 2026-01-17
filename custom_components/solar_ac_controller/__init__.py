from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .coordinator import SolarACCoordinator
from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_INITIAL_LEARNED_POWER,
    DEFAULT_INITIAL_LEARNED_POWER,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


async def _async_migrate_data(
    old_major: int,
    old_minor: int,
    old_data: dict | None,
    initial_lp: float = DEFAULT_INITIAL_LEARNED_POWER,
) -> dict:
    """
    Normalize and migrate stored data for Solar AC Controller.
    STORAGE_VERSION is incremented whenever the structure of the stored payload changes.
    Document migration changes here and in commit messages for future maintainers.
    """
    if not isinstance(old_data, dict):
        return {"learned_power": {}, "learned_power_bands": {}, "samples": 0}

    learned_power = old_data.get("learned_power", {})
    learned_power_bands = old_data.get("learned_power_bands", {}) or {}
    if not isinstance(learned_power, dict):
        learned_power = {}

    modified = False
    for zone, val in list(learned_power.items()):
        if val is None:
            learned_power[zone] = {"default": initial_lp, "heat": initial_lp, "cool": initial_lp}
            modified = True
        elif isinstance(val, (int, float)):
            v = float(val)
            learned_power[zone] = {"default": v, "heat": v, "cool": v}
            modified = True
        elif isinstance(val, dict):
            for mode in ["default", "heat", "cool"]:
                if mode not in val:
                    val[mode] = initial_lp
                    modified = True
    
    payload = {
        "learned_power": learned_power,
        "learned_power_bands": learned_power_bands if isinstance(learned_power_bands, dict) else {},
        "samples": old_data.get("samples", 0),
    }

    if modified:
        return payload
    return payload


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    # 1. Get Integration Version from manifest
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else None

    initial_lp = entry.options.get(
        CONF_INITIAL_LEARNED_POWER,
        entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER)
    )

    # 2. Storage Setup
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    
    async def migrate_fn(old_major, old_minor, old_data):
        return await _async_migrate_data(old_major, old_minor, old_data, initial_lp)

    try:
        old_data = await store.async_load()
        stored_data = await migrate_fn(STORAGE_VERSION, 0, old_data)
    except Exception:
        _LOGGER.exception("Failed to load stored data")
        stored_data = None

    if stored_data is None:
        stored_data = {"learned_power": {}, "learned_power_bands": {}, "samples": 0}

    # 3. Create Device (The "Master" record)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Solar AC Controller",
        configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
    )

    # 4. Initialize Coordinator
    coordinator = SolarACCoordinator(
        hass,
        entry,
        store,
        stored_data,
        version=version,
    )

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    entry.add_update_listener(async_reload_entry)

    # 5. Services
    async def handle_reset_learning(call: ServiceCall):
        controller = getattr(coordinator, "controller", None)
        if controller:
            await controller.reset_learning()

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    
    if not hass.data.get(DOMAIN):
        if hass.services.has_service(DOMAIN, "reset_learning"):
            hass.services.async_remove(DOMAIN, "reset_learning")
            
    return unload_ok
