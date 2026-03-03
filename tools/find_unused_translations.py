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
    for section in ("config", "options"):
        sec = data.get(section, {})
        steps = sec.get("step", {})
        for step, name in steps.items():
            d = steps[step].get("data", {})
            dd = steps[step].get("data_description", {})
            keys.update(d.keys())
            keys.update(dd.keys())
    errs = data.get("config", {}).get("step", {}).get("user", {}).get("error", {})
    keys.update(errs.keys())
    return keys


strings_keys = load_keys(STRINGS)
trans_keys = load_keys(TRANSL)
all_keys = strings_keys.union(trans_keys)

# Build set of candidates generated from CONF_*
matched = set()
for c in conf_names:
    candidate = c.lower().replace("conf_", "")
    candidates = {candidate}
    candidates.add(candidate.replace("_temp_", "_temperature_"))
    candidates.add(candidate.replace("_ac_", "ac_"))
    candidates.add(candidate.replace("_enable_temp_", "enable_temperature_"))
    # add also direct mapping
    for cand in candidates:
        if cand in all_keys:
            matched.add(cand)

# Now unused keys are keys in all_keys that are not matched
unused = sorted(k for k in all_keys if k not in matched)

print("All translation keys:", sorted(all_keys))
print()
if unused:
    print("Unused translation keys (not matched to CONF_* candidates):")
    for k in unused:
        print(" -", k)
else:
    print("No unused translation keys found.")
