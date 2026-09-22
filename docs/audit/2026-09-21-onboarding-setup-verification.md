# Clean-checkout setup verification

Date: 2026-09-21. Started from a fresh Git worktree at
`614431aef8ec139751ed85b211d7eae31358f708`, without copied dependencies or build
outputs. The original checkout and operator state were left unchanged. Tests
used macOS arm64, Python 3.14 and Node 24. The host's default Node was 26, so a
separate Node 24 executable was put on PATH for npm commands.

This verifies contributor setup and a frozen-backend smoke path. It is not a
clean-machine installation or signed-release acceptance receipt.

## Reproduced failures and fixes

1. **Interpreter selection.** The original `make setup` called `python3`, which
   created a Python 3.8.3 venv on this machine. Installing the 3.14 lockfile then
   failed with “No matching distribution found for annotated-doc==0.0.5”. The
   dependency was available for the intended interpreter. Make now defaults to
   `python3.14`; after moving aside the failed environment, plain `make setup`
   succeeded with a fresh venv. Explicit `PYTHON` overrides still work.
2. **Configuration directory.** Copying the sample into a fresh profile-like
   `.config/club/.env` failed because its parent did not exist. Guides now create
   the directory and copy only if the file is absent. Both new-file and
   preserve-existing-file commands were executed successfully; the config is
   mode 0600. A `cp -n` variant was discarded because it returns nonzero on this
   Mac when preserving a file.
3. **Isolated browser authentication.** Vite always read the default token even
   when the backend used `CLUB_STATE_DIR`. Three new tests failed before the
   change. Vite now resolves the override, including `~`/`~/`, and returns no
   token if that isolated file is absent. Six tests pass, covering those cases,
   default-path behavior, explicit-token precedence, and omission from builds.
   The shared test setup now guards DOM-only shims so Node config tests can run.
4. **Native development resources.** `cargo check --locked` on the clean checkout
   failed because `resources/sourcecado-backend` was absent. The development npm
   script now passes `tauri.dev.conf.json` with an empty resource list. The debug
   shell still launches the development venv. Default release configuration
   retains its required frozen resource.
5. **Packaging interpreter.** `build_backend.sh` independently selected
   `python3`, ignoring Make's choice. Make now passes `PYTHON`, and direct script
   use defaults to `python3.14`. `make build-backend` completed successfully and
   produced the frozen backend using the locked dependencies.
6. **Smoke harness environment.** The original `make smoke-test` used global
   Python; it reached health/auth checks, then failed importing `websockets`.
   Make now runs the harness with `desktop/.venv/bin/python`, where setup
   installed its dependencies. The complete smoke test passed on rerun.

## Verification results

Commands are from the repository root unless the table says otherwise.

| Check | Result |
| --- | --- |
| `make setup`, with Node 24 on PATH | Passed from a new venv and installed GUI dependencies with `npm ci`. |
| Documented configuration copy | Created absent config, preserved existing contents, both commands returned success; mode 0600. |
| `make test-python` | **1,954 passed, 3 skipped**, one Starlette/httpx deprecation warning; 75.41 seconds. |
| `make test-gui` | **582 passed across 63 test files**, including 6 new token tests; 33.21 seconds. |
| `make build` | TypeScript check and Vite bundle passed; existing large-chunk warning remains. |
| `make eval-sourcing` | **15/15 deterministic sourcing scenes passed** using fake providers/connectors. |
| `make backend` + `make gui` | Started against a newly created external state directory with blank sample config and a minimal environment without provider/connector keys. Authenticated health returned `status: ok`, `model: null`. |
| Headless Chromium browser check | Loaded `http://127.0.0.1:5180/`, waited for network idle, observed sourcing conversation and navigation. No page JavaScript errors. Screenshot captured locally, not committed. |
| `TAURI_CONFIG='{"bundle":{"resources":[]}}' cargo check --locked` in `src-tauri` | Passed before a frozen resource was built. |
| `TAURI_CONFIG='{"bundle":{"resources":[]}}' cargo test --locked` in `src-tauri` | Passed; **zero Rust tests are defined**, so this is compilation/doc-test evidence only. |
| `make native` | Compiled and launched the debug desktop binary; its child backend log showed HTTP 200s and accepted chat WebSockets. Test-owned processes were stopped afterward. |
| `make build-backend` | Frozen arm64 backend built successfully. |
| `make smoke-test` | Health passed; unauthenticated request rejected; isolated state created; retained-person chat completed before and after backend restart; backend exited without an orphan. Provider was a local fake. |

The two review agents additionally ran focused suites. Those overlap the full
suites above and must not be added to the totals as unique tests.

## Remaining limits and observations

- The native accessibility inspection tool timed out. Native startup and
  frontend/backend communication are verified; native visual acceptance is not.
- The frozen backend was built and exercised directly. A full `Sourcecado.app`
  release bundle, signing/notarization, clean-user install, update and rollback
  were not tested in this pass. The remaining human release checklist is #134.
- No live model response, OAuth authorization, Gmail send, Apollo credit spend,
  or live connector journey was attempted. Missing-provider behavior is expected
  in the credential-free development environment.
- npm's install audit reported five dependency findings: three moderate, one
  high, one critical. No blind major-version upgrade was made. Resolve and
  remeasure them with the quality-gate/dependency work in #166.
- This was a fresh checkout on an existing development Mac, not a newly
  provisioned operating system. A teammate rehearsal remains useful for
  toolchain installation and human comprehension.

See [the onboarding guide](../../CONTRIBUTING.md), [reconciled backlog](2026-09-21-onboarding-backlog.md),
and [documentation upkeep receipt](../engineering/onboarding-docs-upkeep.md).

## Pre-ship verification after repository cleanup

The active paths are now `backend/`, `frontend/` and `docs/engineering/`. The
initial checks above predate that move. Final review caught and fixed the old
public-brand symlink escaping into the neighboring checkout, a runtime Vite
token fallback that could embed a development token in production, and a native
command missing its frontend prefix. The token regression test failed before
the fix and passed after it; it builds the shipped app in memory and inspects
the output, rather than only checking a config value.

Fresh checks passed: 1,954 Python tests (3 skipped), 583 GUI tests, all 15 sourcing
scenes, frontend type checking/build, Rust format/clippy and compilation tests
(the Rust suite still defines zero tests), and 133 current-document links. A
clean export installed its own dependencies and built with assets from its own
checkout; a dummy VITE_CLUB_TOKEN was absent from production output.

The full Sourcecado.app bundle also built successfully. Its bundled backend
passed health/authentication, isolated-state, retained-person chat before and
after restart, and process-exit smoke checks. This does not certify Developer ID
signing, notarization, native visual acceptance, clean-user installation,
updates/rollback, or live Gmail/Apollo behavior.

One unchanged Scheduled history-selection test failed once during a suite run,
then its focused suite and the full GUI suite passed. Both the component and
test are byte-for-byte unchanged from the reviewed main revision; this remains
an observed intermittent failure rather than a repaired behavior.
