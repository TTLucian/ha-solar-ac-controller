from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration
from homeassistant.helpers.typing import ConfigType

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
    """Normalize and migrate stored data into the new per‑mode structure."""
    _LOGGER.info(
        "Running explicit fallback migration from v%s.%s to v%s",
        old_major,
        old_minor,
        STORAGE_VERSION,
    )

    if not isinstance(old_data, dict):
        return {"learned_power": {}, "samples": 0}

    learned_power = old_data.get("learned_power", {})
    if not isinstance(learned_power, dict):
        learned_power = {}

    modified = False

    for zone, val in list(learned_power.items()):
        if val is None:
            learned_power[zone] = {
                "default": initial_lp,
                "heat": initial_lp,
                "cool": initial_lp,
            }
            modified = True

        elif isinstance(val, (int, float)):
            v = float(val)
            learned_power[zone] = {"default": v, "heat": v, "cool": v}
            modified = True

        elif isinstance(val, dict):
            zone_modified = False
            default = val.get("default")

            for mode in ("default", "heat", "cool"):
                if val.get(mode) is None:
                    val[mode] = default if default is not None else initial_lp
                    zone_modified = True

            if zone_modified:
                learned_power[zone] = val
                modified = True

        else:
            learned_power[zone] = {
                "default": initial_lp,
                "heat": initial_lp,
                "cool": initial_lp,
            }
            modified = True

    if modified:
        old_data["learned_power"] = learned_power
        try:
            old_data["samples"] = int(old_data.get("samples", 0) or 0)
        except Exception:
            old_data["samples"] = 0

    return old_data


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Basic setup. No async_migrate available on this HA version."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar AC Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Defensive: async_get_integration may rarely return None; fall back to "unknown"
    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version if integration is not None else "unknown"
    if integration is None:
        _LOGGER.debug("async_get_integration returned None for %s; using fallback version=%s", DOMAIN, version)

    initial_lp = float(
        entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
        )
    )

    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    try:
        stored_data = await store.async_load()
    except Exception:
        _LOGGER.exception("Critical error loading integration storage")
        stored_data = None

    # Explicit fallback migration
    try:
        migrated = await _async_migrate_data(0, 0, stored_data, initial_lp)

        if migrated != stored_data:
            _LOGGER.debug("Data migration changed payload; saving to storage")
            await store.async_save(migrated)
            stored_data = migrated
            _LOGGER.info("Storage migrated and saved for %s", STORAGE_KEY)

    except Exception:
        _LOGGER.exception("Error while attempting explicit migration fallback")
        if stored_data is None:
            stored_data = {"learned_power": {}, "samples": 0}

    coordinator = SolarACCoordinator(
        hass,
        entry,
        store,
        stored_data,
        version=version,
    )

    # Make coordinator available immediately so any code invoked during first refresh can access it
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    _LOGGER.debug("SolarACCoordinator stored in hass.data for entry %s (version=%s)", entry.entry_id, version)

    # Perform initial refresh (may trigger platform setup callbacks)
    await coordinator.async_config_entry_first_refresh()

    # Device registry entry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "solar_ac_controller")},
        name="Solar AC Controller",
        manufacturer="TTLucian",
        model="Solar AC Smart Controller",
        sw_version=version,
    )

    # Platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.add_update_listener(async_reload_entry)

    # Services
    async def handle_reset_learning(call: ServiceCall):
        if hasattr(coordinator, "controller"):
            await coordinator.controller.reset_learning()

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
