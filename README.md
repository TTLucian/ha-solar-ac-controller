# 🌞 Solar AC Controller — Home Assistant Integration

<p align="center">
  <img src="https://img.shields.io/github/v/release/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/github/license/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/ci.yml?style=for-the-badge" />
</p>

A smart and adaptive Home Assistant integration that manages multi-zone (milti-split) AC systems based on real-time solar production, grid import/export, and learned compressor behavior.

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
- **Next Zone** — The zone that will be added next if conditions allow
- **Last Zone** — The most recently active zone
- **Last Action** — Most recent controller action (e.g., `added_zone`, `removed_zone`, `panic`, `no_action`)
- **EMA 30s** — 30-second exponential moving average of grid power
- **EMA 5m** — 5-minute exponential moving average of grid power
- **Confidence** — Current decision confidence score (points)
- **Confidence Thresholds** — Shows unified add/remove confidence thresholds as attributes
- **Required Export** — Minimum export needed to add the next zone
- **Export Margin** — Current export headroom above required export
- **Import Power** — Current import power (mirrors EMA 5m)
- **Panic Cooldown Active** — Status of panic cooldown timer ("yes"/"no")
- **Learned Power [zone]** — Per-zone learned power consumption (one sensor per configured zone)
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
- **Unified add threshold** (default: 20 points) — Add zones when unified confidence >= this value
- **Unified remove threshold** (default: 10 points) — Remove zones when unified confidence <= this value
- **Initial learned power** (default: 1000W) — Bootstrap estimate before learning completes
- **Max temperature winter** (default: 21C) — Comfort target for zones in heat mode
- **Min temperature summer** (default: 21C) — Comfort target for zones in cool mode
- **Zone temperature sensors** (optional) — Per-zone indoor temperature sensor entities for comfort-aware removal blocking
- **Enable diagnostics sensor** (default: disabled) — Optional JSON diagnostics sensor

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

- **`unified_add_threshold`** — Add zones when unified confidence >= this value (default: 20 points)
- **`unified_remove_threshold`** — Remove zones when unified confidence <= this value (default: 10 points)
- **`initial_learned_power`** — Bootstrap estimate before learning completes (default: 1000W)

### Diagnostics

- **`enable_diagnostics_sensor`** — Toggle optional diagnostics sensor (default: disabled)

**Changes apply immediately** after saving — no integration reload required.

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

## 🙌 Credits

**Created by:** [@TTLucian](https://github.com/TTLucian)

### Device Version in Home Assistant

The integration creates a **single logical device** ("Solar AC Controller") that manages all zones. The device version in Home Assistant Settings → Devices & Services comes from **[manifest.json](custom_components/solar_ac_controller/manifest.json)** (the `version` field).

- **Device Version = Integration Version** (shown in HA UI)
- If you see an old version after updating, restart Home Assistant or delete and re-add the integration
- Version is fetched at setup time from the manifest

Designed for high-performance, solar-aware HVAC automation with comprehensive observability and production-grade reliability.
