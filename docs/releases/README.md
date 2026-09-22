# Release records

[CHANGELOG.md](CHANGELOG.md) preserves the project history. Its older entries
describe retired implementations. Paths in dated entries reflect their time.

The active app version is in [frontend/package.json](../../frontend/package.json)
and [the native package](../../frontend/src-tauri/Cargo.toml). CI adds its build
number for preview artifacts.

[legacy-hosted-version.txt](legacy-hosted-version.txt) preserves the old root
VERSION value, 0.2.0.0. The current build does not read it.
