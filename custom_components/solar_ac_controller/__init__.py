from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_INITIAL_LEARNED_POWER,
    CONF_ZONE_TEMP_SENSORS,
    CONF_ZONES,
    DEFAULT_INITIAL_LEARNED_POWER,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]
ALL_PLATFORMS = PLATFORMS + ["switch", "select"]


async def _async_migrate_data(
    _old_major: int,
    _old_minor: int,
    old_data: dict[str, Any] | None,
    initial_lp: float = DEFAULT_INITIAL_LEARNED_POWER,
) -> dict[str, Any]:
    """Normalize and migrate stored data for Solar AC Controller."""

    if not isinstance(old_data, dict):
        return {"learned_power": {}, "samples": 0}

    # Use .copy() to be safe when modifying nested structures in 2026
    learned_power = old_data.get("learned_power", {}).copy()
    if not isinstance(learned_power, dict):
        learned_power = {}

    for zone, val in learned_power.items():
        if val is None:
            learned_power[zone] = {
                "default": initial_lp,
                "heat": initial_lp,
                "cool": initial_lp,
            }
        elif isinstance(val, (int, float)):
            v = float(val)
            learned_power[zone] = {"default": v, "heat": v, "cool": v}
        elif isinstance(val, dict):
            for mode in ["default", "heat", "cool"]:
                if mode not in val:
                    val[mode] = initial_lp

    return {
        "learned_power": learned_power,
        "samples": old_data.get("samples", 0),
    }


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Solar AC Controller component."""
    _svc_flag = "__svc_reset_learning_registered"
    hass_data = hass.data.setdefault(DOMAIN, {})

    if _svc_flag not in hass_data:

        async def handle_reset_learning(_call: ServiceCall) -> None:
            """Reset learning for all loaded coordinators."""
            for entry_dict in hass.data[DOMAIN].values():
                if isinstance(entry_dict, dict) and (
                    coordinator := entry_dict.get("coordinator")
                ):
                    if controller := getattr(coordinator, "controller", None):
                        await controller.reset_learning()

        async def handle_force_relearn(call: ServiceCall) -> None:
            """Reset learned power for a specific zone or all zones."""
            zone = call.data.get("zone")
            zone_found = False
            for entry_dict in hass.data[DOMAIN].values():
                if not isinstance(entry_dict, dict):
                    continue
                if coordinator := entry_dict.get("coordinator"):
                    if controller := getattr(coordinator, "controller", None):
                        zones = set(coordinator.config.get(CONF_ZONES, []))
                        if not zone:
                            await controller.reset_learning()
                            zone_found = True
                        elif zone in zones:
                            await controller.reset_learning(zone)
                            zone_found = True

            if zone and not zone_found:
                _LOGGER.warning("Zone '%s' not found in any loaded instance", zone)

        hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)
        hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)
        hass_data[_svc_flag] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Solar AC Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Migrate zone_temp_sensors from dict to list
    new_data = {**entry.data}
    new_options = {**entry.options}
    needs_update = False

    for data_dict in [new_data, new_options]:
        if (zone_temp_sensors := data_dict.get(CONF_ZONE_TEMP_SENSORS)) and isinstance(
            zone_temp_sensors, dict
        ):
            zones = data_dict.get(CONF_ZONES, [])
            data_dict[CONF_ZONE_TEMP_SENSORS] = [
                zone_temp_sensors.get(z_id, "") for z_id in zones
            ]
            needs_update = True

    if needs_update:
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options
        )

    # Storage and Versioning
    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version) if integration.version else None

    initial_lp = entry.options.get(
        CONF_INITIAL_LEARNED_POWER,
        entry.data.get(CONF_INITIAL_LEARNED_POWER, DEFAULT_INITIAL_LEARNED_POWER),
    )

    store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored_data = await store.async_load() or {"learned_power": {}, "samples": 0}

    # Run Migration
    stored_data = await _async_migrate_data(0, 0, stored_data, initial_lp)

    # 2026 Cleanup: Inline rounding map using modern Python
    def _round_vals(data: Any) -> Any:
        if isinstance(data, dict):
            return {k: _round_vals(v) for k, v in data.items()}
        try:
            return int(round(float(data)))
        except (ValueError, TypeError):
            return data

    stored_data["learned_power"] = _round_vals(stored_data.get("learned_power", {}))
    stored_data.setdefault("integration_enabled", True)
    stored_data.setdefault("activity_logging_enabled", False)

    await store.async_save(stored_data)

    # Registry and Coordinator
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Solar AC Controller",
        sw_version=version,
        configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
    )

    coordinator = SolarACCoordinator(hass, entry, store, stored_data, version=version)

    # Functional persistence
    coordinator.integration_enabled = stored_data["integration_enabled"]
    coordinator.activity_logging_enabled = stored_data["activity_logging_enabled"]

    # Entry forward setup would go here (hass.config_entries.async_forward_entry_setups)

    return True
