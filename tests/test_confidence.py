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
