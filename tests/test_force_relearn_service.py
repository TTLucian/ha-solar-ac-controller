"""Regression tests for the solar_ac_controller.force_relearn service handler."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.solar_ac_controller import async_setup
from custom_components.solar_ac_controller.const import CONF_ZONES, DOMAIN
from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

# Generous: the happy path is pure in-memory work plus one store write.
TIMEOUT = 5.0


class MockStore:
    def __init__(self) -> None:
        self.saved: dict | None = None

    async def async_save(self, data: dict) -> None:
        self.saved = data


class MockServices:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def async_register(self, domain: str, service: str, handler: Any) -> None:
        self.registered[service] = handler

    def has_service(self, domain: str, service: str) -> bool:
        return service in self.registered

    def async_remove(self, domain: str, service: str) -> None:
        self.registered.pop(service, None)


class MockHass:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.services = MockServices()

    def async_create_task(self, coro: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)


def _make_coordinator() -> SolarACCoordinator:
    """Minimal coordinator exercising the real persistence path."""
    coord = object.__new__(SolarACCoordinator)
    coord.hass = MockHass()  # type: ignore[assignment]
    coord.store = MockStore()
    coord._storage_lock = asyncio.Lock()
    coord._storage_dirty = False
    coord._storage_debounce_task = None
    # Force the immediate-save branch so no background task is spawned.
    coord._last_storage_save = 0.0
    coord._storage_debounce_seconds = 5.0
    coord.stored_data = {}
    coord.learned_power = {
        "living": {"default": 100.0, "heat": 100.0, "cool": 100.0, "lead_delta": 0.0},
        "study": {"default": 50.0, "heat": 50.0, "cool": 50.0, "lead_delta": 0.0},
    }
    coord.samples = 12
    coord.learned_idle_power = 18.0
    coord.idle_power_samples = 3
    coord.zone_action_history = {}
    coord.config = {CONF_ZONES: ["climate.living", "climate.study"]}
    coord.last_relearn_at = None
    coord.last_relearn_target = ""
    # async_update_listeners() is inherited from DataUpdateCoordinator.
    coord._listeners = {}
    return coord


async def _register_handler(coord: SolarACCoordinator) -> Any:
    """Register the real service handler and return it."""
    hass = MockHass()
    hass.data[DOMAIN] = {"entry1": {"coordinator": coord}}
    await async_setup(hass, {})  # type: ignore[arg-type]
    return hass.services.registered["force_relearn"]


async def _call(handler: Any, zone: str | None = None) -> None:
    """Invoke the handler, failing fast instead of hanging if it deadlocks.

    asyncio.wait_for is not usable here: the handler swallows CancelledError,
    so cancelling a deadlocked call never completes and wait_for would hang
    the whole suite rather than report a failure.
    """
    call = SimpleNamespace(data={"zone": zone} if zone else {})
    task = asyncio.create_task(handler(call))
    done, pending = await asyncio.wait({task}, timeout=TIMEOUT)
    assert not pending, (
        "force_relearn deadlocked: handler did not return within "
        f"{TIMEOUT}s, which means _storage_lock was re-acquired by the "
        "same task that already holds it"
    )
    assert task.exception() is None


@pytest.mark.asyncio
async def test_force_relearn_all_zones_does_not_deadlock() -> None:
    """Resetting all zones must return instead of hanging on the storage lock."""
    coord = _make_coordinator()
    handler = await _register_handler(coord)

    await _call(handler)

    assert coord.learned_power == {}
    assert coord.samples == 0


@pytest.mark.asyncio
async def test_force_relearn_single_zone_does_not_deadlock() -> None:
    """Resetting one zone must return instead of hanging on the storage lock."""
    coord = _make_coordinator()
    handler = await _register_handler(coord)

    await _call(handler, zone="climate.living")

    assert "living" not in coord.learned_power
    assert "study" in coord.learned_power
    assert coord.samples == 0


@pytest.mark.asyncio
async def test_force_relearn_releases_storage_lock() -> None:
    """The storage lock must be free afterwards, or every later save times out."""
    coord = _make_coordinator()
    handler = await _register_handler(coord)

    await _call(handler)

    assert not coord._storage_lock.locked(), (
        "force_relearn left _storage_lock held; every subsequent "
        "_debounced_save would hit the 2s timeout and stop persisting"
    )


@pytest.mark.asyncio
async def test_force_relearn_persists_cleared_values() -> None:
    """The reset must actually reach the store, not just in-memory state."""
    coord = _make_coordinator()
    handler = await _register_handler(coord)

    await _call(handler)

    saved = coord.store.saved
    assert saved is not None, "force_relearn did not write to the store"
    assert saved["learned_power"] == {}
    assert saved["samples"] == 0


@pytest.mark.asyncio
async def test_force_relearn_records_feedback_fields() -> None:
    """The Last Relearn sensor's inputs must be set for the handler's last entry."""
    coord = _make_coordinator()
    handler = await _register_handler(coord)

    await _call(handler, zone="climate.living")

    assert coord.last_relearn_target == "living"
    assert coord.last_relearn_at is not None


@pytest.mark.asyncio
async def test_persist_learned_values_propagates_cancellation() -> None:
    """async_persist_learned_values must not swallow CancelledError.

    Catching it broke cooperative cancellation: an in-flight save could no
    longer be torn down on shutdown, and force_relearn's own call could
    never unwind, because cancelling a deadlocked task did not stop it.
    """

    async def _cancel() -> None:
        raise asyncio.CancelledError

    coord = _make_coordinator()
    coord._debounced_save = _cancel  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await coord.async_persist_learned_values()
