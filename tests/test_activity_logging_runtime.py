from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.metrics import MetricsCollector


class MockBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_name, data):
        self.events.append((event_name, data))


class MockHass:
    def __init__(self):
        self.bus = MockBus()


@pytest.mark.asyncio
async def test_log_emits_logbook_when_enabled_and_system_log_level(caplog):
    coord = object.__new__(SolarACCoordinator)
    coord.hass = MockHass()
    coord.config_entry = SimpleNamespace(entry_id="test_entry")
    coord.metrics = cast(MetricsCollector, SimpleNamespace(cycle_count=2))
    coord._last_logbook_emit = {}
    from custom_components.solar_ac_controller.const import LOGBOOK_THROTTLE_SECONDS

    coord._logbook_throttle_seconds = LOGBOOK_THROTTLE_SECONDS

    # Capture logs at DEBUG so all levels are visible
    caplog.set_level("DEBUG")

    # Case 1: activity logging disabled -> no logbook event, but system log emitted
    coord.activity_logging_enabled = False
    await coord._log("Test message disabled", "info")
    assert any("Test message disabled" in r.message for r in caplog.records)
    assert coord.hass.bus.events == []

    # Case 2: activity logging enabled -> logbook event fired and system log emitted
    coord.hass.bus.events.clear()
    caplog.clear()

    coord.activity_logging_enabled = True
    await coord._log("Test message enabled", "warning")

    # System log should contain the message
    assert any("Test message enabled" in r.message for r in caplog.records)

    # Logbook event should have been fired once
    assert len(coord.hass.bus.events) == 1
    ev_name, ev_data = coord.hass.bus.events[0]
    assert ev_name == "logbook_entry"
    assert "Test message enabled" in ev_data.get("message", "")
    # Level mapping should be present
    assert ev_data.get("level") in ("WARNING", "INFO", "ERROR", "DEBUG")
