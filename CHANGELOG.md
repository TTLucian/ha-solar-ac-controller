# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---
## [0.7.6] — 2026-01-18 — Manual Power Override & Diagnostics Cleanup

### Added

- Per-zone manual power override: configure comma-separated watts in the comfort step; coordinator parses both string and list, mapping by zone order. Manual values take precedence over learned power for `required_export`.
- Diagnostics field `required_export_source`: clearly indicates `manual_power` vs `learned_power`.
- ISO8601 timestamps in diagnostics (`*_at`) alongside human-readable deltas; improved readability for last action, panic, and master-off events.

### Changed

- Learned power updates now discard unreasonable samples (absolute and relative outlier filters) and apply EMA smoothing for stable expected zone draw.
- Diagnostics payload trimmed: removed redundant fields (e.g., `manifest_version`, `zone_last_state`) and aligned schema.

### Notes

- No storage schema changes; `STORAGE_VERSION` unchanged.
- Temperature modulation and comfort gating behavior unchanged; only observability and manual override paths improved.

## [0.6.3] — 2026-01-17 — Storage Migration Function Fix

### Fixed

- Migration function now synchronous (as required by Home Assistant Store), preventing `NotImplementedError` during storage load.

## [0.6.2] — 2026-01-17 — Minor improvements

### Changed

- Code quality and reliability enhancements.

## [0.6.1] — 2026-01-17 — Storage Migration Fix

### Fixed

- Storage migration function now properly passed to Store constructor, preventing `NotImplementedError` on load.

## [0.6.0] — 2026-01-17 — 7-Day Rolling Mean & Temperature-Aware Control

### ✨ Major Features
- **7-day rolling mean for season switching**: Outside temperature is now smoothed over 7 days to reduce jitter during weather transitions; falls back to instantaneous temp if history is short or sensor unavailable.
- **Explicit auto-disable for missing sensors**: Auto-season is automatically disabled if outside temperature sensor is missing; temperature modulation is automatically disabled if zone temperature sensors are not configured. No silent fallback—clear logging explains why features are disabled.
- **Temperature-aware removal gating**: Comfort targets (`max_temp_winter` / `min_temp_summer`) now explicitly block zone removal until all active zones reach their targets; missing sensors assume "not at target" to conservatively keep zones on.

### Changed

- SeasonManager now tracks 7-day temperature history (deque, max 10,080 samples) and computes rolling mean each cycle; `update_season_mode()` uses rolling mean for hysteresis-based decisions.
- Coordinator exposes `outside_temp_rolling_mean` and reads from season manager each cycle.
- Diagnostics payload includes `outside_temp_rolling_mean` for visibility into season decision inputs.
- Season auto-detect explicitly disabled at init if outside sensor is missing (SeasonManager checks and logs).
- Temperature modulation explicitly disabled at init if no zone temperature sensors configured (Coordinator checks and logs).
- README documents 7-day rolling mean feature and temperature bands; Copilot instructions updated with rolling mean behavior and auto-disable logic.

### Fixed

- Temperature-aware decisions now have explicit guards: removing zones respects comfort targets across all seasons.

## [0.5.9] — 2026-01-17 — Reconfigure defaults & diagnostics clarity

### Changed

- Reconfigure flow seeds defaults from existing entry data/options (zones, thresholds, comfort targets) so the form pre-fills during Configure/Reconfigure; strings updated to cover comfort targets and zone temp sensors.
- Comfort/season UX: options labels/descriptions now mention `max_temp_winter`, `min_temp_summer`, and `zone_temp_sensors`; Copilot instructions note `async_step_reconfigure` and keeping defaults in sync; README documents the pre-fill behavior.
- Diagnostics: payload now includes `manifest_version` derived from the manifest alongside the existing `version`, improving support visibility of installed build; no behavior change to decision logic.
- Device metadata: removed `sw_version` from all DeviceInfo (sensors, binary sensors, diagnostics, device registry) to prevent duplicate devices; kept identifiers stable; manifest bumped to 0.5.9 with fallback aligned.
- Season/comfort clarifications: document that auto-season uses hysteresis and outside bands, with comfort gates (`max_temp_winter`/`min_temp_summer`) blocking removals until all active zones reach targets; missing temp sensors fall back to solar-only control.
- Learning and bands: reaffirm per-mode, per-band learned power (cold/mild_cold/mild_hot/hot) with fallback to mode default and initial learned power when band data is missing.
- Manual overrides: manual lock duration respected when users toggle zones; neutral mode can turn master off when configured.

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
