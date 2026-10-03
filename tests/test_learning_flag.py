"""Regression tests for the live-learning safety flag.

``learning_active_cached`` is not just a status bit. It gates three things:

* a -100 point penalty on add confidence (``decisions.py``), on a 0-100 scale,
* the zone-swap guard, which refuses to swap "because adding or removing a zone
  would contaminate the measurement and force a discard",
* the Learning Active binary sensor.

So a transient failure while refreshing it must never flip it to False. That
would let a second zone be added during a live learning session, which is
exactly what the refresh was added to prevent.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator


class FlakySession:
    """Session whose get_zone() raises, simulating a transient transport error."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_zone(self) -> str:
        self.calls += 1
        raise RuntimeError("transport closed")


class WorkingSession:
    async def get_zone(self) -> str:
        return "climate.living"


def _coordinator(session: Any, *, active: bool) -> SolarACCoordinator:
    coord = object.__new__(SolarACCoordinator)
    coord.learning_active_cached = active
    coord.controller = type("C", (), {"session": session})()
    return coord


@pytest.mark.asyncio
async def test_flag_keeps_previous_value_when_lookup_fails() -> None:
    """A failed lookup must not clear a flag that was already True.

    Failing open here is the bug: it drops the -100pt add penalty and unlocks
    zone swapping for the rest of the cycle.
    """
    coord = _coordinator(FlakySession(), active=True)

    await coord._refresh_learning_active()

    assert coord.learning_active_cached is True, (
        "learning_active_cached was cleared by a failed lookup; a second zone "
        "could be added during live learning and contaminate the measurement"
    )


@pytest.mark.asyncio
async def test_flag_still_refreshes_when_lookup_succeeds() -> None:
    """The happy path must still update the flag in both directions."""
    starting = _coordinator(WorkingSession(), active=False)
    await starting._refresh_learning_active()
    assert starting.learning_active_cached is True, "a live session was not detected"

    stopped = _coordinator(WorkingSession(), active=True)
    stopped.controller.session = type("S", (), {"get_zone": lambda self: _empty()})()
    await stopped._refresh_learning_active()
    assert stopped.learning_active_cached is False, "a finished session was not cleared"


async def _empty() -> None:
    return None


@pytest.mark.asyncio
async def test_failed_lookup_is_logged_not_silent() -> None:
    """Swallowing the error silently is what made this survive so long."""
    coord = _coordinator(FlakySession(), active=True)
    logged: list[str] = []

    async def _capture(message: str, level: str | None = "info") -> None:
        logged.append(f"{level}: {message}")

    coord._log = _capture  # type: ignore[method-assign]

    await coord._refresh_learning_active()

    assert logged, "the failure was swallowed without any log entry"
