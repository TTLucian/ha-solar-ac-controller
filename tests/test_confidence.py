from homeassistant.util import dt as dt_util

from custom_components.solar_ac_controller.decisions import DecisionEngine


class FakeCoordinator:
    def __init__(self):
        self.panic_threshold = 2000.0
        # Simulate a very recent zone change to trigger short-cycle penalty
        now = dt_util.utcnow().timestamp()
        self.zone_last_changed = {"climate.guest": now}
        self.zone_last_changed_type = {"climate.guest": "on"}
        self.short_cycle_on_seconds = 1200
        self.short_cycle_off_seconds = 20
        self.samples = 0
        self.aggressiveness = 0.5
        self.season_mode = "heat"
        self.initial_learned_power = 1000.0

    def get_learned_power(self, zone_short, season):
        return 1500.0  # fake value


def test_remove_confidence_is_non_negative_when_penalized():
    coord = FakeCoordinator()
    engine = DecisionEngine(coord)  # type: ignore[arg-type]

    # Negative import power (exporting) should produce base==0, offset applies,
    # but short-cycle penalty may drive the raw value negative. After change,
    # compute_remove_conf must return >= 0.
    remove_conf = engine.compute_remove_conf(
        import_power=-495.26, last_zone="climate.guest"
    )
    assert remove_conf >= 0
    assert remove_conf == 0


def test_add_confidence_zero_when_required_export_missing():
    coord = FakeCoordinator()
    engine = DecisionEngine(coord)  # type: ignore[arg-type]

    add_conf = engine.compute_add_conf(export=0.0, required_export=None, last_zone=None)
    assert add_conf == 0.0


def test_sample_bonus_ramp_below_required_export():
    """Sample bonus must ramp down smoothly when export is below required, not cliff at zero."""
    # No short-cycle, no zone changes
    coord_below = FakeCoordinator()
    coord_below.zone_last_changed = {}
    coord_below.zone_last_changed_type = {}
    coord_below.samples = 10  # enough samples for a full bonus

    coord_just_above = FakeCoordinator()
    coord_just_above.zone_last_changed = {}
    coord_just_above.zone_last_changed_type = {}
    coord_just_above.samples = 10

    engine_below = DecisionEngine(coord_below)  # type: ignore[arg-type]
    engine_above = DecisionEngine(coord_just_above)  # type: ignore[arg-type]

    required = 1000.0
    # 50 W below required → export_margin = -50, ramp factor = 0.5
    conf_below = engine_below.compute_add_conf(
        export=950.0, required_export=required, last_zone=None
    )
    # 1 W above required → export_margin = +1, ramp factor = 1.0
    conf_above = engine_above.compute_add_conf(
        export=1001.0, required_export=required, last_zone=None
    )

    # With the old binary gate conf_below would have had zero sample bonus;
    # with the ramp it should be strictly between 0 and conf_above.
    assert conf_below > 0, (
        "sample bonus should be partial, not zero, when slightly below required"
    )
    assert conf_below < conf_above, "partial ramp should give less than full bonus"
