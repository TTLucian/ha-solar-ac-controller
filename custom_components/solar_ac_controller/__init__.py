from __future__ import annotations

import logging
import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.storage import Store
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
    CONF_ZONES,
    CONF_MANUAL_LOCK_SECONDS,
    CONF_SHORT_CYCLE_ON_SECONDS,
    CONF_SHORT_CYCLE_OFF_SECONDS,
    CONF_PANIC_THRESHOLD,
    CONF_PANIC_DELAY,
    CONF_ACTION_DELAY_SECONDS,
    CONF_INITIAL_LEARNED_POWER,
    CONF_ENABLE_DIAGNOSTICS,
)
from .coordinator import SolarACCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


def _short_name(entity_id: str) -> str:
    """Return trailing segment of an entity_id."""
    if not isinstance(entity_id, str):
        return str(entity_id)
    return entity_id.rsplit(".", 1)[-1]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """YAML setup is intentionally unsupported."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration and coordinator, preserving original behavior plus migration."""
    hass.data.setdefault(DOMAIN, {})

    # Load integration version from manifest
    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version
    hass.data[DOMAIN]["version"] = version

    # Load persistent storage
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    try:
        # Some HA Store implementations may raise NotImplementedError during migration
        # or when a migration callback is expected but not provided. Treat that as
        # "no stored data" and continue with an empty payload.
        stored: dict[str, Any] | None = await store.async_load()
    except NotImplementedError:
        _LOGGER.warning(
            "Storage migration function not implemented for %s; starting with empty store",
            STORAGE_KEY,
        )
        stored = {}
    except Exception as exc:
        _LOGGER.exception("Failed to load storage for %s: %s; starting with empty store", DOMAIN, exc)
        stored = {}

    stored = stored or {}

    # Create coordinator (it will perform migration if needed)
    coordinator = SolarACCoordinator(
        hass,
        entry,
        store,
        stored,
        version=version,
    )

    # First refresh to populate state
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "store": store,
    }

    # ---------------------------------------------------------------------
    # OPTIONS UPDATE LISTENER (preserve original behavior)
    # ---------------------------------------------------------------------
    async def _async_options_updated(hass: HomeAssistant, updated_entry: ConfigEntry):
        data = hass.data.get(DOMAIN, {}).get(updated_entry.entry_id)
        if not data:
            return

        coordinator: SolarACCoordinator = data["coordinator"]

        old = bool(coordinator.config.get(CONF_ENABLE_DIAGNOSTICS, False))
        merged = {**updated_entry.data, **updated_entry.options}
        new = bool(merged.get(CONF_ENABLE_DIAGNOSTICS, False))

        # Diagnostics toggle changed → reload integration
        if old != new:
            await hass.config_entries.async_reload(updated_entry.entry_id)
            return

        # Apply merged config
        coordinator.config = merged

        coordinator.manual_lock_seconds = merged.get(
            CONF_MANUAL_LOCK_SECONDS, coordinator.manual_lock_seconds
        )
        coordinator.short_cycle_on_seconds = merged.get(
            CONF_SHORT_CYCLE_ON_SECONDS, coordinator.short_cycle_on_seconds
        )
        coordinator.short_cycle_off_seconds = merged.get(
            CONF_SHORT_CYCLE_OFF_SECONDS, coordinator.short_cycle_off_seconds
        )
        coordinator.action_delay_seconds = merged.get(
            CONF_ACTION_DELAY_SECONDS, coordinator.action_delay_seconds
        )
        coordinator.panic_threshold = merged.get(
            CONF_PANIC_THRESHOLD, coordinator.panic_threshold
        )
        coordinator.panic_delay = merged.get(
            CONF_PANIC_DELAY, coordinator.panic_delay
        )
        coordinator.initial_learned_power = merged.get(
            CONF_INITIAL_LEARNED_POWER, coordinator.initial_learned_power
        )

        _LOGGER.info("Solar AC options updated; refreshing coordinator")

        try:
            await coordinator.async_refresh()
        except Exception:
            _LOGGER.exception("Coordinator refresh failed after options update")

    entry.add_update_listener(_async_options_updated)

    # ---------------------------------------------------------------------
    # DEVICE REGISTRY
    # ---------------------------------------------------------------------
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "solar_ac_controller")},
        name="Solar AC Controller",
        manufacturer="TTLucian",
        model="Solar AC Smart Controller",
        sw_version=version,
        configuration_url="https://github.com/TTLucian/ha-solar-ac-controller",
    )

    # ---------------------------------------------------------------------
    # PLATFORM SETUP (preload to avoid blocking warnings)
    # ---------------------------------------------------------------------
    await asyncio.gather(
        *(hass.async_add_executor_job(integration.get_platform, p) for p in PLATFORMS)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ---------------------------------------------------------------------
    # SERVICES (reset_learning, force_relearn) - preserve original semantics
    # ---------------------------------------------------------------------
    async def handle_reset_learning(call: ServiceCall):
        try:
            # Controller should be present; guard just in case
            if getattr(coordinator, "controller", None):
                await coordinator.controller.reset_learning()
            else:
                _LOGGER.warning("reset_learning called but controller is not initialized")
        except Exception as exc:
            _LOGGER.exception("reset_learning service failed: %s", exc)
            try:
                await coordinator._log(f"[SERVICE_ERROR] reset_learning {exc}")
            except Exception:
                _LOGGER.exception("Failed to write service error to logbook")

    hass.services.async_register(DOMAIN, "reset_learning", handle_reset_learning)

    async def handle_force_relearn(call: ServiceCall):
        zone = call.data.get("zone")

        # Validate zone if provided
        if zone and zone not in coordinator.config.get(CONF_ZONES, []):
            await coordinator._log(f"[FORCE_RELEARN_INVALID_ZONE] {zone}")
            return

        # Reset one or all zones
        if zone:
            zn = _short_name(zone)
            coordinator.set_learned_power(zn, float(coordinator.initial_learned_power), mode=None)
            target = zn
        else:
            for z in coordinator.config.get(CONF_ZONES, []):
                zn = _short_name(z)
                coordinator.set_learned_power(zn, float(coordinator.initial_learned_power), mode=None)
            target = "all"

        # Reset learning state
        coordinator.samples = 0
        coordinator.learning_active = False
        coordinator.learning_zone = None
        coordinator.learning_start_time = None
        coordinator.ac_power_before = None

        await coordinator._log(f"[FORCE_RELEARN] target={target}")

        try:
            await coordinator._persist_learned_values()
        except Exception as exc:
            _LOGGER.exception("force_relearn save failed: %s", exc)
            try:
                await coordinator._log(f"[SERVICE_ERROR] force_relearn save {exc}")
            except Exception:
                _LOGGER.exception("Failed to write service error to logbook")

    hass.services.async_register(DOMAIN, "force_relearn", handle_force_relearn)

    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the new format."""
    data = dict(entry.data)

    zones = data.get(CONF_ZONES)
    if isinstance(zones, str):
        data[CONF_ZONES] = [z.strip() for z in zones.split(",") if z.strip()]

    hass.config_entries.async_update_entry(entry, data=data)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration and clean up resources."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data:
        coordinator: SolarACCoordinator | None = data.get("coordinator")
        if coordinator:
            # Cancel background tasks if running
            if getattr(coordinator, "_panic_task", None) and not coordinator._panic_task.done():
                coordinator._panic_task.cancel()
            if getattr(coordinator, "_master_shutdown_task", None) and not coordinator._master_shutdown_task.done():
                coordinator._master_shutdown_task.cancel()

    # Unregister services if no entries remain
    if not hass.data.get(DOMAIN):
        try:
            hass.services.async_remove(DOMAIN, "reset_learning")
            hass.services.async_remove(DOMAIN, "force_relearn")
        except Exception:
            _LOGGER.debug("Failed to remove services for %s", DOMAIN)

    return ok
