<p align="center">
  <img src="https://img.shields.io/github/v/release/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/github/license/TTLucian/ha-solar-ac-controller?style=for-the-badge" />
  <img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" />
  <img src="https://img.shields.io/github/actions/workflow/status/TTLucian/ha-solar-ac-controller/validate.yml?style=for-the-badge" />
</p>


🌞 Solar AC Controller — Home Assistant Integration
A smart, adaptive controller that manages multi‑zone AC systems based on solar production, grid import/export, and learned compressor behavior.

This integration automatically:

Turns AC zones on/off based on available solar power

Learns each zone’s compressor delta (W)

Avoids short‑cycling

Detects manual overrides

Performs panic shedding when grid import spikes

Exposes full diagnostics and observability sensors

Provides a complete Options Flow for reconfiguration

Designed for high‑performance solar‑aware HVAC automation.

🚀 Features
🌞 Solar‑aware zone control
Zones are activated in priority order based on real‑time solar export and grid import.

🧠 Adaptive learning engine
The controller learns each zone’s compressor power delta using a bootstrap + EMA model.

🔒 Manual override detection
If a user manually changes a zone, the controller locks it for 20 minutes to avoid fighting the user.

🆘 Panic shedding
If grid import exceeds a configurable threshold, the controller safely shuts down zones to protect the inverter.

📊 Full observability
The integration exposes:

Sensors
Active zones

Next zone

Last action

EMA 30s

EMA 5m

Add confidence

Remove confidence

Required export

Export margin

Import power

Learned power per zone

Binary Sensors
Learning active

Panic state

Short‑cycling

Manual lock active

Exporting

Importing

Diagnostic Entity
A single entity exposing the entire controller brain as attributes.

Home Assistant Diagnostics Export
A full JSON dump of internal state for debugging and support.

⚙️ Configuration
Initial setup
The integration supports a full Config Flow with friendly names and explanations.

Options Flow
You can adjust everything without removing the integration:

Sensors

Zones (comma‑separated, ordered by priority)

Solar thresholds

Panic thresholds

Panic delay

All changes apply instantly.

🛠 Services
solar_ac_controller.reset_learning
Resets all learned compressor values.

solar_ac_controller.force_relearn
Forces relearning for a specific zone or all zones.

🧪 Diagnostics
Home Assistant’s built‑in Diagnostics export includes:

Learned power

EMA values

Zone lock timers

Zone last changed

Panic/learning state

Config thresholds

Active/next zone

Full controller state

This makes debugging and support trivial.

📦 Installation
Manual installation
Copy the custom_components/solar_ac_controller folder into your Home Assistant config directory

Restart Home Assistant

Add the integration via:
Settings → Devices & Services → Add Integration → Solar AC Controller

HACS (planned)
HACS support will be added soon.

🧩 File Structure
Code
custom_components/solar_ac_controller/
│
├── __init__.py
├── manifest.json
├── config_flow.py
├── coordinator.py
├── controller.py
├── sensor.py
├── binary_sensor.py
├── diagnostic.py        ← Diagnostic entity
├── diagnostics.py       ← HA diagnostics export
└── diagnostics.json     ← Diagnostics metadata
🙌 Credits
Created by @TTLucian  
Designed for high‑performance solar‑aware HVAC automation.
