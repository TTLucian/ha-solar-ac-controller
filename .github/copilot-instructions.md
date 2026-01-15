<!-- Copilot / AI agent instructions for contributors and automation -->
# Solar AC Controller — AI coding agent guide

Purpose: help AI agents be immediately productive when editing this Home Assistant integration.

Big picture
  - `custom_components/solar_ac_controller/coordinator.py` — the central DataUpdateCoordinator. Runs every 5s, reads sensors, computes EMAs, decides add/remove/panic actions.
  - `custom_components/solar_ac_controller/controller.py` — encapsulates learning and persistent logic (start/finish learning, reset, storage writes).
  - `custom_components/solar_ac_controller/sensor.py`, `binary_sensor.py`, `diagnostics.py` — entity factories and debug/diagnostic exposures. Entities register listeners with `coordinator.async_add_listener` and are non-polling.
  - `custom_components/solar_ac_controller/__init__.py` — config entry setup, `Store` usage, shared `coordinator` instance, service registration (`reset_learning`, `force_relearn`), and device registration.

Data & control flow (concrete):

  The coordinator calls the appropriate `turn_on`/`turn_off` service for the entity's domain and falls back to `climate` when needed.
 - Manual override lock: when a zone is manually changed the coordinator sets a lock to avoid fighting the user. The default lock is 1200s (20 minutes) and is configurable via the `manual_lock_seconds` option.
 - Manual override lock: when a zone is manually changed the coordinator sets a lock to avoid fighting the user. The default lock is 1200s (20 minutes) and is configurable via the `manual_lock_seconds` option.
 - Short-cycle protection: separate configurable delays apply after a zone was turned `on` vs `off`. Use `short_cycle_on_seconds` and `short_cycle_off_seconds` options to tune how long the controller avoids rapid add/remove decisions after a recent change.
 - Short-cycle protection: separate configurable delays apply after a zone was turned `on` vs `off`. Use `short_cycle_on_seconds` and `short_cycle_off_seconds` options to tune how long the controller avoids rapid add/remove decisions after a recent change.
 - Sequential action delay: to avoid hammering cloud APIs when the integration turns multiple zones in sequence (for example during panic shed), use `action_delay_seconds` to add a delay between consecutive `turn_on`/`turn_off` calls (default 3s).

Integration/service points to reference in edits

Developer workflows & commands (discovered)
```bash
pip install -r requirements_dev.txt
black .
pylint custom_components/solar_ac_controller
```

  - All power sensors are expected in Watts (`W`).
  - `grid_sensor` reports grid import as positive, export as negative. The coordinator treats export as `-ema_30s` when computing available export for zone add decisions.
 - Panic configuration:
   - `panic_threshold` (W, positive = grid import) — controller enters panic when `ema_30s > panic_threshold` and `on_count > 1`.
   - `panic_delay` (seconds) — how long the condition must persist before panic executes.
   - Recommended defaults: `panic_threshold=1500`, `panic_delay=30`.

What to watch for when editing

Examples (copyable patterns)
  - subclass `_BaseSolarACSensor` in `sensor.py` and read `self.coordinator.ema_30s` in `state`.
  - call `await self.controller.start_learning(zone, ac_power_before)` then call an HA service with `await self.hass.services.async_call(..., blocking=True)`.

<!-- Copilot / AI agent instructions for contributors and automation -->
# Solar AC Controller — AI Coding Agent Guide

## Table of Contents
1. [Purpose](#purpose)
2. [Big Picture & Architecture](#big-picture--architecture)
3. [Data & Control Flow](#data--control-flow)
4. [Project Conventions & Patterns](#project-conventions--patterns)
5. [Integration/Service Reference Points](#integrationservice-reference-points)
6. [Developer Workflows](#developer-workflows)
7. [Sensors, Units, and Panic Config](#sensors-units-and-panic-config)
8. [Editing & Coding Conventions](#editing--coding-conventions)
9. [Examples (Copyable Patterns)](#examples-copyable-patterns)
10. [2026 Integration Lessons & Error Fixes](#2026-integration-lessons--error-fixes)
11. [Contributing & Update Policy](#contributing--update-policy)

---

## Purpose
Help AI agents and contributors be immediately productive when editing this Home Assistant integration.

## Big Picture & Architecture
- Home Assistant "service" integration. Main pieces:
  - `coordinator.py`: Central DataUpdateCoordinator (runs every 5s, reads sensors, computes EMAs, decides add/remove/panic actions)
  - `controller.py`: Learning and persistent logic (start/finish learning, reset, storage writes)
  - `sensor.py`, `binary_sensor.py`, `diagnostics.py`: Entity factories and debug/diagnostic exposures. Entities register listeners with `coordinator.async_add_listener` and are non-polling.
  - `__init__.py`: Config entry setup, `Store` usage, shared `coordinator` instance, service registration, device registration.

## Data & Control Flow
- External sensors (solar, grid, ac power) → coordinator `_async_update_data` → compute `ema_30s/ema_5m` → decide actions
- On add/remove, coordinator calls Home Assistant services: `climate.turn_on/off`, `switch.turn_on/off` (see `_add_zone`, `_remove_zone`, `_handle_master_switch`)
- Learned values are kept in `coordinator.learned_power` and persisted via `Store` (see `__init__.py` and `controller._save`)

## Project Conventions & Patterns
- **Zones:** List of HA entity IDs (e.g., `climate.*`, `switch.*`, `fan.*`). Zone "short name" is extracted by `zone.split('.')[-1]`; learned values keyed by short name. The order of zones selected in the config/options flow is preserved and used as zone priority throughout the integration (first = highest priority).
- **Manual override lock:** When a zone is manually changed, the coordinator sets a lock to avoid fighting the user. Default lock is 1200s (20 min), configurable via `manual_lock_seconds`.
- **Short-cycle protection:** Separate configurable delays after a zone is turned `on` vs `off` (`short_cycle_on_seconds`, `short_cycle_off_seconds`).
- **Sequential action delay:** Use `action_delay_seconds` to add a delay between consecutive `turn_on`/`turn_off` calls (default 3s).
- **Options flow:** Runtime options are merged over stored data in `__init__.py` with `config = {**entry.data, **entry.options}` — always read options from `config` after that merge.
- **Coordinator-centric design:** State and behavior live on `SolarACCoordinator` (EMA, learned_power, samples, locks, last_action). Entities read state from the coordinator and call `async_write_ha_state` via `async_add_listener`.
- **Logging:** Use structured tag taxonomy in `_log` calls (e.g., `[LEARNING_BOOTSTRAP]`, `[PANIC_SHED]`).
- **Non-polling entities:** Set `_attr_should_poll = False` and rely on coordinator callbacks.

## Integration/Service Reference Points
- `__init__.py`: Config entry lifecycle, service registration, `Store` usage
- `coordinator.py`: Main loop, decision logic, EMAs, panic and learning flow
- `controller.py`: Learning algorithm and persistence
- `sensor.py`: How entity classes expose coordinator data and register listeners
- `manifest.json`: Integration metadata (integration_type, platforms)

## Developer Workflows
- **Formatting & linting:**
  ```bash
  pip install -r requirements_dev.txt
  black .
  pylint custom_components/solar_ac_controller
  ```
- **Tests:** Run unit tests with `pytest` if present.
- **Home Assistant:** Intended to run inside Home Assistant; manual install steps in `README.md`.

## Sensors, Units, and Panic Config
- All power sensors are expected in Watts (`W`).
- `grid_sensor` reports grid import as positive, export as negative. The coordinator treats export as `-ema_30s` when computing available export for zone add decisions.
- **Panic configuration:**
  - `panic_threshold` (W, positive = grid import): Controller enters panic when `ema_30s > panic_threshold` and `on_count > 1`.
  - `panic_delay` (seconds): How long the condition must persist before panic executes.
  - Recommended defaults: `panic_threshold=1500`, `panic_delay=30`.

## Editing & Coding Conventions
- Maintain coordinator as single source of truth. Avoid duplicating decision logic across entities.
- Preserve `async` semantics — use `async_call` with `blocking=True` only where existing code expects synchronous completion (see `_add_zone` / `_remove_zone`).
- When changing storage layout, increment `STORAGE_VERSION` in `const.py` and add migration reasoning in commit message. Example:
  ```python
  # In const.py
  STORAGE_VERSION = 2  # Incremented for new learned_power structure
  ```
- Unit of learned values: `learned_power` values are whole-Watt integers; ensure computations and stored values remain numeric.
- Use type hints (PEP 484/526) and Google-style docstrings throughout for maintainability.
- Use `_LOGGER.exception` for error logging, especially for storage/setup errors.
- Use consistent bullet/numbering style and code blocks for all code/config examples.

## Examples (Copyable Patterns)
- **Add a new sensor that reflects `ema_30s`:**
  ```python
  class MyEMASensor(_BaseSolarACSensor):
      @property
      def state(self):
          return self.coordinator.ema_30s
  ```
- **Start a controller action from coordinator:**
  ```python
  await self.controller.start_learning(zone, ac_power_before)
  await self.hass.services.async_call(..., blocking=True)
  ```
- **Minimal DeviceInfo for new entities:**
  ```python
  from homeassistant.helpers.device_registry import DeviceInfo
  self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
  ```
- **Error handling in async_setup_entry:**
  ```python
  try:
      old_data = await store.async_load()
      stored_data = await migrate_fn(STORAGE_VERSION, 0, old_data)
  except Exception:
      _LOGGER.exception("Failed to load stored data")
      stored_data = None
  ```

## 2026 Integration Lessons & Error Fixes

### Config Flow & Options Flow
- Always pass a list of domains directly to the entity selector for multi-domain selection. Do **not** use `selector({"select": ...})[...][...]`—this causes a TypeError. Example:
  ```python
  selector({"entity": {"domain": ["climate", "switch", "fan"], "multiple": True}})
  ```
- Set defaults in the schema using `vol.Optional(..., default=...)`, not with `vol.Default` (which does not exist in voluptuous).

### Store Usage & Data Migration
- Use `Store.async_load()` to load stored data. If migration is needed, call your migration function manually after loading. Do **not** use `async_load_with_migration` (not present in HA Store).
  ```python
  old_data = await store.async_load()
  stored_data = await migrate_fn(STORAGE_VERSION, 0, old_data)
  ```

### Zone Order & Priority
- The order of zones selected in the config/options flow is preserved and used as zone priority throughout the integration. The first zone in the list has the highest priority for add/remove decisions.

### Diagnostics & Device Registry
- All entities should use `DeviceInfo` for device registry. Do not set manufacturer/model fields unless required. Use consistent device registration for all entities.
- Diagnostics sensor and JSON export should be unified and reflect the same state.

### Error Handling & Logging
- Always log exceptions with `_LOGGER.exception` for storage and setup errors.
- If a config flow or options flow error occurs, check for selector misuse or missing voluptuous features.

### General Coding Conventions
- Maintain coordinator as the single source of truth for state and logic.
- Use type hints and docstrings throughout for maintainability.
- When changing storage layout, increment `STORAGE_VERSION` and document migration reasoning.
- All power values are in Watts (W); grid import is positive, export is negative.

## Contributing & Update Policy
- Propose changes via PR; run all tests and linters before submitting.
- For architectural or design questions, contact the maintainer.
- Update this document after major refactors, Home Assistant version changes, or when new best practices are established.

---

If anything here is unclear or you need more details (tests, CI, or reasoning for a specific logic branch), tell me which area to expand and I will iterate.

# 2026 Integration Lessons & Error Fixes

## Config Flow & Options Flow
- When using Home Assistant selectors, always pass a list of domains directly to the entity selector for multi-domain selection. Do **not** use `selector({"select": ...})[...][...]`—this causes a TypeError. Example:
  ```python
  selector({"entity": {"domain": ["climate", "switch", "fan"], "multiple": True}})
  ```
- Set defaults in the schema using `vol.Optional(..., default=...)`, not with `vol.Default` (which does not exist in voluptuous).

## Store Usage & Data Migration
- Use `Store.async_load()` to load stored data. If migration is needed, call your migration function manually after loading. Do **not** use `async_load_with_migration` (not present in HA Store).
  ```python
  old_data = await store.async_load()
  stored_data = await migrate_fn(STORAGE_VERSION, 0, old_data)
  ```

## Zone Order & Priority
- The order of zones selected in the config/options flow is preserved and used as zone priority throughout the integration. The first zone in the list has the highest priority for add/remove decisions.

## Diagnostics & Device Registry
- All entities should use `DeviceInfo` for device registry. Do not set manufacturer/model fields unless required. Use consistent device registration for all entities.
- Diagnostics sensor and JSON export should be unified and reflect the same state.

## Error Handling & Logging
- Always log exceptions with `_LOGGER.exception` for storage and setup errors.
- If a config flow or options flow error occurs, check for selector misuse or missing voluptuous features.

## General Coding Conventions
- Maintain coordinator as the single source of truth for state and logic.
- Use type hints and docstrings throughout for maintainability.
- When changing storage layout, increment `STORAGE_VERSION` and document migration reasoning.
- All power values are in Watts (W); grid import is positive, export is negative.

If anything here is unclear or you need more details (tests, CI, or reasoning for a specific logic branch), tell me which area to expand and I will iterate.
