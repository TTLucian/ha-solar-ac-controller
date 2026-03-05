# 🌞 Solar AC Controller — Home Assistant Integration

<p align="center">
  <img src="https://img.shields.io/github/v/release/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" /></a>
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/ci.yml?style=for-the-badge" />
  <a href="https://buymeacoffee.com/ttlucian"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-Donate-yellow?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee" /></a>
</p>

A smart and adaptive Home Assistant integration that manages multi-zone (multi-split) AC systems based on real-time solar production, grid import/export, and learned compressor behavior.

This integration automatically:

- **Dynamically controls AC zones** based on available solar export and grid conditions
- **Learns each zone's power consumption** using an adaptive EMA (Exponential Moving Average) algorithm
- **Prevents short-cycling** with configurable delays for both ON and OFF transitions
- **Detects manual overrides** and locks zones to respect user control
- **Performs panic shedding** when grid import exceeds configurable thresholds
- **Optional master AC switch control** based on solar production thresholds
- **Comfort-aware zone control** with per-zone temperature sensors for intelligent removal prioritization
- **Exposes comprehensive diagnostics** through sensors and JSON export
- **Provides runtime reconfiguration** via Options Flow without restart

Designed as a Home Assistant **service integration** for high-performance, solar-aware HVAC automation.

---
 [!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/TTLucian)

---

## 📦 Installation

### Manual Installation

1. Copy the `custom_components/solar_ac_controller` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration via **Settings → Devices & Services → Add Integration → Solar AC Controller**

### HACS Installation (Custom Repository)

1. Open **HACS → Integrations**
2. Click the **three-dot menu** → **Custom repositories**
3. Add repository:
   - **URL:** `https://github.com/TTLucian/ha-solar-ac-controller`
   - **Category:** Integration
4. Click **Install** on the Solar AC Controller card
5. Restart Home Assistant
6. Add the integration via **Settings → Devices & Services → Add Integration**

### Quick Links

[![Add Solar AC Controller to HACS](https://img.shields.io/badge/HACS-Add%20Solar%20AC%20Controller-blue?style=for-the-badge)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TTLucian&repository=ha-solar-ac-controller&category=integration)

[![Add Solar AC Controller Integration](https://img.shields.io/badge/Home%20Assistant-Add%20Integration-blue?style=for-the-badge&logo=homeassistant)](https://my.home-assistant.io/redirect/config_flow_start?domain=solar_ac_controller)

## 🚀 Features

### 🌞 Solar-aware zone control

Zones activate **in priority order** (based on config order) using real-time solar export and grid import data. The controller maintains separate EMAs (30-second and 5-minute) for responsive yet stable decision-making.

### 🧠 Adaptive learning engine

Each zone's power consumption is learned using a **per-mode (heat/cool) EMA model** with bootstrap initialization. The system tracks samples and continuously refines estimates as zones operate, improving accuracy over time.

### 🔒 Manual override detection

When a zone state changes outside the controller's actions, a **configurable lock** (default 20 minutes) prevents the controller from modifying that zone, respecting user intent.

### 🆘 Panic shedding

When grid import exceeds a configured threshold **and persists for the panic delay**, the controller **sequentially sheds zones** (with configurable inter-action delays) to protect the inverter from overload.

### 🔌 Optional master switch control

If configured, the controller can automatically turn the master AC switch ON when solar production exceeds `solar_threshold_on` and OFF when it drops below `solar_threshold_off`, using hysteresis to prevent oscillation.

### 📊 Full observability

Exposes **20+ sensors and binary sensors** showing EMAs, confidence scores, zone states, panic status, learning activity, and more. Optional diagnostics sensor provides complete internal state as JSON attributes.

---

## 📡 Exposed Entities

### **Sensors** (Power values in Watts)

- **Active Zones** — Comma-separated list of currently running zones
- **Active Zone Count** — Number of currently active zones (useful for automations)
- **Next Zone** — The zone that will be added next if conditions allow
- **Last Zone** — The most recently active zone
- **Last Action** — Most recent controller action (e.g., `added_zone`, `removed_zone`, `panic`, `no_action`)
- **Season Mode** — Current season mode (`heat` / `cool`)
- **EMA 30s** — 30-second exponential moving average of grid power
- **EMA 5m** — 5-minute exponential moving average of grid power
- **Confidence** — Current decision confidence score (points)
- **Confidence Thresholds** — Shows unified add/remove confidence thresholds as attributes
- **Required Export** — Minimum export needed to add the next zone
- **Required Export Source** — Human-readable reason for the current required export value
- **Export Margin** — Current export headroom above required export
- **Learned Idle Power** — Learned compressor draw while running with no active zones
- **Grid Import Tolerance** — Current import tolerance in W, live-updated from the Aggressiveness slider (`aggressiveness × 700 W`)
- **Compressor Recovery Remaining** — Seconds left on the post-add compressor ramp-up guard
- **Panic Cooldown Active** — Status of panic cooldown timer ("yes"/"no")
- **Samples** — Number of learning samples collected
- **Learned Power [zone]** — Per-zone learned power consumption (one sensor per configured zone)
- **Zone Lock Remaining [zone]** — Seconds until a zone's manual-override lock expires (one per zone)
- **Add Confidence Breakdown** (optional diagnostic) — Per-factor breakdown of the add confidence score
- **Remove Confidence Breakdown** (optional diagnostic) — Per-factor breakdown of the remove confidence score
- **Peak Delta [zone]** (optional diagnostic) — Learned compressor startup surge per zone
- **Diagnostics** (optional) — JSON snapshot of complete controller state

### **Binary Sensors**

- **Learning Active** — Whether a learning cycle is in progress
- **Panic State** — Whether panic shedding is currently active
- **Panic Cooldown** — Whether panic cooldown period is active
- **Short Cycling** — Whether any zone is in short-cycle protection
- **Manual Lock Active** — Whether any zone is manually locked
- **Exporting** — Grid export active (EMA 30s < 0)
- **Importing** — Grid import active (EMA 30s > 0)
- **Master Switch** — State of the optional master AC switch

## 🔍 Diagnostics

The Solar AC Controller provides a unified diagnostics system designed to help with troubleshooting, performance tuning, and understanding the controller’s internal decision engine. Diagnostics are available in two complementary forms:

### 🧠 Diagnostics Sensor (Optional)

You can enable an always‑on diagnostics sensor that exposes the controller’s full internal state as JSON attributes.

**How to enable:**
1. Go to **Settings → Devices & Services → Solar AC Controller → Configure**
2. Toggle **Enable Diagnostics Sensor**
3. Submit the form

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

**Settings → Devices & Services → Add Integration → Solar AC Controller**

### Required Configuration

- **Solar sensor** — Entity measuring solar production (W)
- **Grid sensor** — Entity measuring grid power (W, positive=import, negative=export)
- **AC power sensor** — Entity measuring total AC power consumption (W)
- **Zones** — Multi-select of `climate`, `switch`, or `fan` entities (order = priority)

### Optional Configuration

- **Master AC switch** — Optional switch entity to control entire AC system
- **Solar ON threshold** (default: 1200W) — Solar production required to enable master switch
- **Solar OFF threshold** (default: 800W) — Solar production below which master switch turns off
- **Panic threshold** (default: 2000W) — Grid import level triggering panic shedding
- **Panic delay** (default: 60s) — How long panic condition must persist
- **Manual lock seconds** (default: 1200s) — Duration zones are locked after manual changes
- **Short cycle ON seconds** (default: 1200s) — Minimum ON time before allowing OFF
- **Short cycle OFF seconds** (default: 20s) — Minimum OFF time before allowing ON
- **Action delay seconds** (default: 3s) — Delay between consecutive service calls
- **Initial learned power** (default: 1000W) — Bootstrap estimate before learning completes
- **Max temperature winter** (default: 21C) — Comfort target for zones in heat mode
- **Min temperature summer** (default: 21C) — Comfort target for zones in cool mode
- **Zone temperature sensors** (optional) — Per-zone indoor temperature sensor entities for comfort-aware removal blocking
- **Enable diagnostics sensor** (default: disabled) — Optional JSON diagnostics sensor
- **Aggressiveness** (default: 0.5) — Controls how eagerly zones are activated; see the [Aggressiveness Slider](#️-aggressiveness-slider) section for details

---

## 🛠 Runtime Options (Options Flow)

All configuration parameters can be changed at runtime via **Settings → Devices & Services → Solar AC Controller → Configure**.
When launched via Reconfigure, the form now pre-fills with your existing data+options values for a faster review.

### Behavioral Parameters

- **`manual_lock_seconds`** — Duration a zone remains locked after manual override (default: 1200s / 20 min)
- **`short_cycle_on_seconds`** — Minimum ON time before allowing OFF transition (default: 1200s)
- **`short_cycle_off_seconds`** — Minimum OFF time before allowing ON transition (default: 20s)
- **`action_delay_seconds`** — Inter-service-call delay for sequential zone actions (default: 3s)

### Threshold Parameters (Watts)

- **`panic_threshold`** — Grid import level triggering panic shedding (default: 2000W)
- **`panic_delay`** — Persistence time before panic activates (default: 60s)
- **`solar_threshold_on`** — Solar production to enable master switch (default: 1200W)
- **`solar_threshold_off`** — Solar production to disable master switch (default: 800W)

### Decision Engine Parameters

- **`initial_learned_power`** — Bootstrap estimate before learning completes (default: 1000W)
- **`aggressiveness`** — Zone activation eagerness from 0.0 (conservative) to 1.0 (aggressive); drives add/remove thresholds and import tolerance simultaneously (default: 0.5)

### Diagnostics

- **`enable_diagnostics_sensor`** — Toggle optional diagnostics sensor (default: disabled)

**Changes apply immediately** after saving — no integration reload required.

---

## 🎚️ Aggressiveness Slider

The **Aggressiveness** slider (range 0.0 – 1.0) is the primary tuning knob for how eagerly the controller activates and keeps zones running. Moving it drives **four interconnected parameters simultaneously**, so you never need to manually edit confidence thresholds or import tolerance to change the overall behaviour.

### Parameters driven by aggressiveness

| Parameter | Formula | Conservative (0.0) → Aggressive (1.0) |
|---|---|---|
| **Add threshold** | `80 − 60 × a` | 80 pts → 20 pts |
| **Remove threshold** | `−70 + 50 × a` *(clamped for 50-pt deadband)* | −70 pts → −30 pts |
| **Grid import tolerance** | `a × 700 W` | 0 W → 700 W |
| **Deadband** *(add − remove)* | 150 − 100 × a *(≥ 50 pts)* | 150 pts → 50 pts |

**What each parameter does:**

- **Add threshold** — Minimum confidence score the controller must reach before switching a zone on. Lower means easier to turn on.
- **Remove threshold** — Confidence score must fall below this (negative) value before the controller turns a zone off. Less negative means easier to turn off.
- **Grid import tolerance** — Watts of grid import the controller accepts while still allowing zone activation. Lets the system be optimistic during short demand spikes at the moment a compressor starts.
- **Deadband** — Gap between the add and remove thresholds. A wide deadband resists oscillation; a narrow one reacts faster to changing conditions.

### Settings reference table

| Aggressiveness | Add threshold (pts) | Remove threshold (pts) | Grid tolerance (W) | Deadband (pts) | Behaviour summary |
|:-:|:-:|:-:|:-:|:-:|:--|
| **0.0** | 80 | −70 | 0 | 150 | Maximum caution — zones only activate during strong, sustained export; no grid import tolerated at all |
| **0.1** | 74 | −65 | 70 | 139 | Very conservative; tiny transient import (70 W) tolerated |
| **0.2** | 68 | −60 | 140 | 128 | Conservative; requires reliable export before activating |
| **0.3** | 62 | −55 | 210 | 117 | Slightly cautious; small 210 W import margin accepted |
| **0.4** | 56 | −50 | 280 | 106 | Below default; suits smaller solar arrays or shared inverters |
| **0.5** | 50 | −45 | 350 | 95 | **Default** — balanced; tolerates ~350 W transient import during compressor ramp-up |
| **0.6** | 44 | −40 | 420 | 84 | Slightly aggressive; zones activate more readily on moderate export |
| **0.7** | 38 | −35 | 490 | 73 | Aggressive; accepts up to 490 W of import |
| **0.8** | 32 | −30 | 560 | 62 | Very aggressive; turns on with modest export |
| **0.9** | 26 | −25 | 630 | 51 | Near-maximum; minimal solar surplus needed to trigger activation |
| **1.0** | 20 | −30 ¹ | 700 | 50 | Maximum — activates on any measurable surplus; 50-pt minimum deadband enforced |

> ¹ At aggressiveness 1.0 the raw remove threshold (−20) would reduce the deadband below 50 points, so it is clamped to −30 to preserve stability and prevent rapid cycling.

### Choosing a value

| Situation | Recommended range |
|---|---|
| Cloudy / unreliable generation or shared inverter | 0.2 – 0.4 |
| Typical rooftop solar system | **0.5 (default)** |
| Large array, modest AC loads | 0.6 – 0.7 |
| Maximise self-consumption, large array | 0.8 – 1.0 |

---

## 🧩 Services

### `solar_ac_controller.force_relearn`

Resets learned power values and sample count for specific zones or all zones. This allows you to force the system to relearn power consumption patterns.

**Parameters:**
- `zone` (optional): Specific climate or switch zone entity ID to reset. If omitted, resets all zones.

**Examples:**

Reset all zones (full relearn):
```yaml
service: solar_ac_controller.force_relearn
data: {}
```

Reset specific zone:
```yaml
service: solar_ac_controller.force_relearn
data:
  zone: climate.living_room
```

## 🙌 Credits

**Created by:** [@TTLucian](https://github.com/TTLucian)

### Device Version in Home Assistant

The integration creates a **single logical device** ("Solar AC Controller") that manages all zones. The device version in Home Assistant Settings → Devices & Services comes from **[manifest.json](custom_components/solar_ac_controller/manifest.json)** (the `version` field).

- **Device Version = Integration Version** (shown in HA UI)
- If you see an old version after updating, restart Home Assistant or delete and re-add the integration
- Version is fetched at setup time from the manifest

Designed for high-performance, solar-aware HVAC automation with comprehensive observability and production-grade reliability.
