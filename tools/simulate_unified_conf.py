"""Simulate unified confidence from log lines and show when the add threshold is reached."""

import os
import re
import sys
from datetime import datetime

# Ensure we can import the integration package from the repo root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from custom_components.solar_ac_controller.decisions import DecisionEngine


class FakeCoordinator:
    def __init__(self):
        self.aggressiveness = 0.5
        self.initial_learned_power = 1000.0
        self.samples = 0
        self.season_mode = "heat"
        self.zone_last_changed = {}
        self.zone_last_changed_type = {}
        self.compressor_recover_until = 0.0
        self.compressor_ramp_seconds = 0.0
        self.learning_active_cached = False
        self.ema_30s = 0.0
        self.ema_5m = 0.0
        self.solar_ema_fast = 0.0
        self.solar_ema_slow = 0.0
        self.solar_fraction = 0.0

    def get_learned_power(self, zone_short, season):
        return 1500.0


def parse_float(s: str) -> float:
    try:
        return float(s)
    except Exception:
        return 0.0


# Extract timestamp (used for filtering and reporting)
ts_re = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")

# Extract numeric values from ZONE_CALC lines (order may vary)
export_re = re.compile(r"export=(?P<export>[-0-9.]+)W")
import_re = re.compile(r"import_power=(?P<import>[-0-9.]+)W")
required_re = re.compile(r"required_export=(?P<req>[-0-9.]+)W")

ema_re = re.compile(
    r"\[EMA\].*ema30s=(?P<ema30s>[-0-9.]+) ema5m=(?P<ema5m>[-0-9.]+) .*solar_fast=(?P<fast>[-0-9.]+) solar_slow=(?P<slow>[-0-9.]+) .*solar_fraction=(?P<frac>[-0-9.]+)"
)
samples_re = re.compile(r"samples=(?P<samples>\d+)")

records = []
state = {
    "ema_30s": 0.0,
    "ema_5m": 0.0,
    "solar_fast": 0.0,
    "solar_slow": 0.0,
    "solar_fraction": 0.0,
    "samples": 0,
}

start_ts = datetime.fromisoformat("2026-03-18 08:00:00")
end_ts = datetime.fromisoformat("2026-03-18 10:18:00")


def parse_timestamp(ts_str):
    base, _, _ = ts_str.partition(".")
    return datetime.fromisoformat(base)


with open("log.txt", "r") as f:
    for line in f:
        if "SYSTEM_BALANCED" in line:
            m = samples_re.search(line)
            if m:
                state["samples"] = int(m.group("samples"))
        m = ema_re.search(line)
        if m:
            state["ema_30s"] = parse_float(m.group("ema30s"))
            state["ema_5m"] = parse_float(m.group("ema5m"))
            state["solar_fast"] = parse_float(m.group("fast"))
            state["solar_slow"] = parse_float(m.group("slow"))
            state["solar_fraction"] = parse_float(m.group("frac"))
        # Only consider ZONE_CALC lines
        if "[ZONE_CALC]" not in line:
            continue

        ts_m = ts_re.match(line)
        if not ts_m:
            continue
        ts = parse_timestamp(ts_m.group("ts"))
        if ts < start_ts or ts > end_ts:
            continue

        export_m = export_re.search(line)
        import_m = import_re.search(line)
        required_m = required_re.search(line)
        if not (export_m and import_m and required_m):
            continue

        export = parse_float(export_m.group("export"))
        imp = parse_float(import_m.group("import"))
        req = parse_float(required_m.group("req"))

        last_zone = None
        lm = re.search(r"last_zone=(?P<last>[^\s]+)", line)
        if lm:
            last_zone = lm.group("last")

        records.append(
            {
                "ts": ts,
                "export": export,
                "import_power": imp,
                "required_export": req,
                "last_zone": last_zone,
                "ema_30s": state["ema_30s"],
                "ema_5m": state["ema_5m"],
                "solar_fast": state["solar_fast"],
                "solar_slow": state["solar_slow"],
                "solar_fraction": state["solar_fraction"],
                "samples": state["samples"],
            }
        )

records.sort(key=lambda r: r["ts"])

coordinator = FakeCoordinator()
engine = DecisionEngine(coordinator)
threshold = 80.0 - (60.0 * coordinator.aggressiveness)

first_add = None
lines = []
for rec in records:
    coordinator.ema_30s = rec["ema_30s"]
    coordinator.ema_5m = rec["ema_5m"]
    coordinator.solar_ema_fast = rec["solar_fast"]
    coordinator.solar_ema_slow = rec["solar_slow"]
    coordinator.solar_fraction = rec["solar_fraction"]
    coordinator.samples = rec["samples"]

    add = engine.compute_add_conf(
        export=rec["export"],
        required_export=rec["required_export"],
        last_zone=rec["last_zone"],
    )
    rem = engine.compute_remove_conf(
        import_power=rec["import_power"],
        last_zone=rec["last_zone"],
    )
    unified = add - max(0.0, rem)

    if first_add is None and unified >= threshold:
        first_add = (
            rec["ts"],
            unified,
            add,
            rem,
            rec["export"],
            rec["required_export"],
        )

    if rec["ts"].minute % 10 == 0 and rec["ts"].second < 2:
        lines.append(
            (rec["ts"], unified, add, rem, rec["export"], rec["required_export"])
        )

print(f"Parsed {len(records)} cycles between {start_ts} and {end_ts}.")
print(f"Aggressiveness={coordinator.aggressiveness}, add_threshold={threshold}\n")
if first_add:
    ts, u, a, r, ex, req = first_add
    print(
        f"First time unified_conf >= threshold: {ts} -> unified={u:.2f} (add={a:.2f}, rem={r:.2f}), export={ex}, required={req}"
    )
else:
    print("Unified confidence never reached threshold in this window.")

print("\nSample timeline (every ~10 minutes):")
for ts, u, a, r, ex, req in lines:
    print(
        f"{ts:%H:%M:%S}  unified={u:5.1f} add={a:5.1f} rem={r:5.1f} export={ex:6.0f} required={req:6.0f}"
    )
