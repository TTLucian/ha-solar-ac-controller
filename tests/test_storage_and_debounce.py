import asyncio
import logging

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class MockStore:
    def __init__(self):
        self.saved = None

    async def async_save(self, data):
        self.saved = data


@pytest.mark.asyncio
async def test_perform_storage_save_failure_leaves_dirty_flag(caplog):
    """A failed save leaves _storage_dirty=True; the error is logged for visibility."""
    caplog.set_level(logging.ERROR)
    coord = object.__new__(SolarACCoordinator)
    coord.stored_data = {"v": 42}
    coord._storage_dirty = True
    calls = {"n": 0}

    class FlakyStore:
        async def async_save(self, data):
            calls["n"] += 1
            raise OSError("io error")

    coord.store = FlakyStore()
    await coord._perform_storage_save()

    assert coord._storage_dirty is True
    assert calls["n"] == 1
    assert any("Error saving to storage" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_debounced_save_cancels_previous_and_saves_latest():
    """A second _debounced_save call before the first fires cancels the first task."""
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {"value": 1}
    coord._storage_dirty = True
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.2

    mock_store = MockStore()
    coord.store = mock_store

    await coord._debounced_save()
    coord.stored_data = {"value": 2}
    coord._storage_dirty = True
    await coord._debounced_save()

    await asyncio.sleep(0.5)
    assert mock_store.saved == {"value": 2}
