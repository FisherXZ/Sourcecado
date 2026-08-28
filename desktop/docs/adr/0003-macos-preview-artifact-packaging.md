# Package the desktop shell as a reproducible macOS preview artifact

Status: Proposed — 2026-08-27

## Context

The Tauri shell only ran in development: `lib.rs` shelled out to `desktop/.venv/bin/python -m coworker.run` from a path computed off `CARGO_MANIFEST_DIR`, `tauri.conf.json` had no CSP and carried unused window capabilities, `desktop/requirements.txt` was unpinned with no hashes, and the app's product name, bundle identifier, and version were split across three files with two different names ("Club" in the shell, "Sourcecado" everywhere else) and two different version strings. None of that runs on a clean Mac without a repository checkout.

This work (issue #76) makes the shell buildable and runnable standalone. It does not touch `desktop/coworker/` itself — connectors, tools, persona, and store code are owned by parallel lanes.

Issue #76 is formally blocked by #73 (Doctor / versioned migration registry). Only the state-location half of "one product name, identifier, version, icons, and migration-compatible state location" actually depends on it; everything else here is independent.

## Decision

- **Lock**: `desktop/requirements.lock`, generated with `uv pip compile --generate-hashes --python-version 3.14`, replaces the unpinned `requirements.txt` as the install source for both CI and the sidecar build. Verified to install cleanly with `pip install --require-hashes`.
- **Sidecar bundling: PyInstaller `--onedir` as a Tauri bundle resource, not `--onefile` as an `externalBin`.** A onefile build's macOS bootloader forks a second process to run the real interpreter and hands the caller the bootloader's PID; killing that PID leaves the real sidecar running, orphaned (verified: killing a onefile build's PID left a second PID still bound to the port). onedir has no extraction step and no fork — the spawned PID is the one that answers requests and the one a later `kill()` actually stops (verified the same way, clean). The onedir tree is bundled as `desktop/surfaces/gui/src-tauri/resources/sourcecado-sidecar/` via `bundle.resources`, not `bundle.externalBin` (externalBin requires a single file).
- **Dev/release split lives in `lib.rs` via `#[cfg(debug_assertions)]`**, not a runtime flag: `tauri dev` still shells out to `desktop/.venv/bin/python -m coworker.run`; a release build resolves the bundled sidecar through `app.path().resolve("sourcecado-sidecar/sourcecado-sidecar", BaseDirectory::Resource)`. Neither path references the other environment.
- **Branding: one name, "Sourcecado," everywhere** — `tauri.conf.json` productName/identifier, the Cargo package/lib names, the window title, tray tooltip, and menu item. `tauri.conf.json`'s `version` key is removed so Tauri falls back to `Cargo.toml`'s `package.version`, per Tauri's own documented precedence — one version source instead of three independent literals.
- **CSP**: `app.security.csp` moves from `null` to a restrictive policy (`default-src 'self'`, plus `connect-src ... http://127.0.0.1:* ws://127.0.0.1:*` for the sidecar's dynamic loopback port, since the frontend only reaches it through `window.__CLUB_HTTP__`/`fetch`/`WebSocket`, never through Tauri IPC).
- **Capabilities trimmed to `core:default` plus `dialog:allow-open`.** Workspace Settings calls `window.__TAURI__.dialog.open` (the dialog plugin maps that to `plugin:dialog|open`), so the main window still needs `dialog:allow-open`. An import search for `@tauri-apps/api` or plugin-dialog missed the global. Window hide/show/focus in `lib.rs` are direct Rust API calls, not JS-invoked commands, so they need no capability grant.
- **State location reused verbatim.** `state_dir()` (both the Rust and the untouched Python copy) still resolves to `CLUB_STATE_DIR` or `~/.config/club`. This packaging change does not rename it, invent a schema version, or add a migration step — that is #73's job. Wiring note for #73: whatever versioned-migration entry point it adds should run against this same path; nothing here assumes a different one.
- **CI**: a new macOS job in `.github/workflows/ci.yml` runs `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`, the GUI test/build steps, the Python suite, `build_sidecar.sh`, and a full `tauri build`, then runs `smoke_test.py` against the bundled sidecar and records artifact checksums.

## Accepted risks / left for later

- Built and tested only for `aarch64-apple-darwin` (this host, and GitHub's current macOS runners). `x86_64-apple-darwin` is unverified.
- Unsigned: no code-signing identity or notarization. Gatekeeper will require a right-click-Open on first launch. Acceptable for a preview artifact per issue #76's scope; a signing identity is a separate, later decision.
- onedir's first request takes a few seconds (interpreter/import warm-up) before the health endpoint answers, versus a warm dev server. Not addressed here.
- The state directory is still named `club`, not `sourcecado`. Renaming it is a migration, and migrations belong to #73's registry, not this change.
- Real Sourcecado icon artwork was not created; `icons/` still ships Tauri's placeholder set. The bundle icon configuration is already a single consistent array — only the artwork itself is a design gap.
- **`bundle.targets` is `["app"]`, not `["dmg"]`** (a pre-existing default this change removes). `tauri build` produces a working `.app` reliably, but DMG creation fails deterministically here: `bundle_dmg.sh`'s AppleScript step errors with `Finder got an error: Can't set statusbar visible of container window... (-10006)`, a documented Finder-automation/TCC permission failure on non-interactive macOS sessions, not a Sourcecado defect. It is a known flakiness source for Tauri's `create-dmg`-based DMG bundling on headless macOS CI runners. None of the 9 acceptance criteria require a `.dmg`; the `.app` satisfies "reproducible native macOS preview artifact" and is what the smoke test runs against. Producing a `.dmg` again is a later, separate decision (naturally paired with signing anyway).

## Consequences

`tauri build` produces a `Sourcecado.app` that starts its own sidecar without a repository checkout or `desktop/.venv`, using dependencies resolved from a hash-verified lock. The dev workflow (`tauri dev`, `make native`) is unchanged. Signing, a `.dmg`, Intel support, and the state-directory migration remain open follow-ups outside this issue's scope.
