# DU-01 ExternalStoreRuntime go/no-go

**Verdict: GO**

- Pinned runtime: `@assistant-ui/react` `0.15.16` (exact). Its peer range is React 18 or 19; this proof resolved and ran with React/React DOM `18.3.1` only.
- Fixture result: legacy text/tool records and proposed structured text, tool, approval, partial, and cancelled states retain stable message/part identities across live replay and restore.
- Host result: thread-scoped deltas, targeted tool/approval callbacks, and queue cancellation all preserve Sourcecado authority. Cancel pauses and keeps queued work; only an explicit resume dispatches the next item.
- UI result: the real `ExternalStoreRuntime` renders and invokes callbacks through minimal custom Warm Operator proof components. No stock registry or theme was adopted.

Verification on 2026-08-25:

- `npm test -- tests/messageAdapter.test.ts tests/store.test.ts tests/queuePolicy.test.ts tests/runtimeProof.test.tsx` — 4 DU-01 files, 21 tests passed.
- `npm run build` — TypeScript and Vite production build passed.
- `cd desktop && .venv/bin/pytest -q` — 158 passed, 1 skipped; one existing Starlette deprecation warning.

Known limitation: this is an isolated contract proof, not the production transcript replacement. The future backend adapter must persist queue/order and drive the tested busy, settle, cancel, and explicit-resume edges. `@assistant-ui/react` installs its upstream `assistant-cloud` package transitively; Sourcecado adds no direct Cloud dependency, import, configuration, persistence, or authentication and remains entirely host-owned.

Concurrent-checkout note: the last unfiltered `npm test` observed 25 passes plus DU-02's intentional in-progress Settings-route RED. DU-02 owns that shell test and implementation; it is excluded from this DU-01 verdict at the root agent's direction. The shared production build still passes.
