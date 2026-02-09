import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.storage_circuit_breaker import (
    StorageCircuitBreaker,
)


class FakeCoordinator:
    def __init__(self):
        self.messages = []

    async def _log(self, message, level="info"):
        self.messages.append((level, message))


@pytest.mark.asyncio
async def test_storage_circuit_breaker_opens_and_recovers():
    coord = FakeCoordinator()
    # Use small reset timeout for test speed
    cb = StorageCircuitBreaker(max_failures=2, reset_timeout=1, coordinator=coord)

    # Initially allowed
    assert await cb.should_attempt_operation() is True

    # Record a failure twice to trigger open state
    await cb.record_failure()
    await cb.record_failure()

    # Now it should be open
    assert await cb.should_attempt_operation() is False

    # Coordinator should have received an 'open' log
    assert any("disabled" in m[1] or "disabled" in m[1] for m in coord.messages)

    # Wait beyond reset timeout and then it should recover
    await asyncio.sleep(1.1)
    assert await cb.should_attempt_operation() is True

    # After recovery a log entry should indicate recovery or re-enabled state
    assert any(
        ("entering recovery" in m[1]) or ("re-enabled" in m[1]) or ("recovered" in m[1])
        for m in coord.messages
    )


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class MockStore:
    def __init__(self):
        self.saved = None

    async def async_save(self, data):
        # fast save
        self.saved = data


@pytest.mark.asyncio
async def test_debounced_save_cancels_previous_and_saves_latest():
    coord = object.__new__(SolarACCoordinator)

    # Provide minimal internals
    coord.hass = FakeHass()
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {"value": 1}
    coord._storage_dirty = True
    coord._storage_debounce_task = None
    coord._last_storage_save = dt_util.utcnow().timestamp()
    coord._storage_debounce_seconds = 0.2

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

    # Schedule first debounced save
    await coord._debounced_save()

    # Mutate and schedule another shortly after to cancel the first
    coord.stored_data = {"value": 2}
    coord._storage_dirty = True
    await coord._debounced_save()

    # Wait enough for debounced task to run
    await asyncio.sleep(0.5)

    # Ensure store saved the latest value
    assert mock_store.saved == {"value": 2}
