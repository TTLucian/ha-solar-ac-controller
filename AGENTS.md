# Agent Instructions

## Repository

- Upstream: `TTLucian/ha-solar-ac-controller`
- All PRs target upstream `main`
- Component path: `custom_components/solar_ac_controller/` (underscore, not
  `solar-ac-controller`)

## Branching

- Never commit directly to `main`
- Branch prefixes: `fix/` bugfixes, `feat/` features, `analysis/` research, `chore/` tooling and CI, `release/` version prep
- Rebase on latest `upstream/main` before pushing
- Confirm the branch before editing - `git rev-parse --abbrev-ref HEAD`. A `git checkout <branch> -- <paths>` in a compound command can leave you somewhere you did not intend.

## Pull Requests

- Always create PRs as **drafts** first
- One concern per PR - keep scope tight
- Draft -> ready fires `ready_for_review`; CI is configured to run on it

## Toolchain - uv, and the lockfile is the contract

`uv.lock` is the single source of truth. `--locked` matches CI: the committed
lockfile is exactly what gets tested.

```bash
uv sync --locked          # install; there is no "test" group, "dev" is default
uv run ruff check custom_components/solar_ac_controller tests
uv run ruff format --check custom_components/solar_ac_controller tests
uv run mypy custom_components/solar_ac_controller
uv run mypy --namespace-packages --explicit-package-bases tests/
uv run pytest
```

- `uv sync --group test` **fails** here - the group is named `dev`.
- `mypy` reads `pyproject.toml`. There is no `mypy.ini`; the two used to coexist
  and `mypy.ini` silently won, leaving the `pyproject.toml` settings dead.
- Never pass `--follow-imports=skip` to mypy. It reports false
  `untyped-decorator` errors on `@pytest.mark.asyncio` because it cannot see
  pytest's own types. The flags above are the ones that work.
- **Never install or run `black`.** Current black releases rewrite
  `except (TypeError, ValueError):` into `except TypeError, ValueError:`, which
  is a `SyntaxError`. It was removed from CI for this reason and must not come
  back.

## Testing

- Fix test failures before pushing - no PRs with known failing tests
- CI runs `pytest -q --junitxml=junit.xml` and uploads the report as an artifact
- There is **no coverage gate**. Current coverage is ~31%, mostly because
  `sensor.py`, `select.py`, `switch.py` and `number.py` (the HA entity
  platforms) have no tests. Do not quote a coverage floor that does not exist,
  and do not add `--cov-fail-under`: a gate that fails every run teaches people
  to ignore it. `pytest-cov` is available if coverage needs measuring.

## Workflows

Two guards exist because a bad workflow reference fails *silently*:

- `.github/scripts/validate_workflow_actions.py` - fetches each SHA-pinned
  action's `action.yml` and fails on an unresolvable reference or a `with:` key
  the action does not declare. GitHub **ignores unknown `with:` keys**, so a
  typo there looks like a passing job.
- `.github/workflows/actionlint.yml` - syntax and expression checking, including
  shellcheck rules, via the `rhysd/actionlint` container.

Both have caught real bugs here. When adding a workflow step, check the action's
real input names - do not copy them from `actions/setup-python` onto a different
action.

## Pre-commit (optional)

`.pre-commit-config.yaml` mirrors CI: ruff, ruff-format, mypy and actionlint on
commit, `uv lock --check` when the lockfile or `pyproject.toml` changes, and
pytest on pre-push. Keep the pinned `rev`s equal to the tool versions in
`pyproject.toml`/`uv.lock`, and the `files:` scope equal to what CI checks - a
hook that checks less than CI reads as a passing check while checking nothing.

The actionlint hook is `actionlint-docker`, pinned to the same image CI uses. It
needs Docker, and for a reason: plain actionlint silently skips every shellcheck
rule when shellcheck is absent locally, which is how unquoted `$GITHUB_OUTPUT`
redirections reach CI.

The mypy hook needs `--namespace-packages --explicit-package-bases`. Without
them it fails outright with "Source file found twice under different module
names", because it hands mypy individual file paths.

```bash
uv sync --locked && uv run pre-commit install
```

## Line endings - the tests are CRLF

Every file under `tests/` uses CRLF, and `manifest.json`, `strings.json` and
`translations/en.json` do too. A bulk edit that rewrites them as LF turns a
one-line change into a whole-file diff - roughly 1,600 insertions and 1,400
deletions instead of a handful of lines. When scripting an edit across these
files, preserve the line endings, and check `git diff --stat` before committing.

## JSON files - edit, never re-serialize

Do not read a repo JSON file with `json.load` and write it back with `json.dump`.
`manifest.json`, `strings.json` and `translations/en.json` all use hand-set
2-space indentation and CRLF endings; a serializer round-trip rewrites every
line and normalises the line endings, producing an enormous diff for a one-key
change.

Edit the specific line with the editor tool or a targeted `sed`. If a bulk edit
is genuinely needed, verify with `git diff --stat` that the change is
proportional - check the stat *before* committing, not after.

## Translations

- `custom_components/solar_ac_controller/translations/en.json` is the only
  translation file in this repository, and it is the source of truth.
- New UI strings go in `strings.json` **and** `translations/en.json`. Both are
  required; a key present in only one shows up untranslated in the UI.
- There is no translation script here. Do not add one by copying it from
  another repository.

## Releases

- A release needs `release_notes/RELEASE_NOTES_vX.Y.Z.md` or the release
  workflow creates nothing and raises a warning in the run summary.
- Prerelease versions (containing `-`) are skipped by the workflow and
  published by hand via the API.
- Bump `manifest.json` by editing the one `version` line, then tag only after the
  release merge lands on `main`.
- The drafter attaches `solar_ac_controller.zip` and updates an existing draft
  rather than skipping it.

## Home Assistant version pinning

`uv.lock` pins Home Assistant to a **pre-release** (`2026.10.0b0`), because
`pytest-homeassistant-custom-component` depends on that exact version. Latest
stable is behind it. This is deliberate - CI stays forward-compatible - but it
means CI does not test what most users actually run.

The weekly `Dependency freshness` job compares the locked version against the
newest stable release on PyPI and fails when one is available. It runs on
schedule and manual dispatch only, not on every push.

## Files to never commit

- `*.log`
- `*.txt` used as script output (`requirements.txt` was removed as redundant -
  `uv.lock` supersedes it)
- `config_entry-*.json` (diagnostics dumps)
- `__pycache__/`, `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`, `.coverage`
- `pyrightconfig.local.json` - but `pyrightconfig.json` **is** tracked, because
  without an explicit `pythonVersion` Pylance falls back to an older
  interpreter and reports errors that do not exist
- `.envrc`, `.subtask/`, `.claude/`
