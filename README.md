<p align="center">
  <img src="https://img.shields.io/github/v/release/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/github/license/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/validate.yml?style=for-the-badge" />
</p>

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

### **Diagnostic Entity**
A single entity (`Solar AC Diagnostics`) exposing the entire controller brain:

- Config  
- Active zones  
- Learning state  
- Learned power  
- EMA values  
- Zone last‑changed timestamps  
- Manual lock timers  
- Panic configuration  
- Required export  
- Export margin  
- Next/last zone  
- Master‑off timestamp  
- Panic cooldown state  
- Last action  

### **Home Assistant Diagnostics Export**
A dedicated diagnostics handler (`diagnostics.py`) provides a JSON‑safe snapshot of all internal state:

`Settings → Devices & Services → Solar AC Controller → Diagnostics`

Perfect for debugging or attaching to GitHub issues.

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

### `solar_ac_controller.reset_learning`
Cancels any active learning cycle and resets runtime learning state.

### `solar_ac_controller.force_relearn`
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
