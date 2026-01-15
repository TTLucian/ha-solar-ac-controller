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
    """Normalize and migrate stored data into the new per-mode structure.

    Always returns a dict with 'learned_power' and 'samples' keys.
    """
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
    """Set up Solar AC Controller from a config entry.

    Defensive and robust setup:
    - Normalize stored data
    - Create coordinator and expose it immediately
    - Forward platforms early
    - Perform initial refresh without blocking setup on transient errors
    - Create device registry entry safely (avoid awesomeversion comparison issues)
    - Register service and track registration for clean unload
    """
    hass.data.setdefault(DOMAIN, {})

    # Resolve integration version defensively
    try:
        integration = await async_get_integration(hass, DOMAIN)
    except Exception:
        integration = None
        _LOGGER.debug("async_get_integration raised an exception for %s", DOMAIN)

    # Convert integration.version to a plain string if possible; otherwise None
    version: str | None
    if integration is not None and getattr(integration, "version", None) is not None:
        try:
            version = str(integration.version)
        except Exception:
            version = None
            _LOGGER.debug("Failed to stringify integration.version; using None for sw_version")
    else:
        version = None

    initial_lp = float(
        entry.options.get(
            CONF_INITIAL_LEARNED_POWER,
            entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
        )
    )

    # Persistent store for learned values
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)

    try:
        stored_data = await store.async_load()
    except Exception:
        _LOGGER.exception("Critical error loading integration storage")
        stored_data = None

    # Run explicit fallback migration
    try:
        migrated = await _async_migrate_data(0, 0, stored_data, initial_lp)

        if migrated != stored_data:
            _LOGGER.debug("Data migration changed payload; saving to storage")
            try:
                await store.async_save(migrated)
                stored_data = migrated
                _LOGGER.info("Storage migrated and saved for %s", STORAGE_KEY)
            except Exception:
                _LOGGER.exception("Failed to save migrated storage payload")
    except Exception:
        _LOGGER.exception("Error while attempting explicit migration fallback")
        if stored_data is None:
            stored_data = {"learned_power": {}, "samples": 0}

    # Instantiate coordinator
    coordinator = SolarACCoordinator(
        hass,
        entry,
        store,
        stored_data,
        version=version,
    )

    # Expose coordinator immediately
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}
    _LOGGER.debug(
        "SolarACCoordinator stored in hass.data for entry %s (version=%s)",
        entry.entry_id,
        version,
    )

    # Forward platforms early so entities appear quickly
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Perform initial refresh but do not let failures block setup
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        _LOGGER.exception("Initial coordinator refresh failed: %s", exc)
        # Continue setup; platforms will update when coordinator recovers.

    # Device registry: create device without sw_version, then update sw_version safely
    device_registry = dr.async_get(hass)
    try:
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, entry.entry_id)},
            name="Solar AC Controller",
            manufacturer="TTLucian",
            model="Solar AC Smart Controller",
        )
        if version is not None:
            try:
                device_registry.async_update_device(device.id, sw_version=version)
            except Exception:
                _LOGGER.exception("Failed to update device sw_version safely")
    except Exception:
        _LOGGER.exception("Failed to create/update device registry entry")

    # Register service for resetting learning
    service_registered = False

    async def handle_reset_learning(call: ServiceCall):
        if hasattr(coordinator, "controller"):
            await coordinator.controller.reset_learning()

    try:
        hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)
        service_registered = True
    except Exception:
        _LOGGER.exception("Failed to register service %s.reset_learning", DOMAIN)
        service_registered = False

    # Persist service flag for cleanup and ensure coordinator reference present
    hass.data[DOMAIN][entry.entry_id]["service_registered"] = service_registered
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    # Add update listener for reloads
    entry.add_update_listener(async_reload_entry)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an entry and clean up resources."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # If no more entries remain, remove the service registration if present
    if not hass.data.get(DOMAIN):
        try:
            if hass.services.has_service(DOMAIN, "reset_learning"):
                hass.services.async_remove(DOMAIN, "reset_learning")
        except Exception:
            _LOGGER.exception("Failed to remove service %s.reset_learning during unload", DOMAIN)

    return unload_ok
