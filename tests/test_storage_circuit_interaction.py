import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.storage_circuit_breaker import (
    StorageCircuitBreaker,
)


class FakeLogCoordinator:
    def __init__(self):
        self.messages = []

    async def _log(self, message: str, level: str = "info"):
        self.messages.append((level, message))


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


@pytest.mark.asyncio
async def test_debounced_save_cancellation_under_concurrent_calls():
    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {"val": 1}
    coord._storage_dirty = True
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.25

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

    class SlowStore:
        def __init__(self):
            self.saved = None

        async def async_save(self, data):
            # simulate slow I/O
            await asyncio.sleep(0.1)
            self.saved = data

    store = SlowStore()
    coord.store = store

    # Schedule first debounced save
    await coord._debounced_save()

    # Mutate stored_data and schedule another quickly to cancel the first
    coord.stored_data = {"val": 2}
    coord._storage_dirty = True
    await coord._debounced_save()

    # Wait enough time for debounced delayed save + slow store to complete
    await asyncio.sleep(0.6)

    assert store.saved == {"val": 2}


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_store_failures_and_blocks_saves():
    fake_log = FakeLogCoordinator()
    # small thresholds for speed
    cb = StorageCircuitBreaker(max_failures=2, reset_timeout=10, coordinator=fake_log)

    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {"x": 1}
    coord._storage_dirty = True
    coord.storage_circuit_breaker = cb

    class FailingStore:
        def __init__(self):
            self.calls = 0

        async def async_save(self, data):
            self.calls += 1
            raise OSError("disk error")

    failing = FailingStore()
    coord.store = failing

    # First attempt -> failure recorded
    await coord._perform_storage_save()
    # Second attempt -> failure recorded and circuit should open
    await coord._perform_storage_save()

    assert cb.failure_count >= 2
    # Circuit should now prevent further attempts
    assert await cb.should_attempt_operation() is False

    # Attempting to save while open should be a no-op (store.calls unchanged)
    await coord._perform_storage_save()
    assert failing.calls == 2
