<!-- Copilot / AI agent instructions for contributors and automation -->
# Solar AC Controller — AI Coding Agent Guide (2026)

Purpose: concise, actionable guidance for editing this Home Assistant integration.

## Big Picture
- `coordinator.py`: Central DataUpdateCoordinator. Runs every 5s, reads sensors, updates `ema_30s/ema_5m`, decides add/remove/panic, master switch logic.
- `controller.py`: Learning engine + persistence helpers. `start_learning()` stores baseline; `finish_learning()` computes delta, updates `learned_power` (per-mode), persists.
- `__init__.py`: Config entry setup, `Store` load/migration, coordinator creation, device registration, `reset_learning` service.
- `sensor.py` / `binary_sensor.py`: Non‑polling entities that read coordinator state via `async_add_listener`.
- `helpers.py` + `diagnostics.py`: Unified diagnostics; `build_diagnostics(coordinator)` feeds both sensor attrs and HA export.

## Data & Decisions
- Inputs: `solar_sensor`, `grid_sensor`, `ac_power_sensor` (Watts). Grid import is positive; export is negative.
- EMA: `ema_30s = 0.25*grid + 0.75*ema_30s`, `ema_5m = 0.03*grid + 0.97*ema_5m`.
- Required export = learned power for `next_zone` (no multiplier). Export headroom = `-ema_30s - required_export`.
- Priority: zone order from config is the priority; first is highest for add, last active (unlocked) is candidate for remove.
- Locks & guards: manual override lock (`manual_lock_seconds`), short‑cycle delays (`short_cycle_on_seconds`, `short_cycle_off_seconds`), sequential action delay (`action_delay_seconds`).
- Panic: triggers when `ema_30s > panic_threshold` for `panic_delay` seconds and multiple zones are on; sheds sequentially.

## Storage & Migration
- On‑disk payload: `{"learned_power": {zone: {default|heat|cool: float}}, "samples": int}`.
- `const.py`: increment `STORAGE_VERSION` on schema changes; migration lives in `__init__._async_migrate_data()`.
- Coordinator persists via `_persist_learned_values()`; controller calls it after learning.

## Config & Options Flow
- Use selectors directly: `selector({"entity": {"domain": ["climate","switch","fan"], "multiple": True}})` for zones.
- Set defaults with `vol.Optional(..., default=...)`; avoid `vol.Default` (not a thing).
- Options are merged over data: always read runtime config from `{**entry.data, **entry.options}`.

## Entities & Patterns
- Non‑polling: entities set `_attr_should_poll = False` and register `coordinator.async_add_listener(self.async_write_ha_state)`.
- Minimal `DeviceInfo`: `identifiers={(DOMAIN, entry.entry_id)}`; version sourced from coordinator.
- Example sensor:
  ```python
  class MyEMASensor(_BaseSolarACSensor):
      @property
      def state(self):
          return self.coordinator.ema_30s
  ```
- Learning call sequence:
  ```python
  await self.controller.start_learning(zone, ac_power_before)
  await self.hass.services.async_call(..., blocking=True)
  ```

## Developer Workflow
- Formatting & linting:
  ```bash
  pip install -r requirements_dev.txt
  black .
  pylint custom_components/solar_ac_controller
  ```
- Install/run: This is a Home Assistant service integration. See README for manual or HACS install.

## Diagnostics
- Prefer `helpers.build_diagnostics(coordinator)`; it documents `required_export` equals learned power (no multiplier) and keeps sensor/export in sync.

## Gotchas
- Treat grid export as `-ema_30s` when evaluating add decisions.
- Respect zone locks when selecting next/last zones.
- `diagnostic.py` exists but `sensor.py` already exposes a diagnostics entity guarded by `enable_diagnostics_sensor`.

Questions or unclear areas? Ask for specific sections to expand (decision thresholds, panic flow, learning bootstrap, or service call patterns).

---

## Detailed Decision Engine
- Add decision: Adds `next_zone` only if it’s unlocked, not short‑cycling, `export_margin >= 0` (i.e., headroom), and confidence meets `add_confidence`.
- Remove decision: Removes `last_zone` only if it’s unlocked, not short‑cycling, import pressure high and confidence meets `remove_confidence`.
- Confidence: Coordinator computes `last_add_conf` and `last_remove_conf`, then `confidence = last_add_conf - last_remove_conf`. Thresholds are `add_confidence` and `remove_confidence` from Options.
- EMA updates every cycle: `ema_30s = 0.25*grid + 0.75*ema_30s`, `ema_5m = 0.03*grid + 0.97*ema_5m`. Use `ema_30s` for “responsive” decisions and `ema_5m` to gauge sustained import.
- Required export: Exactly the learned power for `next_zone` (no multiplier). Export margin: `-ema_30s - required_export`.

## Learning Lifecycle
- Start: `controller.start_learning(zone, ac_power_before)` records baseline (from AC power sensor) and timestamps; `coordinator.learning_active = True`.
- Finish: `controller.finish_learning()` reads current AC power (or falls back to `ema_30s`), computes `delta = |ac_now - ac_before|`, infers mode (`heat`/`cool`/`default`), updates `learned_power[zone_short]`, increments `samples`, and persists via `coordinator._persist_learned_values()`.
- Timeout: Coordinator auto‑finishes learning after ~360s to avoid indefinite states.
- Reset: Service `solar_ac_controller.reset_learning` clears runtime learning state and storage payload to empty defaults.

## Panic Flow
- Trigger: When multiple zones are ON and `ema_30s > panic_threshold` for at least `panic_delay` seconds.
- Action: Panic sheds zones sequentially (respecting `action_delay_seconds`), prioritizing last active and unlocked zones.
- Cooldown: `last_panic_ts` starts a cooldown window (~120s) during which add/remove decisions are suppressed to let the system stabilize.

## Master Switch Hysteresis
- Optional master switch (`ac_switch`): Coordinator turns master ON when `solar_sensor >= solar_threshold_on` and OFF when below `solar_threshold_off` (hysteresis prevents oscillation).
- When master is OFF: Coordinator cancels in‑flight tasks, resets learning state, and after `_EMA_RESET_AFTER_OFF_SECONDS` (~600s) resets EMA to 0 for a clean restart.

## Zone Selection and Locks
- Priority: `next_zone` is the first in configured order that’s currently OFF and not locked. `last_zone` is the most recently active (reverse order) that’s not locked.
- Manual override: If a zone’s HA state changes without matching a recent controller action, coordinator sets `zone_manual_lock_until[zone] = now + manual_lock_seconds`.
- Short‑cycle protection: Tracks `zone_last_changed` with type `on`/`off` and disallows opposite transitions until the respective delay elapses (`short_cycle_on_seconds`, `short_cycle_off_seconds`).

## Service Calls and Domains
- Entities considered “active” when HA state is `heat`, `cool`, or `on` across domains.
- Climate vs Switch/Fan: Coordinator selects service domains when turning zones on/off (e.g., `climate.turn_on`, `switch.turn_off`). Keep service calls sequential and respect `action_delay_seconds`.
- Learning and actions: Typical sequence is start learning → service call to turn a zone ON → finish learning after stabilization.

## Storage and Migration Details
- Payload: `{ "learned_power": { zone_short: { default|heat|cool: float } }, "samples": int }`.
- Migration: `__init__._async_migrate_data()` normalizes prior shapes (numeric → dict, missing modes → add `default|heat|cool`). Coordinator also migrates legacy `learned_power` if encountered at runtime and persists.
- Keys: Learned power keyed by zone short name (`entity_id.split('.')[-1]`) for stability across renames.

## Diagnostics Structure
- Unified builder: [custom_components/solar_ac_controller/helpers.py](custom_components/solar_ac_controller/helpers.py#L1) provides `build_diagnostics(coordinator)`, used by both diagnostics sensor and HA diagnostics export.
- Fields include: version, merged config (`data + options`), samples, learned power (truncated if large), EMAs, last action, next/last zone, required_export and export_margin, zones and modes, locks, panic state/timestamps, master state.
- Explicit note: `required_export_source = "learned_power"` and `note` clarifies no safety multiplier.

## Developer Tasks and Commands
- Lint/format:
  ```bash
  pip install -r requirements_dev.txt
  black .
  pylint custom_components/solar_ac_controller
  ```
- Install/run in Home Assistant (manual): Copy [custom_components/solar_ac_controller](custom_components/solar_ac_controller) into HA’s `config/custom_components/`, restart HA, then add the integration.
- HACS: Add as a custom repository via HACS, install, restart, configure.

## Conventions and Anti‑Patterns
- Read runtime config from `{**entry.data, **entry.options}`; do not read only `entry.data` inside the coordinator.
- Voluptuous: Use `vol.Optional(..., default=...)`; avoid `vol.Default` (non‑existent). Use `selector({"entity": {"domain": ["climate","switch","fan"], "multiple": True}})` for multi‑domain zone selection.
- Non‑polling entities: Always set `_attr_should_poll = False` and register a listener `coordinator.async_add_listener(self.async_write_ha_state)`.
- Error handling: Use `_LOGGER.exception` for storage/setup errors; skip cycles on non‑numeric or unavailable sensor states.
- Units: All power values are Watts. Grid import is positive, export negative; evaluate export headroom as `-ema_30s`.

## Confidence Examples (Add/Remove)
- Add confidence components (see [coordinator.py](custom_components/solar_ac_controller/coordinator.py#L529)):
  - `export_margin = export - required_export`
  - `base = clamp(0..40, export_margin / 25)` → more headroom → higher base
  - `sample_bonus = min(20, samples * 2)` → learning progress increases confidence
  - `short_cycle_penalty = -30` if last zone is short‑cycling
  - `last_add_conf = base + 5 + sample_bonus + short_cycle_penalty`
- Remove confidence components (see [coordinator.py](custom_components/solar_ac_controller/coordinator.py#L547)):
  - `base = clamp(0..60, (import_power - 200) / 8)` → sustained import increases base
  - `heavy_import_bonus = 20` if `import_power > 1500`
  - `short_cycle_penalty = -40` if last zone is short‑cycling
  - `last_remove_conf = base + 5 + heavy_import_bonus + short_cycle_penalty`
- Unified confidence: `confidence = last_add_conf - last_remove_conf`; thresholds from Options: `add_confidence` (default 25), `remove_confidence` (default 10).
- Example add: `export = 1200`, `req = 1000` → margin `200` → `base ≈ 8`, `samples=10` → bonus `20` → `last_add_conf ≈ 8 + 5 + 20 = 33` (above 25 → eligible if not short‑cycling).
- Example remove: `import_power = 1000` → `base ≈ (1000-200)/8 = 100`, clamped to `60` → `heavy_import_bonus = 20` → `last_remove_conf ≈ 60 + 5 + 20 = 85` (above 10 → eligible unless protected).

## Service Call Patterns
- Primary domain call: `_call_entity_service(entity_id, turn_on)` uses the entity’s domain (`climate`/`switch`/`fan`) with `blocking=True`.
- Fallback: if primary fails, retries `climate.turn_on/turn_off` and logs a warning.
- Action timing: After add/remove, coordinator sleeps `action_delay_seconds` (default 3s) to avoid rapid sequences.
- Learning sequence: `_add_zone` does `controller.start_learning()` → domain service call → updates `zone_last_changed` and short‑cycle type → logs `[LEARNING_START]`.
- Remove sequence: `_remove_zone` calls domain service → updates short‑cycle type → logs `[ZONE_REMOVE_SUCCESS]`.

## Troubleshooting
- Non‑numeric/unknown sensor states: Coordinator skips cycle and may set `last_action = "ac_sensor_unavailable"` when AC power sensor is unavailable. Check entities referenced in Options.
- Missing sensors: If any of grid/solar/ac states are missing, coordinator logs and skips the cycle.
- Panic doesn’t trigger: Ensure `on_count > 1` and `ema_30s > panic_threshold` persists for `panic_delay` seconds. Check binary sensors `Panic State` and `Panic Cooldown`.
- Master switch behavior: Confirm thresholds and that the configured `ac_switch` exists and is controllable; hysteresis requires `solar_threshold_on > solar_threshold_off`.
- Storage/migration: If learned values look wrong, review [__init__.py](custom_components/solar_ac_controller/__init__.py#L1) migration and [const.py](custom_components/solar_ac_controller/const.py#L1) `STORAGE_VERSION`; learned power is keyed by zone short name.
