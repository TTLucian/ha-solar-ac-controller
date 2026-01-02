<p align="center">
  <img src="https://img.shields.io/github/v/release/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/github/license/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/validate.yml?style=for-the-badge" />
</p>

# 🌞 Solar AC Controller — Home Assistant Integration

A smart, adaptive controller that manages multi-zone AC systems based on solar production, grid import/export, and learned compressor behavior.

This integration automatically:

- Turns AC zones on/off based on available solar power
- Learns each zone’s compressor delta (W)
- Avoids short-cycling
- Detects manual overrides
- Performs panic shedding when grid import spikes
- Exposes full diagnostics and observability sensors
- Provides a complete Options Flow for reconfiguration

Designed for high-performance solar-aware HVAC automation.

---

## 🚀 Features

### 🌞 Solar-aware zone control

Zones are activated in priority order based on real-time solar export and grid import.

### 🧠 Adaptive learning engine

The controller learns each zone’s compressor power delta using a bootstrap + EMA model, improving decisions over time.

### 🔒 Manual override detection

If a user manually changes a zone, the controller locks that zone for a period to avoid fighting the user.

### 🆘 Panic shedding

If grid import exceeds a configurable threshold, the controller safely shuts down zones to protect the inverter and installation.

### 📊 Full observability

The integration exposes multiple entities to let you see exactly what the controller is doing and why.

#### Sensors

- Active zones
- Next zone
- Last action
- EMA 30s
- EMA 5m
- Add confidence
- Remove confidence
- Required export
- Export margin
- Import power
- Learned power per zone

#### Binary sensors

- Learning active
- Panic state
- Short-cycling
- Manual lock active
- Exporting
- Importing

#### Diagnostic entity

A single entity exposing the controller brain via attributes:

- Config
- Active zones
- Learning state and current learning zone
- Learned power
- EMA values
- Zone last changed timestamps
- Zone manual lock timers
- Panic configuration
- Last action

#### Home Assistant diagnostics export

A dedicated diagnostics handler provides a JSON dump of internal state that can be downloaded from:

`Settings → Devices & Services → Solar AC Controller → Diagnostics`

This is useful for debugging and for attaching to GitHub issues.

---

## Configuration

### Initial setup

The integration uses Home Assistant’s Config Flow. You can add it from:

`Settings → Devices & Services → Add Integration → Solar AC Controller`

During setup you define:

- Solar sensor
- Grid sensor
- AC power sensor
- Master AC switch
- Zones (as a comma-separated list, in activation priority order)
- Solar thresholds
- Panic threshold
- Panic delay

### Options Flow

All key parameters can be adjusted later without removing the integration:

- Sensors
- Zones and their order (comma-separated)
- Solar ON/OFF thresholds
- Panic threshold
- Panic delay

Changes take effect immediately after saving.

---

## Services

### `solar_ac_controller.reset_learning`

Resets all learned compressor values and related learning statistics.

### `solar_ac_controller.force_relearn`

Forces relearning for:

- A specific zone (when `zone` is provided)
- All zones (when `zone` is omitted)

---

## Diagnostics

The integration provides two layers of diagnostics:

1. A diagnostic entity (`Solar AC Diagnostics`) with rich attributes
2. A Home Assistant diagnostics export (`diagnostics.py`) that returns a JSON-safe snapshot of:

   - Config
   - Learned power
   - Samples
   - Learning state
   - EMA values
   - Zone lock timers
   - Zone last changed
   - Panic configuration
   - Active zones
   - Next zone
   - Last action

This makes it easy to understand behavior and report issues.

---

## 📦 Installation

### Manual installation

1. Copy the `custom_components/solar_ac_controller` folder into your Home Assistant `config` directory.
2. Restart Home Assistant.
3. Add the integration via: `Settings → Devices & Services → Add Integration → Solar AC Controller`

### HACS (Custom Repository)

This integration can be installed through **HACS** by adding it as a **Custom Repository**:

1. Open HACS → Integrations
2. Click the three‑dot menu → *Custom repositories*
3. Add the repository URL: ```https://github.com/TTLucian/ha-solar-ac-controller```
4. Select category: **Integration**
5. Install the integration
6. Restart Home Assistant
7. Add it via: `Settings → Devices & Services → Add Integration → Solar AC Controller`

---

## 🙌 Credits
Created by @TTLucian.
Designed for high-performance, solar-aware HVAC automation with strong observability.
