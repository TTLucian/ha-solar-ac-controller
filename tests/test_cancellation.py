"""Cancellation must reach the event loop, not be absorbed by a handler.

`asyncio.CancelledError` inherits from `BaseException` precisely so that a broad
`except Exception` cannot absorb it. Every site below went further and listed it
explicitly alongside `OSError` and `ValueError`, which quietly undid that
protection:

* a task cancelled during shutdown kept running instead of unwinding;
* in `_perform_freeze_cleanup` - the path that turns zones off when solar drops
  or master goes off - a cancelled cleanup ignored the cancellation and kept
  toggling zones;
* the cancellation was then reported through `_LOGGER.exception`, so an ordinary
  shutdown looked like a fault in the logbook.

The resilience those handlers were written for is real and is kept here: an
`OSError` or a `ValueError` while scheduling a debounced save must not abort the
caller, and a failed flush must not stop the zone turn-off that follows it. Only
cancellation is re-raised.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


def _coordinator(*, save_raises: BaseException | None = None) -> SolarACCoordinator:
    """Minimal coordinator wired for the setter and freeze-cleanup paths."""
    coord = object.__new__(SolarACCoordinator)
    coord._storage_lock = asyncio.Lock()
    coord._user_freeze_lock = asyncio.Lock()
    coord.stored_data = {}
    coord._storage_dirty = False
    coord.integration_suspended = False
    coord.integration_disabled = False
    coord.activity_logging_enabled = False
    coord.season_mode = "summer"
    coord.aggressiveness = 50.0
    coord.active_zones = []
    coord.panic_manager = None  # type: ignore[assignment]
    coord.action_executor = None  # type: ignore[assignment]
    coord.controller = None  # type: ignore[assignment]
    coord._state_lock = asyncio.Lock()
    coord.master_off_since = None
    coord.master_ema_reset_done = False
    coord.ema_30s = 0.0
    coord.ema_5m = 0.0

    async def _log(message: str, level: str | None = "info") -> None:
        return None

    def _recalc() -> None:
        # Synchronous in the coordinator (line 456) - it is called without await.
        return None

    async def _save() -> None:
        if save_raises is not None:
            raise save_raises
        return None

    coord._log = _log  # type: ignore[method-assign]
    coord._debounce_recalc = _recalc  # type: ignore[method-assign]
    coord._debounced_save = _save  # type: ignore[method-assign]
    return coord


SETTERS = (
    ("integration_suspended", lambda c: c.async_set_integration_suspended(True)),
    ("integration_disabled", lambda c: c.async_set_integration_disabled(True)),
    ("activity_logging_enabled", lambda c: c.async_set_activity_logging_enabled(True)),
    ("season_mode", lambda c: c.async_set_season_mode("winter")),
    ("aggressiveness", lambda c: c.async_set_aggressiveness(75)),
)
SETTER_IDS = [name for name, _ in SETTERS]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "call"), SETTERS, ids=SETTER_IDS)
async def test_setter_propagates_cancellation(name: str, call: Any) -> None:
    """A cancelled save must not be absorbed by the setter."""
    coord = _coordinator(save_raises=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await call(coord)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "call"), SETTERS, ids=SETTER_IDS)
async def test_setter_still_swallows_os_errors(name: str, call: Any) -> None:
    """The resilience the handler exists for must survive."""
    coord = _coordinator(save_raises=OSError("disk gone"))

    await call(coord)  # must not raise


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "call"), SETTERS, ids=SETTER_IDS)
async def test_setter_still_swallows_value_errors(name: str, call: Any) -> None:
    """A bad payload must not break the caller either."""
    coord = _coordinator(save_raises=ValueError("bad payload"))

    await call(coord)  # must not raise


@pytest.mark.asyncio
async def test_freeze_cleanup_propagates_cancellation() -> None:
    """Freeze cleanup turns zones off; it must not ignore a cancellation.

    This is the safety path. Swallowing CancelledError here means a cleanup
    cancelled during shutdown keeps going and continues toggling zones.
    """
    coord = _coordinator()

    async def _flush() -> None:
        raise asyncio.CancelledError()

    async def _cancel_panic() -> None:
        raise asyncio.CancelledError()

    coord._flush_pending_storage_save = _flush  # type: ignore[method-assign]
    coord.panic_manager = type("P", (), {"cancel_panic": staticmethod(_cancel_panic)})()

    with pytest.raises(asyncio.CancelledError):
        await coord._perform_freeze_cleanup()


@pytest.mark.asyncio
async def test_freeze_cleanup_still_swallows_ordinary_errors() -> None:
    """A failed flush must not stop the zone turn-off that follows it."""
    coord = _coordinator()

    async def _flush() -> None:
        raise OSError("disk gone")

    coord._flush_pending_storage_save = _flush  # type: ignore[method-assign]

    await coord._perform_freeze_cleanup()  # must not raise
