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
async def test_create_task_logs_exception(caplog):
    caplog.set_level(logging.ERROR)

    coord = object.__new__(SolarACCoordinator)
    coord.hass = FakeHass()

    async def _boom():
        raise RuntimeError("boom")

    task = coord.create_task(_boom())

    # Give the task a moment to run and the done-callback to log
    await asyncio.sleep(0.01)
    try:
        await asyncio.wait_for(task, timeout=1)
    except RuntimeError:
        # The exception is raised on the task; it's expected.
        pass

    assert "Background task exception" in caplog.text


@pytest.mark.asyncio
async def test_async_set_integration_enabled_skips_save_when_circuit_open():
    coord = object.__new__(SolarACCoordinator)

    # Minimal attributes used by the method
    coord._storage_lock = asyncio.Lock()
    coord.stored_data = {}
    coord._storage_dirty = False
    coord.hass = FakeHass()
    coord._listeners = {}

    # Circuit breaker refuses operation
    async def _should_attempt():
        return False

    async def _record_success():
        return None

    async def _record_failure():
        return None

    coord.storage_circuit_breaker = cast(
        StorageCircuitBreaker,
        SimpleNamespace(
            should_attempt_operation=_should_attempt,
            record_success=_record_success,
            record_failure=_record_failure,
        ),
    )

    mock_store = MockStore()
    coord.store = mock_store

    # Call the method; it should not raise and should not call store.async_save
    await coord.async_set_integration_enabled(True)

    assert coord.stored_data.get("integration_enabled") is True
    assert mock_store.saved is None
