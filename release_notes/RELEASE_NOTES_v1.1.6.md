# Version 1.1.6

## Fixes

- **Config entry no longer breaks after a restart.** The config flow declared
  `VERSION = 1` while the migration wrote `version = 2` into the stored entry.
  On the next start Home Assistant saw an entry newer than the handler
  understood, refused to load it, and the integration stopped working until it
  was deleted and re-added. The flow now declares `VERSION = 2`.

- **Persisted idle-power baseline survives a restart.** The learned-data loader
  read the idle power, its sample count and the zone action history from
  storage, and the runtime-state initialiser then immediately overwrote all
  three with defaults. The idle-power sample count therefore always reset to
  zero, so the learned-baseline branch of the master-relay spindown guard could
  never be reached, the "Learned Idle Power" sensor always read 0, and the zone
  action history was emptied on every restart.

- **Configuration values load before the learned-data loader.** A stored zone
  entry missing one of `default` / `heat` / `cool` made the loader read
  `initial_learned_power` before that attribute existed. The initialiser order
  is corrected so the fallback cannot raise `AttributeError`.

## Internal

- Migrated CI to [uv](https://docs.astral.sh/uv/) with a committed `uv.lock`,
  dependency caching, and `uv lock --check` to catch lockfile drift.
- Extended `ruff` and `mypy` coverage to the test suite, resolving roughly 120
  pre-existing type errors.
- Pinned all third-party GitHub Actions to commit SHAs and added Dependabot to
  keep those pins current.
- Added a workflow validator that fails on an unresolvable action reference or
  an input an action does not declare, plus a weekly job that reports when a
  newer stable Home Assistant release is available.
