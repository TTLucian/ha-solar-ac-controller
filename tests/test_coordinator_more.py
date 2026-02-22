import asyncio
import logging
from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.storage_circuit_breaker import (
    StorageCircuitBreaker,
)


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class MockStore:
    def __init__(self):
        self.saved = None

    async def async_save(self, data):
        self.saved = data


@pytest.mark.asyncio
async def test_async_persist_learned_values_saves_rounded_values():
    coord = object.__new__(SolarACCoordinator)

    # Minimal coordinator internals required
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {}
    coord._storage_dirty = False
    coord.hass = FakeHass()
    coord._storage_debounce_task = None
    coord._last_storage_save = 0.0
    coord._storage_debounce_seconds = 5.0

    # Learned power with proper TypedDict structure
    coord.learned_power = {
        "zone1": {"default": 123.6, "heat": 200.9, "cool": 150.0, "lead_delta": 0.0},
        "zone2": {"default": 45.2, "heat": 45.2, "cool": 45.2, "lead_delta": 0.0},
    }
    coord.samples = 7
    coord.learned_idle_power = 18.75
    coord.idle_power_samples = 10

    # Circuit breaker allows operation
    async def _should_attempt():
        return True

    async def _record_success():
        return None

    async def _record_failure():
        return None

    async def _call_with_timeout(coro, timeout=10.0):
        return await coro

    coord.storage_circuit_breaker = cast(
        StorageCircuitBreaker,
        SimpleNamespace(
            should_attempt_operation=_should_attempt,
            record_success=_record_success,
            record_failure=_record_failure,
            call_with_timeout=_call_with_timeout,
        ),
    )

    mock_store = MockStore()
    coord.store = mock_store

    # Call persistence helper
    await coord.async_persist_learned_values()

    # Ensure store received rounded integer values
    assert isinstance(mock_store.saved, dict)
    assert mock_store.saved["learned_power"]["zone1"]["default"] == 124
    assert mock_store.saved["learned_power"]["zone1"]["heat"] == 201
    assert mock_store.saved["learned_power"]["zone2"]["default"] == 45
    assert mock_store.saved["samples"] == 7
    assert mock_store.saved["idle_power"] == 18.8
    assert mock_store.saved["idle_power_samples"] == 10
    # Coordinator should clear dirty flag after successful save
    assert coord._storage_dirty is False


def test__rounded_power_nested_and_non_numeric():
    coord = object.__new__(SolarACCoordinator)

    # Nested dict with numeric and non-numeric values
    value = {"a": {"x": 12.7, "y": "n/a"}, "b": 3.2}
    out = SolarACCoordinator._rounded_power(coord, value)

    assert out["a"]["x"] == 13
    assert out["a"]["y"] == "n/a"
    assert out["b"] == 3


@pytest.mark.asyncio
async def test_create_task_ignored_cancel_does_not_log(caplog):
    caplog.set_level(logging.ERROR)

    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()

    # Start a long-running sleep and cancel it
    task = coord.create_task(asyncio.sleep(1))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert "Background task exception" not in caplog.text
