import asyncio
import logging

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


@pytest.mark.asyncio
async def test_debounced_save_cancellation_under_concurrent_calls():
    """Rapid successive _debounced_save calls resolve to a single write of the last value."""
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {"val": 1}
    coord._storage_dirty = True
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.25

    class SlowStore:
        def __init__(self):
            self.saved = None

        async def async_save(self, data):
            await asyncio.sleep(0.1)
            self.saved = data

    store = SlowStore()
    coord.store = store

    await coord._debounced_save()
    coord.stored_data = {"val": 2}
    coord._storage_dirty = True
    await coord._debounced_save()

    await asyncio.sleep(0.6)
    assert store.saved == {"val": 2}


@pytest.mark.asyncio
async def test_perform_storage_save_oserror_does_not_corrupt_state(caplog):
    """An OSError from the store is caught; stored_data and _storage_dirty are unchanged."""
    caplog.set_level(logging.ERROR)
    coord = object.__new__(SolarACCoordinator)
    original_data = {"important": "value", "samples": 42}
    coord.stored_data = dict(original_data)
    coord._storage_dirty = True

    class FailingStore:
        async def async_save(self, data):
            raise OSError("write failed")

    coord.store = FailingStore()
    await coord._perform_storage_save()

    # In-memory data must be unchanged
    assert coord.stored_data == original_data
    # Dirty flag stays True so the next cycle retries
    assert coord._storage_dirty is True
    assert any("Error saving to storage" in r.message for r in caplog.records)
