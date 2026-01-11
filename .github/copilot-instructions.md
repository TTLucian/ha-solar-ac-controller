<!-- Copilot / AI agent instructions for contributors and automation -->
# Solar AC Controller — AI coding agent guide

Purpose: help AI agents be immediately productive when editing this Home Assistant integration.

Big picture
- The integration is a Home Assistant "service" integration. Main pieces:
  - `custom_components/solar_ac_controller/coordinator.py` — the central DataUpdateCoordinator. Runs every 5s, reads sensors, computes EMAs, decides add/remove/panic actions.
  - `custom_components/solar_ac_controller/controller.py` — encapsulates learning and persistent logic (start/finish learning, reset, storage writes).
  - `custom_components/solar_ac_controller/sensor.py`, `binary_sensor.py`, `diagnostics.py` — entity factories and debug/diagnostic exposures. Entities register listeners with `coordinator.async_add_listener` and are non-polling.
  - `custom_components/solar_ac_controller/__init__.py` — config entry setup, `Store` usage, shared `coordinator` instance, service registration (`reset_learning`, `force_relearn`), and device registration.

Data & control flow (concrete):
- External sensors (solar, grid, ac power) -> coordinator `_async_update_data` -> compute `ema_30s/ema_5m` -> decide actions
- On add/remove, the coordinator calls Home Assistant services: `climate.turn_on/off` and `switch.turn_on/off` (see `_add_zone`, `_remove_zone`, `_handle_master_switch`).
- Learned values are kept in `coordinator.learned_power` and persisted via `homeassistant.helpers.storage.Store` (see `__init__.py` setup and `controller._save`).

- Project-specific conventions and patterns
- Zones: the integration expects zones as HA entity IDs (comma-separated) that control HVAC (e.g. `climate.*`, `switch.*`, `fan.*`). Zone "short name" is extracted by `zone.split(".")[-1]`; learned values are keyed by that short name.
  The coordinator calls the appropriate `turn_on`/`turn_off` service for the entity's domain and falls back to `climate` when needed.
 - Manual override lock: when a zone is manually changed the coordinator sets a lock to avoid fighting the user. The default lock is 1200s (20 minutes) and is configurable via the `manual_lock_seconds` option.
 - Manual override lock: when a zone is manually changed the coordinator sets a lock to avoid fighting the user. The default lock is 1200s (20 minutes) and is configurable via the `manual_lock_seconds` option.
 - Short-cycle protection: separate configurable delays apply after a zone was turned `on` vs `off`. Use `short_cycle_on_seconds` and `short_cycle_off_seconds` options to tune how long the controller avoids rapid add/remove decisions after a recent change.
 - Short-cycle protection: separate configurable delays apply after a zone was turned `on` vs `off`. Use `short_cycle_on_seconds` and `short_cycle_off_seconds` options to tune how long the controller avoids rapid add/remove decisions after a recent change.
 - Sequential action delay: to avoid hammering cloud APIs when the integration turns multiple zones in sequence (for example during panic shed), use `action_delay_seconds` to add a delay between consecutive `turn_on`/`turn_off` calls (default 3s).
- Options flow: runtime options are merged over stored data in `__init__.py` with `config = {**entry.data, **entry.options}` — prefer reading options from `config` after that merge.
- Coordinator-centric design: state and behavior live on `SolarACCoordinator` (EMA, learned_power, samples, locks, last_action). Entities read state from the coordinator and call `async_write_ha_state` via `async_add_listener`.
- Logging: internal events use a structured tag taxonomy in `_log` calls (e.g. `[LEARNING_BOOTSTRAP]`, `[PANIC_SHED]`). Preserve or extend this taxonomy when adding messages.
- Non-polling entities: entities set `_attr_should_poll = False` and rely on coordinator callbacks.

Integration/service points to reference in edits
- `custom_components/solar_ac_controller/__init__.py` — config entry lifecycle, service registration, `Store` usage
- `custom_components/solar_ac_controller/coordinator.py` — main loop, decision logic, EMAs, panic and learning flow
- `custom_components/solar_ac_controller/controller.py` — learning algorithm and persistence
- `custom_components/solar_ac_controller/sensor.py` — how entity classes expose coordinator data and register listeners
- `custom_components/solar_ac_controller/manifest.json` — integration metadata (integration_type, platforms)

Developer workflows & commands (discovered)
- Formatting & linting: `black` and `pylint` listed in `requirements_dev.txt`. Run locally via:
```bash
pip install -r requirements_dev.txt
black .
pylint custom_components/solar_ac_controller
```
- Tests: `pytest` is listed; run unit tests with `pytest` if present.
- Home Assistant: the integration is intended to run inside Home Assistant; manual install steps are in `README.md`.

- Sensors & units:
  - All power sensors are expected in Watts (`W`).
  - `grid_sensor` reports grid import as positive, export as negative. The coordinator treats export as `-ema_30s` when computing available export for zone add decisions.
 - Panic configuration:
   - `panic_threshold` (W, positive = grid import) — controller enters panic when `ema_30s > panic_threshold` and `on_count > 1`.
   - `panic_delay` (seconds) — how long the condition must persist before panic executes.
   - Recommended defaults: `panic_threshold=1500`, `panic_delay=30`.

What to watch for when editing
- Maintain coordinator as single source of truth. Avoid duplicating decision logic across entities.
- Preserve `async` semantics — use `async_call` with `blocking=True` only where existing code expects synchronous completion (see `_add_zone` / `_remove_zone`).
- When changing storage layout, increment `STORAGE_VERSION` in `custom_components/solar_ac_controller/const.py` and add migration reasoning in commit message.
- Unit of learned values: `learned_power` values are whole-Watt integers; ensure computations and stored values remain numeric.

Examples (copyable patterns)
- Add a new sensor that reflects `ema_30s`:
  - subclass `_BaseSolarACSensor` in `sensor.py` and read `self.coordinator.ema_30s` in `state`.
- Start a controller action from coordinator:
  - call `await self.controller.start_learning(zone, ac_power_before)` then call an HA service with `await self.hass.services.async_call(..., blocking=True)`.

If anything here is unclear or you need more details (tests, CI, or reasoning for a specific logic branch), tell me which area to expand and I will iterate.
