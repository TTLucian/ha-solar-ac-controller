
<details>
<summary>Example Lovelace Dashboard</summary>

<img src="https://user-images.githubusercontent.com/example/lovelace-dashboard.png" alt="Lovelace Dashboard Example" width="600"/>

</details>
<p align="center">
  <img src="https://img.shields.io/github/v/release/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/github/license/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/validate.yml?style=for-the-badge" />
</p>

# ⚡ Quick Start

1. Install via HACS or copy to custom_components.
2. Restart Home Assistant.
3. Go to Settings → Devices & Services → Add Integration → Solar AC Controller.
4. Select your solar, grid, and AC sensors, and zones.
5. Save and enjoy solar-optimized AC automation!

## 📝 Example Configuration (UI)

<details>
<summary>Example: Options Flow</summary>

<img src="https://user-images.githubusercontent.com/example/options-flow.png" alt="Options Flow Example" width="600"/>

Or YAML (for reference):

```yaml
solar_sensor: sensor.solar_power
grid_sensor: sensor.grid_power
ac_power_sensor: sensor.ac_power
ac_switch: switch.ac_master
zones:
  - climate.living_room
  - climate.bedroom
solar_threshold_on: 1200
solar_threshold_off: 800
panic_threshold: 1500
panic_delay: 30
initial_learned_power: 1200
add_confidence: 25
remove_confidence: 10
manual_lock_seconds: 1200
short_cycle_on_seconds: 1200
short_cycle_off_seconds: 1200
action_delay_seconds: 3
enable_diagnostics_sensor: false
```
</details>

## 🛠️ Extensibility

Supported zone domains are now configurable! By default, you can use `climate`, `switch`, or `fan` entities as zones. To add more, edit the integration’s config flow or open a feature request.

## ❓ Troubleshooting & FAQ

**Why do I see two devices?**
  - This can happen if device names or identifiers were inconsistent in a previous version. Remove old devices and reload the integration after upgrading.

**How do I reset learning?**
  - Use the `solar_ac_controller.reset_learning` service from Developer Tools → Services.

**What if my sensors are unavailable?**
  - The controller will skip cycles and log a warning. Check your sensor states in Developer Tools.

**How do I enable diagnostics?**
  - Go to the integration’s options and toggle “Enable Diagnostics Sensor.”

## 🧪 Testing & CI

Automated tests are recommended! Run with:

```bash
pytest
```

Lint and format with:

```bash
black .
pylint custom_components/solar_ac_controller
```

CI status: ![GitHub Actions](https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/validate.yml?style=flat-square)

## 🤝 Contributing

Contributions are welcome! Please:

- Format code with `black`
- Lint with `pylint`
- Add/expand tests if possible
- Document any storage migrations in code and commit messages

Open issues or pull requests on [GitHub](https://github.com/TTLucian/ha-solar-ac-controller/issues).

## 🔗 Links & Resources

- [Home Assistant Docs](https://www.home-assistant.io/integrations/)
- [GitHub Issues](https://github.com/TTLucian/ha-solar-ac-controller/issues)
- [Discussions/Help](https://github.com/TTLucian/ha-solar-ac-controller/discussions)

## 🌍 Localization

All user-facing strings are translatable. To contribute a translation, submit a PR editing `translations/en.json` or add a new language file.

## 🗃️ Versioning & Upgrades

Storage migrations are handled automatically. When upgrading, check the release notes for any migration steps. See code comments in `__init__.py` for details.

## 🖼️ Visuals

# 🌞 Solar AC Controller — Home Assistant Integration

A smart, adaptive controller that manages multi‑zone AC systems based on solar production, grid import/export, and learned compressor behavior.

This integration automatically:

- Turns AC zones on/off based on available solar power  
- Learns each zone’s compressor delta (W)  
- Avoids short‑cycling  
- Detects manual overrides  
- Performs panic shedding when grid import spikes  
- Exposes full diagnostics and observability sensors  
- Provides a complete Options Flow for reconfiguration  

Designed for high‑performance, solar‑aware HVAC automation.

---

## 🚀 Features

### 🌞 Solar‑aware zone control  
Zones activate in priority order based on real‑time solar export and grid import.

### 🧠 Adaptive learning engine  
Each zone’s compressor delta is learned using a bootstrap + EMA model, improving accuracy over time.

### 🔒 Manual override detection  
If a user manually changes a zone, the controller locks that zone for a configurable period.

### 🆘 Panic shedding  
If grid import exceeds a threshold, the controller safely shuts down zones to protect the inverter and installation.

### 📊 Full observability  
The integration exposes a rich set of sensors and diagnostics so you can see exactly what the controller is doing and why.

---

## 📡 Exposed Entities

### **Sensors**
- Active zones  
- Next zone  
- Last zone  
- Last action  
- EMA 30s  
- EMA 5m  
- Add confidence  
- Remove confidence  
- Required export  
- Export margin  
- Import power  
- Learned compressor power per zone  
- Master‑off timestamp  
- Last panic timestamp  
- Panic cooldown active  

### **Binary Sensors**
- Learning active  
- Panic state  
- Panic cooldown  
- Short‑cycling  
- Manual lock active  
- Exporting  
- Importing  
- Master switch OFF  

## 🔍 Diagnostics
The Solar AC Controller provides a unified diagnostics system designed to help with troubleshooting, performance tuning, and understanding the controller’s internal decision engine. Diagnostics are available in two complementary forms:

### 🧠 Diagnostics Sensor (Optional)
   You can enable an always‑on diagnostics sensor that exposes the controller’s full internal state as JSON attributes.

   How to enable
   Go to Settings → Devices & Services → Solar AC Controller → Configure

   Toggle Enable Diagnostics Sensor

   Submit the form

   When enabled, Home Assistant will create:

   ```Code
   sensor.solar_ac_diagnostics
   ```
   This entity updates in real time and includes:
   - Controller configuration
   - Learned power values
   - EMA metrics (30s and 5m)
   - Active and last‑used zones
   - Decision engine state (next zone, last action, required export, margin)
   - Panic thresholds and cooldown state
   - Master switch lockout timers
   - Timestamps and runtime counters
   
   This sensor is intended for advanced users, debugging, and Lovelace dashboards.

### 📄 Home Assistant Diagnostics Export (Always Available)
  Even if the diagnostics sensor is disabled, you can always download a full diagnostics report:

  Settings → Devices & Services → Solar AC Controller → Download Diagnostics

  This export contains the same structured data as the diagnostics sensor, generated through the same internal helper. It includes:

  - Timestamp
  - Full configuration (merged data + options)
  - Learning state and samples
  - EMA values
  - Decision engine outputs
  - Zone activity and lockouts
  - Panic state
  - Master switch state

  No personal or sensitive data is included.

### 🧩 Unified Diagnostics Architecture
  Both the diagnostics sensor and the HA diagnostics export use the same internal function:

```Code
build_diagnostics(coordinator)
```
This ensures:
- Identical data in both places
- No duplication of logic
- No risk of the two drifting apart
- Zero dependency between the sensor and the export

Disabling the diagnostics sensor does not affect the JSON diagnostics export.

### 🔐 Privacy
The diagnostics system intentionally excludes:
- User identity
- Location
- Energy usage history
- Any personally identifiable information
Only integration configuration and runtime controller state are included.


---

## ⚙️ Configuration

### Initial Setup
Add the integration via:

`Settings → Devices & Services → Add Integration → Solar AC Controller`

You will configure:

- Solar sensor
- Grid sensor
- AC power sensor
- Master AC switch (optional)
- Zones (multi‑select)
- Solar ON threshold
- Solar OFF threshold
- Panic threshold
- Panic delay
- Initial learned power
- Confidence thresholds

---

## 🛠 Runtime Options (Options Flow)

All key behavioral parameters can be changed at runtime:

- `manual_lock_seconds` — Duration (in seconds) for which a zone is locked after a manual user override. During this time, the controller will not modify that zone.
- `short_cycle_on_seconds` — Minimum time a zone must remain ON before the controller is allowed to turn it OFF. Prevents compressor short‑cycling.
- `short_cycle_off_seconds` — Minimum time a zone must remain OFF before the controller is allowed to turn it ON. Also protects against short‑cycling.
- `action_delay_seconds` — Delay between Home Assistant service calls when switching zones. Ensures clean sequencing and avoids overwhelming devices.
- `panic_threshold` — Grid import level (in watts) that triggers panic shedding. If import exceeds this threshold, zones are shut down to protect the inverter.
- `panic_delay` — How long the panic condition must persist before shedding begins. Prevents false positives during brief spikes.
- `solar_threshold_on` - The minimum solar production (in watts) required before the controller is allowed to turn ON the master AC switch. When solar stays above this value, the system considers solar “strong enough” to run AC.
- `solar_threshold_off` - The solar production level (in watts) below which the controller will schedule a master AC shutdown. This forms the lower half of the solar hysteresis band and prevents rapid toggling during cloud fluctuations.
- `add_confidence` - The minimum confidence score required before the controller is allowed to add (enable) the next zone.Confidence is derived from export margin, learned compressor power, EMA trends, and short‑cycle penalties.
- `remove_confidence` - The negative confidence threshold that triggers zone removal (turning off the last active zone). High import, low export, or short‑cycling conditions increase the likelihood of removal.
- `initial_learned_power` - The starting estimate (in watts) for each zone’s compressor delta before the learning engine has collected enough samples. This value is used during the bootstrap phase and gradually replaced with learned data.

Changes apply immediately without restarting the integration.

---

## 🧩 Services

### - `solar_ac_controller.reset_learning`
Cancels any active learning cycle and resets runtime learning state.

### - `solar_ac_controller.force_relearn`
Forces relearning for:

- A specific zone (`zone:` provided)  
- All zones (no `zone:` provided)  

**Example (single zone):**
```yaml
service: solar_ac_controller.force_relearn
data:
  zone: climate.living_room
```
Example (all zones):

```yaml
service: solar_ac_controller.force_relearn
data: {}
```


## 🧪 Recommended Tests
Add tests/ with coverage for:

Coordinator
Add/no‑add logic

Remove/no‑remove logic

Panic path

Learning timeout

Master‑off behavior

Panic cooldown

Controller
Bootstrap learning

EMA updates

Abort conditions (manual lock, missing sensors, invalid values)

## 📦 Installation
Manual Installation
Copy custom_components/solar_ac_controller into your HA config directory.

Restart Home Assistant.

Add the integration via the UI.

HACS (Custom Repository)
Open HACS → Integrations

Three‑dot menu → Custom repositories

Add:

```Code
https://github.com/TTLucian/ha-solar-ac-controller
Category: Integration
```
Install

Restart Home Assistant

Add the integration via the UI

Or click this:

[![Add Solar AC Controller to HACS](https://img.shields.io/badge/HACS-Add%20Solar%20AC%20Controller-blue?style=for-the-badge)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TTLucian&repository=ha-solar-ac-controller&category=integration)

then this:

[![Add Solar AC Controller Integration](https://img.shields.io/badge/Home%20Assistant-Add%20Integration-blue?style=for-the-badge&logo=homeassistant)](https://my.home-assistant.io/redirect/config_flow_start?domain=solar_ac_controller)


## 🙌 Credits
Created by @TTLucian.
Designed for high‑performance, solar‑aware HVAC automation with strong observability.
