"""Tests for the 9 core control paths that drive production decisions.

Each test exercises a real production code path end-to-end using minimal
FakeCoordinator stubs — no real Home Assistant instance required.

Paths covered
-------------
1.  Zone add:   confidence ≥ threshold  → should_add_zone returns True
2.  Zone remove: confidence ≤ threshold → should_remove_zone returns True
3.  Short-cycle: recent toggle blocks add/remove via penalty score
4.  Panic trigger: ema_30s > panic_threshold + on_count > 0 → should_panic
5.  Learning session: start → feed readings → peak detected → stabilise
6.  Learning contamination: other zone changed during session → flag set
7.  Master switch ON: solar ≥ on_threshold + switch off → turn_on called
8.  Master switch OFF: solar ≤ off_threshold + switch on → turn_off called
9.  Sensor unavailable: None/unavailable state → SensorUnavailableError raised
10. Manual lock: zone locked in future → is_locked returns True; expired → False
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.solar_ac_controller.controller import LearningSession
from custom_components.solar_ac_controller.decisions import DecisionEngine
from custom_components.solar_ac_controller.exceptions import (
    SensorInvalidError,
    SensorUnavailableError,
)
from custom_components.solar_ac_controller.helpers import MasterSwitchController
from custom_components.solar_ac_controller.panic import PanicManager
from custom_components.solar_ac_controller.zones import ZoneManager

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _state(value: str, attrs: dict | None = None) -> SimpleNamespace:
    """Build a minimal HA state object."""
    return SimpleNamespace(
        state=value,
        attributes=attrs or {},
        context=SimpleNamespace(id="ctx-0", parent_id=None),
    )


def _decision_coordinator(
    *,
    confidence: float = 0.0,
    add_threshold: float = 50.0,
    remove_threshold: float = 0.0,
    aggressiveness: float = 0.5,
    samples: int = 5,
    ema_30s: float = 0.0,
    ema_5m: float = 0.0,
    learning_active: bool = False,
    initial_learned_power: float = 1000.0,
    season_mode: str = "heat",
    zone_last_changed: dict | None = None,
    zone_last_changed_type: dict | None = None,
    short_cycle_on: int = 1200,
    short_cycle_off: int = 20,
    learned_power: float = 1500.0,
) -> SimpleNamespace:
    coord = SimpleNamespace()
    coord.confidence = confidence
    coord.unified_add_threshold = add_threshold
    coord.unified_remove_threshold = remove_threshold
    coord.aggressiveness = aggressiveness
    coord.samples = samples
    coord.ema_30s = ema_30s
    coord.ema_5m = ema_5m
    coord.learning_active_cached = learning_active
    coord.initial_learned_power = initial_learned_power
    coord.season_mode = season_mode
    coord.zone_last_changed = zone_last_changed or {}
    coord.zone_last_changed_type = zone_last_changed_type or {}
    coord.short_cycle_on_seconds = short_cycle_on
    coord.short_cycle_off_seconds = short_cycle_off
    coord.compressor_recover_until = 0.0
    coord.compressor_ramp_seconds = 60
    coord.get_learned_power = MagicMock(return_value=learned_power)
    return coord


# ---------------------------------------------------------------------------
# 1 & 2. ADD / REMOVE decision gate (threshold comparison)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_add_zone_when_confidence_meets_threshold():
    """confidence ≥ add_threshold → should_add_zone is True."""
    coord = _decision_coordinator(confidence=60.0, add_threshold=50.0)
    engine = DecisionEngine(coord)  # type: ignore[arg-type]
    assert await engine.should_add_zone("climate.z1", 1000.0) is True


@pytest.mark.asyncio
async def test_should_not_add_zone_when_confidence_below_threshold():
    """confidence < add_threshold → should_add_zone is False."""
    coord = _decision_coordinator(confidence=49.9, add_threshold=50.0)
    engine = DecisionEngine(coord)  # type: ignore[arg-type]
    assert await engine.should_add_zone("climate.z1", 1000.0) is False


@pytest.mark.asyncio
async def test_should_remove_zone_when_confidence_below_threshold():
    """confidence ≤ remove_threshold → should_remove_zone is True."""
    coord = _decision_coordinator(confidence=-5.0, remove_threshold=0.0)
    engine = DecisionEngine(coord)  # type: ignore[arg-type]
    assert await engine.should_remove_zone("climate.z1", 800.0, ["climate.z1"]) is True


@pytest.mark.asyncio
async def test_should_not_remove_zone_when_confidence_above_threshold():
    """confidence > remove_threshold → should_remove_zone is False."""
    coord = _decision_coordinator(confidence=10.0, remove_threshold=0.0)
    engine = DecisionEngine(coord)  # type: ignore[arg-type]
    assert await engine.should_remove_zone("climate.z1", 100.0, ["climate.z1"]) is False


# ---------------------------------------------------------------------------
# 3. Short-cycle protection (blocks add/remove via confidence penalty)
# ---------------------------------------------------------------------------


def _ts_ago(seconds: float) -> float:
    """Return a wall-clock timestamp some seconds before now."""
    from homeassistant.util import dt as dt_util

    return dt_util.utcnow().timestamp() - seconds


def test_short_cycle_penalty_applied_when_zone_recently_turned_on():
    """compute_add_conf must return a lower score when the zone just turned on."""
    # Short-cycle scenario: last "on" was 30 s ago, threshold is 1200 s
    zone = "climate.z1"
    coord_sc = _decision_coordinator(
        confidence=0.0,
        ema_30s=-2000.0,  # large export (grid negative = exporting)
        ema_5m=-2000.0,
        samples=10,
        zone_last_changed={zone: _ts_ago(30)},
        zone_last_changed_type={zone: "on"},
        short_cycle_on=1200,
    )
    coord_clean = _decision_coordinator(
        confidence=0.0,
        ema_30s=-2000.0,
        ema_5m=-2000.0,
        samples=10,
        # last change was long ago — no short-cycle penalty
        zone_last_changed={zone: _ts_ago(3600)},
        zone_last_changed_type={zone: "on"},
        short_cycle_on=1200,
    )
    engine_sc = DecisionEngine(coord_sc)  # type: ignore[arg-type]
    engine_clean = DecisionEngine(coord_clean)  # type: ignore[arg-type]

    conf_with_sc = engine_sc.compute_add_conf(
        export=2000.0, required_export=1000.0, last_zone=zone
    )
    conf_without_sc = engine_clean.compute_add_conf(
        export=2000.0, required_export=1000.0, last_zone=zone
    )

    # Short-cycle protection reduces the score
    assert (
        conf_with_sc < conf_without_sc
    ), f"Expected sc penalty: {conf_with_sc:.2f} < {conf_without_sc:.2f}"


def test_short_cycle_penalty_applied_when_zone_recently_turned_off():
    """compute_remove_conf must return a lower score when the zone just turned off."""
    zone = "climate.z1"
    coord_sc = _decision_coordinator(
        zone_last_changed={zone: _ts_ago(5)},
        zone_last_changed_type={zone: "off"},
        short_cycle_off=20,
    )
    coord_clean = _decision_coordinator(
        zone_last_changed={zone: _ts_ago(3600)},
        zone_last_changed_type={zone: "off"},
        short_cycle_off=20,
    )
    engine_sc = DecisionEngine(coord_sc)  # type: ignore[arg-type]
    engine_clean = DecisionEngine(coord_clean)  # type: ignore[arg-type]

    conf_with_sc = engine_sc.compute_remove_conf(import_power=1500.0, last_zone=zone)
    conf_without_sc = engine_clean.compute_remove_conf(
        import_power=1500.0, last_zone=zone
    )

    assert (
        conf_with_sc < conf_without_sc
    ), f"Expected sc penalty: {conf_with_sc:.2f} < {conf_without_sc:.2f}"


# ---------------------------------------------------------------------------
# 4. Panic trigger
# ---------------------------------------------------------------------------


def _panic_coordinator(
    ema_30s: float = 0.0,
    panic_threshold: float = 2000.0,
    on_count: int = 1,
    last_panic_ts: float | None = None,
) -> Any:
    coord = SimpleNamespace()
    coord.ema_30s = ema_30s
    coord.panic_threshold = panic_threshold
    coord.on_count = on_count
    coord.last_panic_ts = last_panic_ts
    coord._panic_task = None
    coord._panic_active = False
    coord._state_lock = asyncio.Lock()
    coord.create_background_task = MagicMock(
        side_effect=lambda coro: asyncio.ensure_future(coro)
    )
    coord._log = AsyncMock()
    coord.last_action = "balanced"
    coord.active_zones = ["climate.z1"]
    return coord


def test_should_panic_when_ema_exceeds_threshold():
    """ema_30s > panic_threshold + at least one zone on → should_panic is True."""
    coord = _panic_coordinator(ema_30s=3000.0, panic_threshold=2000.0, on_count=1)
    mgr = PanicManager(coord)  # type: ignore[arg-type]
    assert mgr.should_panic is True


def test_should_not_panic_below_threshold():
    """ema_30s ≤ panic_threshold → should_panic is False."""
    coord = _panic_coordinator(ema_30s=1999.0, panic_threshold=2000.0, on_count=1)
    mgr = PanicManager(coord)  # type: ignore[arg-type]
    assert mgr.should_panic is False


def test_should_not_panic_when_no_zones_on():
    """on_count == 0 → should_panic is False even if ema exceeds threshold."""
    coord = _panic_coordinator(ema_30s=5000.0, panic_threshold=2000.0, on_count=0)
    mgr = PanicManager(coord)  # type: ignore[arg-type]
    assert mgr.should_panic is False


@pytest.mark.asyncio
async def test_panic_schedule_creates_task():
    """schedule_panic must create a background task."""
    coord = _panic_coordinator(ema_30s=3000.0, panic_threshold=2000.0)
    # Intercept task creation to prevent actual coroutine from running
    tasks_created = []

    def capture_task(coro):
        task = asyncio.ensure_future(coro)
        tasks_created.append(task)
        return task

    coord.create_background_task = capture_task
    coord.panic_delay = 0
    coord.ema_5m = 3000.0
    coord.last_panic_ts = None
    coord.action_delay_seconds = 0
    coord.integration_enabled = True
    coord.config = {}

    # Stub controller.session
    coord.controller = SimpleNamespace(
        _reset_learning_state_async=AsyncMock(),
        session=SimpleNamespace(notify_zone_changed_during_learning=AsyncMock()),
    )

    # Stub action_executor
    coord.action_executor = SimpleNamespace(
        call_entity_service=AsyncMock(),
    )

    # Stub hass.states
    coord.hass = SimpleNamespace(
        states=SimpleNamespace(get=MagicMock(return_value=None))
    )

    mgr = PanicManager(coord)  # type: ignore[arg-type]
    await mgr.schedule_panic(["climate.z1"])

    assert len(tasks_created) == 1, "Expected one background task to be created"

    # Clean up running tasks
    for t in tasks_created:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# 5. Learning session: phase detection lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learning_session_detects_peak():
    """After a rise-then-fall in power readings, peak is detected."""
    session = LearningSession()
    import time

    now = time.time()
    await session.start_session("climate.z1", now)

    # Simulate rising power (compressor spin-up)
    for w in [100, 500, 900, 1400, 1800, 2000]:
        await session.add_power_reading(float(w))

    # Peak is set as soon as a new high is seen
    peak = await session.get_peak_power()
    assert peak == pytest.approx(2000.0), f"Expected peak 2000W, got {peak}"
    assert session._peak_detected is True


@pytest.mark.asyncio
async def test_learning_session_detects_stabilization():
    """After STABILIZATION_READING_COUNT stable readings, stabilized_power is set."""
    from custom_components.solar_ac_controller.const import STABILIZATION_READING_COUNT

    session = LearningSession()
    import time

    now = time.time()
    await session.start_session("climate.z1", now)

    # Enough stable readings at ~1500 W (< 5% variation)
    for _ in range(STABILIZATION_READING_COUNT + 2):
        await session.add_power_reading(1500.0 + ((_ % 3) * 10))  # ±10 W

    assert session._stabilized_detected is True
    # Readings cycle through 1500, 1510, 1520 repeatedly; mean = 1510
    assert session._stabilized_power == pytest.approx(1510.0, abs=5.0)


@pytest.mark.asyncio
async def test_learning_session_end_clears_state():
    """end_session must clear all phase-detection state."""
    session = LearningSession()
    import time

    await session.start_session("climate.z1", time.time())
    await session.add_power_reading(1500.0)
    assert await session.is_active() is True

    await session.end_session()
    assert await session.is_active() is False
    assert await session.get_zone() is None
    assert session._peak_detected is False
    assert session._stabilized_detected is False


# ---------------------------------------------------------------------------
# 6. Learning contamination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_learning_contamination_flagged_when_other_zone_added():
    """Adding a different zone during learning marks the session contaminated."""
    session = LearningSession()
    import time

    await session.start_session("climate.z1", time.time())
    assert await session.is_learning_contaminated() is False

    await session.notify_zone_changed_during_learning("climate.z2", "add")
    assert await session.is_learning_contaminated() is True


@pytest.mark.asyncio
async def test_learning_contamination_not_flagged_for_same_zone():
    """Notification for the learning zone itself does not set contamination."""
    session = LearningSession()
    import time

    await session.start_session("climate.z1", time.time())
    # Same zone should not count as contamination
    await session.notify_zone_changed_during_learning("climate.z1", "add")
    assert await session.is_learning_contaminated() is False


# ---------------------------------------------------------------------------
# 7 & 8. Master switch ON / OFF (hysteresis)
# ---------------------------------------------------------------------------


def _master_coordinator(
    *,
    ac_switch: str = "switch.ac_master",
    switch_state: str = "off",
    solar_on: float = 1200.0,
    solar_off: float = 500.0,
) -> Any:
    coord = SimpleNamespace()
    coord._state_lock = asyncio.Lock()
    coord.last_action = "balanced"
    coord.master_last_state = None
    coord.master_last_action_time = None
    coord.master_manual_lock_state = None
    coord.master_commanded_state = None
    coord.master_last_command_time = 0.0
    coord.master_off_since = None
    coord._log = AsyncMock()

    # config_manager stub
    coord.config_manager = SimpleNamespace(
        get=MagicMock(
            side_effect=lambda k, *a: ac_switch if k == "ac_switch" else None
        ),
        get_float=MagicMock(
            side_effect=lambda k, default: (
                solar_on if "on" in k else (solar_off if "off" in k else default)
            )
        ),
        get_list=MagicMock(return_value=[]),  # no zones → safe to cut power
    )

    # hass stub
    state_obj = _state(switch_state)
    coord.hass = SimpleNamespace(
        states=SimpleNamespace(get=MagicMock(return_value=state_obj)),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    return coord


@pytest.mark.asyncio
async def test_master_switch_turns_on_when_solar_above_threshold():
    """When solar ≥ on_threshold and switch is off, turn_on must be called."""
    coord = _master_coordinator(switch_state="off", solar_on=1200.0, solar_off=500.0)
    ctrl = MasterSwitchController(coord)  # type: ignore[arg-type]

    await ctrl.handle_master_switch(solar=1500.0, cycle_start=0)

    coord.hass.services.async_call.assert_awaited_once()
    _, call_args, call_kwargs = coord.hass.services.async_call.mock_calls[0]
    domain, service = call_args[0], call_args[1]
    assert domain == "switch"
    assert service == "turn_on"


@pytest.mark.asyncio
async def test_master_switch_turns_off_when_solar_below_threshold():
    """When solar ≤ off_threshold and switch is on, turn_off must be called."""
    coord = _master_coordinator(switch_state="on", solar_on=1200.0, solar_off=500.0)
    # configure the off_threshold check
    coord.config_manager.get_float = MagicMock(
        side_effect=lambda k, default: 1200.0 if "on" in k else 500.0
    )
    ctrl = MasterSwitchController(coord)  # type: ignore[arg-type]

    await ctrl.handle_master_switch(solar=200.0, cycle_start=0)

    coord.hass.services.async_call.assert_awaited_once()
    _, call_args, _ = coord.hass.services.async_call.mock_calls[0]
    assert call_args[1] == "turn_off"


@pytest.mark.asyncio
async def test_master_switch_no_action_in_hysteresis_band():
    """Solar between off and on threshold with switch already on → no action."""
    # Switch is on, solar is between thresholds → stay put
    coord = _master_coordinator(switch_state="on", solar_on=1200.0, solar_off=500.0)
    ctrl = MasterSwitchController(coord)  # type: ignore[arg-type]

    await ctrl.handle_master_switch(solar=800.0, cycle_start=0)

    coord.hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_master_switch_manual_lock_prevents_auto_control():
    """A manual lock on 'off' must prevent auto turn_on until natural cycle aligns."""
    coord = _master_coordinator(switch_state="off", solar_on=1200.0, solar_off=500.0)
    coord.master_manual_lock_state = "off"  # locked off by user
    ctrl = MasterSwitchController(coord)  # type: ignore[arg-type]

    # Solar is above on_threshold but lock is active — must NOT turn on
    await ctrl.handle_master_switch(solar=1500.0, cycle_start=0)

    coord.hass.services.async_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# 9. Sensor unavailable
# ---------------------------------------------------------------------------


def _sensor_coordinator() -> Any:
    """Minimal coordinator just for _validate_sensor_state tests."""
    from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

    coord = object.__new__(SolarACCoordinator)
    coord._sensor_unavailable_since = {}
    coord.create_background_task = MagicMock()
    return coord


def test_sensor_unavailable_raises_when_state_is_none():
    """`_validate_sensor_state(None, ...)` must raise SensorUnavailableError."""
    from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

    coord = _sensor_coordinator()
    with pytest.raises(SensorUnavailableError):
        SolarACCoordinator._validate_sensor_state(coord, None, "Grid sensor")


def test_sensor_unavailable_raises_when_state_is_unavailable():
    """`state='unavailable'` must raise SensorUnavailableError."""
    from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

    coord = _sensor_coordinator()
    with pytest.raises(SensorUnavailableError):
        SolarACCoordinator._validate_sensor_state(
            coord, _state("unavailable"), "Solar sensor"
        )


def test_sensor_unavailable_raises_when_state_is_unknown():
    """`state='unknown'` must raise SensorUnavailableError."""
    from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

    coord = _sensor_coordinator()
    with pytest.raises(SensorUnavailableError):
        SolarACCoordinator._validate_sensor_state(
            coord, _state("unknown"), "AC power sensor"
        )


def test_sensor_invalid_raises_when_state_is_not_numeric():
    """`state='foobar'` (non-numeric) must raise SensorInvalidError."""
    from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

    coord = _sensor_coordinator()
    with pytest.raises(SensorInvalidError):
        SolarACCoordinator._validate_sensor_state(
            coord, _state("foobar"), "Solar sensor"
        )


def test_sensor_valid_state_returns_float():
    """`state='1234.5'` must return the numeric value."""
    from custom_components.solar_ac_controller.coordinator import SolarACCoordinator

    coord = _sensor_coordinator()
    result = SolarACCoordinator._validate_sensor_state(
        coord, _state("1234.5"), "Solar sensor"
    )
    assert result == pytest.approx(1234.5)


# ---------------------------------------------------------------------------
# 10. Manual lock (zone_manager.is_locked)
# ---------------------------------------------------------------------------


def _lock_coordinator(lock_until: float | None) -> Any:
    """Minimal coordinator for ZoneManager.is_locked tests."""
    coord = SimpleNamespace()
    coord._state_lock = asyncio.Lock()
    coord.zone_manual_lock_until = {}
    if lock_until is not None:
        coord.zone_manual_lock_until["climate.z1"] = lock_until
    coord.create_background_task = MagicMock(
        side_effect=lambda coro: asyncio.ensure_future(coro)
    )
    coord._log = AsyncMock()
    return coord


@pytest.mark.asyncio
async def test_zone_is_locked_when_lock_is_in_future():
    """A future lock timestamp → is_locked returns True."""
    from homeassistant.util import dt as dt_util

    future = dt_util.utcnow().timestamp() + 9999
    coord = _lock_coordinator(lock_until=future)
    mgr = ZoneManager(coord)  # type: ignore[arg-type]
    assert await mgr.is_locked("climate.z1") is True


@pytest.mark.asyncio
async def test_zone_is_not_locked_when_lock_has_expired():
    """An expired lock timestamp → is_locked returns False and removes the entry."""
    from homeassistant.util import dt as dt_util

    past = dt_util.utcnow().timestamp() - 1
    coord = _lock_coordinator(lock_until=past)
    mgr = ZoneManager(coord)  # type: ignore[arg-type]
    assert await mgr.is_locked("climate.z1") is False
    # Lock entry must be removed after expiry
    assert "climate.z1" not in coord.zone_manual_lock_until


@pytest.mark.asyncio
async def test_zone_is_not_locked_when_no_lock_set():
    """No lock entry → is_locked returns False."""
    coord = _lock_coordinator(lock_until=None)
    mgr = ZoneManager(coord)  # type: ignore[arg-type]
    assert await mgr.is_locked("climate.z1") is False
