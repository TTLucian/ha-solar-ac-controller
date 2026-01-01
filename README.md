# Solar AC Controller (Home Assistant Integration)

A fully native Home Assistant integration that controls multi‑zone AC systems
based on solar surplus, grid import/export, and learned per‑zone power usage.

This integration replaces all YAML automations with a clean Python controller
using a 5‑second coordinator loop and persistent learning stored in HA storage.

---

## Features

### ✔ Solar‑driven zone control
- Adds zones when solar export is high
- Removes zones when grid import rises
- Panic‑sheds all but the primary zone when import spikes

### ✔ Learning engine
- Learns the real power draw of each zone
- Stores values in HA storage (no helpers needed)
- Updates after each zone‑add event
- Automatically adjusts RequiredExport thresholds

### ✔ Confidence‑based decisions
- ADD_conf from EMA 30s
- REMOVE_conf from EMA 5m
- Short‑cycle protection (20 minutes)
- Safety multipliers for early learning

### ✔ Debug sensor
Exposes:
- last action  
- learning state  
- EMA values  
- learned power  
- short‑cycle timers  

### ✔ Services
- `solar_ac_controller.reset_learning`
- `solar_ac_controller.force_relearn`

---

## Installation

### HACS (recommended)
1. Add this repository as a custom repository.
2. Install **Solar AC Controller**.
3. Restart Home Assistant.

### Manual
Copy `custom_components/solar_ac_controller` into your HA config folder.

---

## Configuration

Go to:

**Settings → Devices & Services → Add Integration → Solar AC Controller**

Select:
- Solar power sensor  
- Grid power sensor  
- AC power sensor  
- AC main switch  
- Climate zones  

---

## Debugging

A debug sensor is created:

`sensor.solar_ac_controller_debug`

It exposes all internal state.

---

## Services

### Reset all learning

`solar_ac_controller.reset_learning`


### Reset one zone or all zones

`solar_ac_controller.force_relearn

zone: climate.living`


---

## License
MIT

