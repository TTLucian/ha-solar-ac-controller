# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---
## v0.2.2 — 2026-01-09 — Unified Confidence Model, Stability Improvements, Sensor Cleanup
### ✨ Major Changes
Replaced the dual‑axis confidence system (add_confidence, remove_confidence) with a single unified signed confidence axis:

Positive → add zone

Negative → remove zone

Near zero → balanced

Introduced asymmetric thresholds for stable hysteresis:

add_confidence_threshold (default +25)

remove_confidence_threshold (default 10, applied as –10 internally)

This dramatically improves controller stability, reduces oscillation, and simplifies tuning.

### 🧠 Coordinator Logic
Added self.confidence (signed scalar) computed as:

Code
confidence = last_add_conf - last_remove_conf
Updated decision logic:

Add zone when confidence >= add_confidence_threshold

Remove zone when confidence <= -remove_confidence_threshold

Removed conflicting add/remove decision paths from the old model.

Improved logging to include unified confidence and thresholds.

### 🧩 Config Flow
Updated UI fields to reflect the new unified model.

Removed fractional defaults; thresholds now use integer values.

Backward‑compatible: old option keys still load correctly.

### 📡 Sensor Model Cleanup
Removed:

sensor.solar_ac_add_confidence

sensor.solar_ac_remove_confidence

sensor.solar_ac_add_conf_threshold

sensor.solar_ac_remove_conf_threshold

Added:

sensor.solar_ac_confidence (signed unified confidence)

sensor.solar_ac_conf_thresholds (attributes: add_threshold, remove_threshold)

### 🔧 Internal Improvements
Simplified coordinator state machine.

Reduced decision jitter during cloud transitions.

Improved observability for debugging and tuning.

### ⚠️ Breaking Changes
Old confidence sensors removed.

Config option names changed (but migration is automatic).

### 📈 Expected Behavior Improvements
Far smoother add/remove transitions.

No more add/remove oscillation during marginal solar conditions.

More intuitive tuning: only two numbers matter now.


## [0.2.1] — 2026-01-09
### Added
- New coordinator‑exposed fields:
  - `next_zone`
  - `last_zone`
  - `required_export`
  - `export_margin`
  - `master_off_since`
  - `last_panic_ts`
- New binary sensors:
  - Panic cooldown active
  - Master switch OFF
- New diagnostic fields:
  - Panic cooldown state
  - Required export
  - Export margin
  - Last zone
  - Master OFF timestamp
- New `diagnostic.py` entity exposing full controller brain
- New `diagnostics.json` describing all exported fields
- Direct HACS and Config Flow badges for README

### Changed
- Full refactor of `sensor.py` to remove duplicated logic and rely entirely on coordinator state
- Full refactor of `binary_sensor.py` to align with new coordinator fields
- Full refactor of `diagnostics.py` to expose all internal state cleanly
- Updated `__init__.py` to support runtime option updates and improved services
- Updated `manifest.json` to version `0.2.1`
- Updated README with new badges, installation links, and updated feature list

### Fixed
- Short‑cycle detection now uses correct per‑zone thresholds
- Panic detection and cooldown logic now consistent across coordinator and sensors
- Manual lock detection improved for edge cases
- Master OFF behavior now correctly resets EMA and learning state

---

## [0.2.0] — 2025‑12‑xx
### Added
- Initial Options Flow support
- Panic shedding logic
- Learning engine improvements
- Basic diagnostics export

---

## [0.1.0] — 2025‑11‑xx
### Added
- Initial public release
- Multi‑zone solar‑aware AC control
- Learning engine for compressor delta
- Short‑cycle protection
- Manual override detection
- Basic sensors and binary sensors
