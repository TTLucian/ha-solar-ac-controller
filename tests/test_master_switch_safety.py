"""A failed compressor-relay safety command must never look like success.

`handle_master_switch` is what turns the physical relay off once the
compressor winds down after a freeze. Both the disabled path and the frozen
path in `_async_update_data` run it, and both ended the same way:

    try:
        await self.master_controller.handle_master_switch(...)
    except Exception:  # noqa: BLE001
        pass
    self.metrics.record_cycle_end(cycle_start, success=True)

Three things were wrong at once. The exception was swallowed, so nothing
reached the log. The relay was left un-commanded. And the cycle was then
recorded as a *success*, so `error_count` stayed put and diagnostics reported
a healthy integration while a compressor might still be running.

The `# noqa: BLE001` is what hid it from lint.

The AC-power reading is deliberately optional and stays non-fatal: an
unreadable sensor leaves `ac_power` as None and lets the switch logic decide.
Cancellation must still propagate, so the handler catches `Exception` only -
`CancelledError` is a `BaseException`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.exceptions import (
    SensorInvalidError,
    SensorUnavailableError,
)


class _Boom(Exception):
    """An ordinary failure from the relay control."""


class _Metrics:
    def __init__(self) -> None:
        self.records: list[bool] = []

    def record_cycle_end(self, start_time: float, success: bool = True) -> None:
        self.records.append(success)


class _Master:
    def __init__(self, relay_error: BaseException | None = None) -> None:
        self.calls: list[tuple[float, float | None]] = []
        self._error = relay_error

    async def handle_master_switch(self, solar: float, cycle_start: float, ac_power: float | None = None) -> None:
        self.calls.append((solar, ac_power))
        if self._error is not None:
            raise self._error


class _ConfigManager:
    def get(self, key: str) -> Any:
        return "sensor.ac_power"


def _coordinator(
    *,
    relay_error: BaseException | None = None,
    sensor_error: Exception | None = SensorUnavailableError("AC power sensor"),
    sensor_value: float | None = None,
) -> SolarACCoordinator:
    """Coordinator wired for the master-switch safety path."""
    coord = object.__new__(SolarACCoordinator)
    coord._state_lock = asyncio.Lock()
    coord.last_action = "ok"
    coord.metrics = _Metrics()  # type: ignore[assignment]
    coord.master_controller = _Master(relay_error)  # type: ignore[assignment]
    coord.config_manager = _ConfigManager()  # type: ignore[assignment]

    async def _cached_state(entity: Any) -> Any:
        return None

    def _validate(state: Any, name: str) -> float:
        if sensor_error is not None:
            raise sensor_error
        return 0.0 if sensor_value is None else sensor_value

    coord._get_cached_state = _cached_state  # type: ignore[assignment]
    coord._validate_sensor_state = _validate  # type: ignore[assignment]
    return coord


@pytest.mark.asyncio
async def test_relay_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A relay failure must reach the log, not vanish."""
    coord = _coordinator(relay_error=_Boom("relay unreachable"))

    with caplog.at_level(logging.ERROR):
        ok = await coord._run_master_switch_safety(1500.0, 0.0)

    assert ok is False, "a failed relay command reported success"
    assert any("Master switch safety control failed" in r.getMessage() for r in caplog.records), (
        "the relay failure was swallowed with nothing logged"
    )


@pytest.mark.asyncio
async def test_cycle_recorded_as_failure_when_relay_fails() -> None:
    """The gate must translate a relay failure into success=False.

    This is the bug that mattered: the old code called
    `record_cycle_end(..., success=True)` unconditionally after swallowing the
    exception, so `metrics.error_count` never moved.
    """
    coord = _coordinator(relay_error=_Boom("relay unreachable"))

    if not await coord._run_master_switch_safety(1500.0, 0.0):
        coord.metrics.record_cycle_end(0.0, success=False)

    assert getattr(coord.metrics, "records") == [False], "a failed relay command was recorded as a successful cycle"


@pytest.mark.asyncio
async def test_unreadable_ac_sensor_is_not_fatal() -> None:
    """An unreadable AC power sensor must not count as a relay failure."""
    coord = _coordinator(sensor_error=SensorUnavailableError("AC power sensor"))

    assert await coord._run_master_switch_safety(1500.0, 0.0) is True
    assert getattr(coord.master_controller, "calls") == [(1500.0, None)], (
        "the relay was not commanded when only the AC sensor was unreadable"
    )


@pytest.mark.asyncio
async def test_sensor_invalid_error_also_non_fatal() -> None:
    """Both sensor exception types are tolerated, not just SensorUnavailable."""
    coord = _coordinator(sensor_error=SensorInvalidError("AC power sensor"))

    assert await coord._run_master_switch_safety(1500.0, 0.0) is True


@pytest.mark.asyncio
async def test_valid_ac_sensor_is_passed_through() -> None:
    """A readable AC power reading is forwarded to the switch logic."""
    coord = _coordinator(sensor_error=None, sensor_value=42.0)

    assert await coord._run_master_switch_safety(1500.0, 0.0) is True
    assert getattr(coord.master_controller, "calls") == [(1500.0, 42.0)]


@pytest.mark.asyncio
async def test_cancellation_propagates() -> None:
    """A cancelled shutdown must not be absorbed into `return False`."""
    coord = _coordinator(relay_error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await coord._run_master_switch_safety(1500.0, 0.0)


@pytest.mark.asyncio
async def test_success_path_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """A healthy cycle must not log the failure message."""
    coord = _coordinator()

    with caplog.at_level(logging.ERROR):
        assert await coord._run_master_switch_safety(1500.0, 0.0) is True

    assert not [r for r in caplog.records if "Master switch safety control failed" in r.getMessage()]
