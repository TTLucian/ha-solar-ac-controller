# Solar AC Controller (Home Assistant Integration)

A native Home Assistant integration that controls multi‑zone AC systems
based on solar surplus, grid import/export, and learned per‑zone power usage.

This replaces complex YAML automations with a Python controller using
a 5‑second coordinator loop and persistent learning stored in HA storage.

## Features

- Solar‑driven zone control
- Panic shed when grid import spikes
- Per‑zone power learning
- Confidence‑based ADD/REMOVE decisions
- Short‑cycle protection (20 minutes)
- Master switch control with compressor safety
- Logbook taxonomy for full observability
- Debug sensor exposing internal state

## Installation

### HACS

1. Add this repository as a custom repository.
2. Install **Solar AC Controller**.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/solar_ac_controller` to your HA `custom_components` folder.
2. Restart Home Assistant.

## Configuration

Go to:

**Settings → Devices & Services → Add Integration → Solar AC Controller**

Configure:

- Solar power sensor
- Grid power sensor
- AC power sensor
- AC main switch
- Climate zones
- Solar ON/OFF thresholds

## Entities

### Debug

`sensor.solar_ac_controller_debug`

Attributes:

- `last_action`
- `learning_active`
- `samples`
- `ema_30s`
- `ema_5m`
- `learned_power`
- `zone_last_changed`

## Services

### Reset all learning

```yaml
service: solar_ac_controller.reset_learning
```
### Reset one or all zones
```yaml
service: solar_ac_controller.force_relearn
data:
  zone: climate.living
```
If zone is omitted, all zones are reset.

Logging
The integration writes structured events to the HA logbook with tags like:

[ZONE_ADD_ATTEMPT]

[LEARNING_START]

[LEARNING_FINISHED]

[LEARNING_SKIP]

[ZONE_REMOVE_ATTEMPT]

[ZONE_REMOVE_SUCCESS]

[PANIC_SHED]

[MASTER_POWER_ON]

[MASTER_POWER_OFF]

[MASTER_SHUTDOWN_BLOCKED]

[SYSTEM_BALANCED]

[LEARNING_RESET]

License
MIT
