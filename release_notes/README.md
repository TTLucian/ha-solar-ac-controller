# Release notes

`.github/workflows/release-drafter.yml` publishes a draft release for the
version in `custom_components/solar_ac_controller/manifest.json`, and reads the
notes body from a file named after that exact version:

```
release_notes/RELEASE_NOTES_v<VERSION>.md
```

For example, manifest version `1.1.6` reads `RELEASE_NOTES_v1.1.6.md`.

## Why the file is required

A stable version whose notes file is missing produces **no release at all**, and
the workflow raises a `::warning` annotation in the run summary. This is
intentional: shipping a release whose notes are silently empty is worse than
shipping nothing visible. Older releases predate this convention and were
published with empty notes.

## How to cut a release

1. Bump `version` in `manifest.json`
2. Create `release_notes/RELEASE_NOTES_v<new version>.md` and fill it in
3. Push to `main`

The workflow then creates (or updates) the draft release, attaches
`solar_ac_controller.zip`, and fills in the title and notes. Publish the draft
by hand when the notes are right.

## Prereleases

A version containing a hyphen (for example `1.2.0-beta1`) is treated as a
prerelease: the workflow skips it and logs that it is published manually.
