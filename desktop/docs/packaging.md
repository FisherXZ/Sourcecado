# Building the macOS preview artifact

Design decisions and their reasoning live in [ADR 0003](adr/0003-macos-preview-artifact-packaging.md). This is the practical how-to.

## Prerequisites

- Rust stable with `rustfmt` and `clippy` components (`rustup component add rustfmt clippy`).
- Python 3.14 and `uv` (or any tool that can install `desktop/requirements.lock` with `--require-hashes`).
- Node (version pinned in `.nvmrc`) with `desktop/surfaces/gui/node_modules` installed (`npm ci`).

## Build

```sh
make build-sidecar   # freezes desktop/coworker into desktop/surfaces/gui/src-tauri/resources/sourcecado-sidecar/
npm --prefix desktop/surfaces/gui run tauri -- build
```

`make build-sidecar` must run before any `cargo build`/`cargo check`/`cargo test`/`cargo clippy` in `src-tauri`, including for `tauri dev`. `tauri.conf.json` declares `bundle.resources: ["resources/sourcecado-sidecar"]`, and Tauri's build script validates that path exists at compile time regardless of profile — there is no separate "dev doesn't need it" exemption. This is a new local prerequisite `tauri dev` didn't have before this change.

The result is `desktop/surfaces/gui/src-tauri/target/release/bundle/macos/Sourcecado.app` — a self-contained app that does not reference this repository checkout or `desktop/.venv` at runtime.

## Verify

```sh
make smoke-test
```

Or point `desktop/packaging/smoke_test.py` directly at the built app's sidecar:

```sh
python3 desktop/packaging/smoke_test.py \
  "desktop/surfaces/gui/src-tauri/target/release/bundle/macos/Sourcecado.app/Contents/Resources/resources/sourcecado-sidecar/sourcecado-sidecar"
```

The smoke test launches the sidecar exactly as the shell does (loopback only, isolated `CLUB_STATE_DIR`, in-memory token, then `kill()`), checks the health/auth handshake, confirms isolated state was created, and confirms the process leaves no orphan behind.

## Known gaps

- No code signing or notarization. Gatekeeper requires right-click → Open on first launch. Not required for this preview-build slice (issue #76, criterion 9).
- `bundle.targets` is `["app"]` only. DMG creation depends on Finder AppleScript automation that is not reliable in non-interactive sessions (see ADR 0003) and is not required by any acceptance criterion.
- Verified on `aarch64-apple-darwin` only.
