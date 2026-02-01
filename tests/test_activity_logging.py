from typing import cast

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.storage_circuit_breaker import (
    StorageCircuitBreaker,
)


class _MockCircuitBreaker:
    def __init__(self, allowed: bool):
        self._allowed = allowed

    async def should_attempt_operation(self):
        return self._allowed


@pytest.mark.asyncio
async def test_async_set_activity_logging_enabled_skips_save_when_circuit_open():
    coord = object.__new__(SolarACCoordinator)
    # Minimal attributes needed for the method under test
    coord.activity_logging_enabled = False
    coord.stored_data = {}
    coord.storage_circuit_breaker = cast(
        StorageCircuitBreaker, _MockCircuitBreaker(False)
    )

    called = {"saved": False, "updated": False}

    async def fake_debounced_save():
        called["saved"] = True

    # coordinator.async_update_listeners is called synchronously in the method
    coord.async_update_listeners = lambda: called.__setitem__("updated", True)
    coord._debounced_save = fake_debounced_save

    await coord.async_set_activity_logging_enabled(True)

    assert coord.activity_logging_enabled is True
    assert coord.stored_data["activity_logging_enabled"] is True
    # Circuit open -> save should not have been attempted
    assert called["saved"] is False
    # Listeners should still be notified
    assert called["updated"] is True


@pytest.mark.asyncio
async def test_async_set_activity_logging_enabled_triggers_save_when_allowed():
    coord = object.__new__(SolarACCoordinator)
    coord.activity_logging_enabled = False
    coord.stored_data = {}
    coord.storage_circuit_breaker = cast(
        StorageCircuitBreaker, _MockCircuitBreaker(True)
    )

    called = {"saved": False, "updated": False}

    async def fake_debounced_save():
        called["saved"] = True

    coord.async_update_listeners = lambda: called.__setitem__("updated", True)
    coord._debounced_save = fake_debounced_save

    await coord.async_set_activity_logging_enabled(False)

    assert coord.activity_logging_enabled is False
    assert coord.stored_data["activity_logging_enabled"] is False
    # Circuit allowed -> save should have been attempted
    assert called["saved"] is True
    assert called["updated"] is True
