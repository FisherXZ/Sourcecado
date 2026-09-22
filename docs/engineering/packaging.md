# Building the macOS preview artifact

Design decisions and their reasoning live in [ADR 0003](adr/0003-macos-preview-artifact-packaging.md). This is the practical how-to.

## Prerequisites

- Rust stable with `rustfmt` and `clippy` components (`rustup component add rustfmt clippy`).
- Python 3.14 with pip. The build script installs `backend/requirements.lock` with `--require-hashes` in a temporary virtual environment.
- Node 24 (pinned in the root `.nvmrc`) with `frontend/node_modules` installed (`npm ci`).

## Native development

After [contributor setup](../../CONTRIBUTING.md), run `make native` from the repository root with your isolated `CLUB_STATE_DIR` exported. It invokes `npm run tauri:dev`, which passes `src-tauri/tauri.dev.conf.json` and clears `bundle.resources` for this development invocation. No frozen backend is needed: the debug shell starts `backend/.venv/bin/python -m coworker.run`.

Stop any separately launched backend and Vite server first. Tauri starts both for native development. Bare `tauri dev` does not load the override automatically.

For a direct Rust development check before the resource exists:

```sh
cd frontend/src-tauri
TAURI_CONFIG='{"bundle":{"resources":[]}}' cargo check --locked
```

This override is for development checks only. Release builds must retain the bundled resource.

## Build

From the repository root, after `make setup`:

```sh
make build-backend   # freezes backend/coworker into frontend/src-tauri/resources/sourcecado-backend/
npm --prefix frontend run tauri -- build
```

The build uses Python 3.14 by default, matching `make setup`. To select an explicit interpreter, use `make build-backend PYTHON=/absolute/path/to/python3.14`.

The default `tauri.conf.json` declares `bundle.resources: ["resources/sourcecado-backend"]`. Tauri validates that path during compilation, so `make build-backend` must precede a packaged build or a direct Cargo invocation using the default config. The development override above is the explicit exception.

The result is `frontend/src-tauri/target/release/bundle/macos/Sourcecado.app` — a self-contained app that does not reference this repository checkout or `backend/.venv` at runtime.

## Verify

```sh
make smoke-test
```

This uses the project virtual environment created by `make setup`; the smoke
harness needs its WebSocket dependency even though the backend itself is frozen.

Or point `backend/packaging/smoke_test.py` directly at the built app's backend:

```sh
backend/.venv/bin/python backend/packaging/smoke_test.py \
  "frontend/src-tauri/target/release/bundle/macos/Sourcecado.app/Contents/Resources/resources/sourcecado-backend/sourcecado-backend"
```

The smoke test launches the backend exactly as the shell does (loopback only, isolated `CLUB_STATE_DIR`, in-memory token, then `kill()`), checks the health/auth handshake, confirms isolated state was created, and confirms the process leaves no orphan behind.

## Known gaps

- No code signing or notarization has run. The CI steps for signing, notarization, stapling, and independent verification are written in the `macos-preview` job and guarded on the signing credentials being present, so a run without them warns and produces an unsigned build. Until an authorized Apple identity is supplied, Gatekeeper still requires right-click → Open on first launch. See [the preview channel notes](update-channel.md) for exactly what has to be supplied.
- `bundle.targets` is `["app"]` only. DMG creation depends on Finder AppleScript automation that is not reliable in non-interactive sessions (see ADR 0003) and is not required by any acceptance criterion.
- Verified on `aarch64-apple-darwin` only.

The September 21 onboarding checks verified native development startup and backend HTTP/WebSocket connections, then rebuilt and smoke-tested the frozen backend, including retained-person chat before and after restart. Pre-ship verification subsequently built the full local Sourcecado.app bundle and smoke-tested its bundled backend. Developer ID signing, notarization, native visual acceptance, clean-user installation and updates/rollback remain unverified. See the [setup verification receipt](../audit/2026-09-21-onboarding-setup-verification.md).

## Updating an installed preview build

The artifact this page builds is what an update manifest describes. How that
manifest is signed and verified, when an update is allowed to interrupt a
running Sourcecado, and how to roll one back are in
[update-channel.md](update-channel.md).
