import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONST_FILE = ROOT / "custom_components" / "solar_ac_controller" / "const.py"
STRINGS = ROOT / "custom_components" / "solar_ac_controller" / "strings.json"
TRANSL = ROOT / "custom_components" / "solar_ac_controller" / "translations" / "en.json"

const_text = CONST_FILE.read_text()
conf_names = re.findall(r"CONF_[A-Z0-9_]+", const_text)
conf_names = sorted(set(conf_names))


def load_keys(js_path):
    data = json.loads(js_path.read_text())
    keys = set()
    # traverse config.step.* and options.step.* for 'data' and 'data_description'
    for section in ("config", "options"):
        sec = data.get(section, {})
        steps = sec.get("step", {})
        for step, name in steps.items():
            d = steps[step].get("data", {})
            dd = steps[step].get("data_description", {})
            keys.update(d.keys())
            keys.update(dd.keys())
    # also errors
    errs = data.get("config", {}).get("step", {}).get("user", {}).get("error", {})
    keys.update(errs.keys())
    return keys


strings_keys = load_keys(STRINGS)
trans_keys = load_keys(TRANSL)
all_keys = strings_keys.union(trans_keys)

missing = []
for c in conf_names:
    candidate = c.lower().replace("conf_", "")
    candidates = {candidate}
    # common variants
    candidates.add(candidate.replace("_temp_", "_temperature_"))
    candidates.add(candidate.replace("_ac_", "ac_"))
    # some keys use 'enable_temperature_modulation'
    candidates.add(candidate.replace("_enable_temp_", "enable_temperature_"))
    found = False
    for cand in candidates:
        if cand in all_keys:
            found = True
            break
    if not found:
        missing.append((c, sorted(candidates)))

print("Found CONF_* constants:")
for c in conf_names:
    print("  ", c)
print()
if not missing:
    print("All CONF_* constants have translation keys (heuristic check).")
else:
    print("Missing translation keys for the following CONF_* constants (candidates):")
    for c, cand in missing:
        print(" -", c, "->", ", ".join(cand))

# Exit with non-zero if missing
if missing:
    raise SystemExit(1)
else:
    raise SystemExit(0)
