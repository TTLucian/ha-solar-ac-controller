"""Integration-level test: async_setup_entry → coordinator → entity wiring.

Verifies the full setup chain without a real Home Assistant instance.
All HA infrastructure (Store, device_registry, config_entries, loader) is
mocked so the test runs in plain pytest with no HA test harness.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.solar_ac_controller import async_setup_entry
from custom_components.solar_ac_controller.const import DOMAIN
from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

# Minimal stored data that passes migration/validation without errors.
_STORED = {
    "learned_power": {},
    "samples": 0,
    "integration_enabled": True,
    "activity_logging_enabled": False,
    "season_mode": "heat",
}

# Minimal config entry data covering all required fields.
_ENTRY_DATA = {
    "zones": ["climate.zone1"],
    "solar_sensor": "sensor.solar",
    "grid_sensor": "sensor.grid",
    "ac_power_sensor": "sensor.ac_power",
    "solar_threshold_on": 1200,
    "solar_threshold_off": 500,
    "initial_learned_power": 1000,
    "short_cycle_on_seconds": 1200,
    "short_cycle_off_seconds": 20,
    "panic_threshold": 2000,
    "panic_delay": 60,
    "manual_lock_seconds": 1200,
    "action_delay_seconds": 3,
    "update_interval": 10,
    "aggressiveness": 0.5,
    "compressor_ramp_seconds": 60,
    "enable_temperature_modulation": False,
    "enable_diagnostics_sensor": False,
}


def _make_hass() -> Any:
    """Build a minimal hass-like namespace that satisfies setup_entry."""
    hass = SimpleNamespace()
    hass.data = {}
    hass.loop = asyncio.get_event_loop()
    hass.bus = SimpleNamespace(async_fire=MagicMock())

    # State registry — return None for any entity (no real devices)
    hass.states = SimpleNamespace(get=MagicMock(return_value=None))

    # config_entries stub
    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=AsyncMock(return_value=True),
        async_get_entry=MagicMock(return_value=None),
    )

    # services stub
    hass.services = SimpleNamespace(
        has_service=MagicMock(return_value=False),
        async_register=MagicMock(),
    )

    return hass


def _make_entry() -> Any:
    """Build a mock config entry."""
    entry = SimpleNamespace()
    entry.entry_id = "test_entry_id"
    entry.data = dict(_ENTRY_DATA)
    entry.options = {}
    entry.title = "Solar AC Controller"
    entry.add_update_listener = MagicMock()
    # Called by DataUpdateCoordinator.__init__ when config_entry is passed
    entry.async_on_unload = MagicMock()
    return entry


class _MockStore:
    """Minimal Store stub."""

    def __init__(self) -> None:
        self._data: dict = {}

    async def async_load(self) -> dict:
        return dict(_STORED)

    async def async_save(self, data: dict) -> None:
        self._data = data


@pytest.mark.asyncio
async def test_async_setup_entry_creates_coordinator():
    """async_setup_entry must register a SolarACCoordinator under hass.data."""
    hass = _make_hass()
    entry = _make_entry()
    store = _MockStore()

    mock_integration = SimpleNamespace(version="0.99.0")

    with (
        # Swap out real Store construction for our stub
        patch(
            "custom_components.solar_ac_controller.Store",
            return_value=store,
        ),
        # Prevent real device_registry lookup
        patch(
            "custom_components.solar_ac_controller.dr.async_get",
            return_value=SimpleNamespace(async_get_or_create=MagicMock()),
        ),
        # Prevent real integration manifest lookup
        patch(
            "custom_components.solar_ac_controller.async_get_integration",
            new=AsyncMock(return_value=mock_integration),
        ),
        # DataUpdateCoordinator.__init__ calls frame.report_usage which
        # requires the real HA event-loop context; stub it out.
        patch("homeassistant.helpers.frame.report_usage", return_value=None),
        # Prevent the real first_refresh from firing the update loop
        patch.object(
            SolarACCoordinator,
            "async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True, "async_setup_entry should return True on success"

    # Coordinator must be stored under hass.data[DOMAIN][entry_id]
    assert DOMAIN in hass.data
    assert entry.entry_id in hass.data[DOMAIN]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert isinstance(
        coordinator, SolarACCoordinator
    ), "hass.data entry must contain a SolarACCoordinator"


@pytest.mark.asyncio
async def test_async_setup_entry_forwards_all_platforms():
    """All expected platforms must be forwarded during setup."""
    from custom_components.solar_ac_controller import ALL_PLATFORMS

    hass = _make_hass()
    entry = _make_entry()
    store = _MockStore()

    with (
        patch("custom_components.solar_ac_controller.Store", return_value=store),
        patch(
            "custom_components.solar_ac_controller.dr.async_get",
            return_value=SimpleNamespace(async_get_or_create=MagicMock()),
        ),
        patch(
            "custom_components.solar_ac_controller.async_get_integration",
            new=AsyncMock(return_value=SimpleNamespace(version="0.99.0")),
        ),
        patch("homeassistant.helpers.frame.report_usage", return_value=None),
        patch.object(
            SolarACCoordinator,
            "async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        await async_setup_entry(hass, entry)

    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    _, call_args, _ = hass.config_entries.async_forward_entry_setups.mock_calls[0]
    forwarded_entry, forwarded_platforms = call_args
    assert forwarded_entry is entry
    assert set(forwarded_platforms) == set(
        ALL_PLATFORMS
    ), f"Expected platforms {ALL_PLATFORMS}, got {forwarded_platforms}"


@pytest.mark.asyncio
async def test_async_setup_entry_coordinator_has_expected_attributes():
    """Coordinator produced by setup must expose key runtime attributes."""
    hass = _make_hass()
    entry = _make_entry()
    store = _MockStore()

    with (
        patch("custom_components.solar_ac_controller.Store", return_value=store),
        patch(
            "custom_components.solar_ac_controller.dr.async_get",
            return_value=SimpleNamespace(async_get_or_create=MagicMock()),
        ),
        patch(
            "custom_components.solar_ac_controller.async_get_integration",
            new=AsyncMock(return_value=SimpleNamespace(version="0.99.0")),
        ),
        patch("homeassistant.helpers.frame.report_usage", return_value=None),
        patch.object(
            SolarACCoordinator,
            "async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        await async_setup_entry(hass, entry)

    coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Core attributes that the rest of the codebase depends on
    assert hasattr(coord, "integration_enabled")
    assert hasattr(coord, "activity_logging_enabled")
    assert hasattr(coord, "season_mode")
    assert hasattr(coord, "aggressiveness")
    assert hasattr(coord, "learned_power")
    assert hasattr(coord, "samples")
    assert hasattr(coord, "panic_manager")
    assert hasattr(coord, "zone_manager")
    assert hasattr(coord, "controller")
    assert hasattr(coord, "decision_engine")

    # Persisted values loaded from stored data
    assert coord.integration_enabled is True
    assert coord.activity_logging_enabled is False
    assert coord.aggressiveness == pytest.approx(0.5)
