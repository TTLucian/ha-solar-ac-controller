from __future__ import annotations

from custom_components.solar_ac_controller.coordinator import SolarACCoordinator
from custom_components.solar_ac_controller.decisions import DecisionEngine


class DummyCoordinator(SolarACCoordinator):
    """Minimal coordinator stub for testing DecisionEngine breakdowns."""

    def __init__(self):
        # Do not call parent init; just set attributes used by DecisionEngine
        self.aggressiveness = 0.5
        self.ema_30s = 0.0
        self.ema_5m = 0.0
        self.samples = 0
        self.compressor_recover_until = 0
        self.compressor_ramp_seconds = 0
        self.learning_active_cached = False
        self.panic_threshold = 99999
        self.zone_last_changed = {}
        self.zone_last_changed_type = {}
        self.short_cycle_on_seconds = 1200
        self.short_cycle_off_seconds = 20
        self.unified_add_threshold = 10
        self.unified_remove_threshold = -10
        self.confidence = 0.0

    def get_learned_power(self, zone, season):
        return 1000.0


def test_add_breakdown_present_and_numeric():
    coord = DummyCoordinator()
    engine = DecisionEngine(coord)

    # Ensure breakdown is unset initially
    assert not hasattr(coord, "last_add_breakdown")

    # Call compute_add_conf with valid required_export
    add_conf = engine.compute_add_conf(
        export=2000.0, required_export=1500.0, last_zone=None
    )

    # After call, breakdown should be set and contain numeric values
    assert hasattr(coord, "last_add_breakdown")
    bd = coord.last_add_breakdown
    assert isinstance(bd, dict)
    assert "raw" in bd
    assert isinstance(bd["raw"], (int, float))
    # Returned confidence should match clamped numeric range
    assert 0.0 <= add_conf <= 100.0


def test_remove_breakdown_present_and_numeric():
    coord = DummyCoordinator()
    engine = DecisionEngine(coord)

    # Ensure breakdown is unset initially
    assert not hasattr(coord, "last_remove_breakdown")

    # Call compute_remove_conf with moderate import_power
    rem_conf = engine.compute_remove_conf(import_power=1600.0, last_zone=None)

    assert hasattr(coord, "last_remove_breakdown")
    bd = coord.last_remove_breakdown
    assert isinstance(bd, dict)
    assert "raw" in bd
    assert isinstance(bd["raw"], (int, float))
    assert 0.0 <= rem_conf <= 100.0
