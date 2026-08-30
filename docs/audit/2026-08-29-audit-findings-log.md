# Sourcecado audit — sequential findings log

Working log for the 2026-08-29 architecture + code quality audit. Each entry is
appended in the order it was found. Final reports:
`2026-08-29-sourcecado-architecture-assessment.md` and
`2026-08-29-sourcecado-code-quality-report.md` in this folder.

Method modeled on the-hog-core-api audits (2026-07-07): measured output only,
every claim cited to file:line, verdict up front, strengths credited.

---

## Step 1 — Repo survey (measured)

- Active product: `desktop/` — Python/FastAPI sidecar (`coworker/`), React/Vite/Tauri GUI (`surfaces/gui/`), Rust shell 208 LOC.
- Prod Python (coworker + packaging): **43,549 LOC / 90 files**. Python tests: **50,585 LOC** → test/source ratio **1.16**.
- GUI src: **13,614 LOC / 52 files**. GUI tests: **15,026 LOC** → ratio **1.10**.
- 722 files tracked in git; packaged sidecar bundle under `src-tauri/resources/` is NOT committed (gitignored properly).
- `archive/hosted-web/` historical, excluded from audit scope (per AGENTS.md).

## Step 2 — Tooling config

- `desktop/pytest.ini` exists; **no ruff/flake8/mypy config anywhere** for 43.5k LOC of Python.
- GUI: **no ESLint config at all**; only gate is `tsc --noEmit` in `npm run build`. tsconfig is `strict: true` + `noUnusedLocals` + `noUnusedParameters` (good posture).
- `requirements.lock`: uv-compiled with `--generate-hashes`, installed with `--require-hashes`. Only 6 direct deps / 42 resolved. Excellent supply-chain floor.
- `package.json` name is **"club-gui"**, version 0.0.1 — naming drift from pre-rename era.

## Step 3 — CI (.github/workflows/ci.yml, 332 lines, single workflow)

- Jobs: sidecar (pytest + behavioural evals), gui (vitest + tsc build), macos-preview (both suites + cargo fmt/clippy -D warnings/test + sidecar build + Tauri build + packaged smoke test + gated signing/notarization + pip-audit + provenance/checksums + signed update manifest).
- **Gaps: no Python lint step, no TS lint step, no coverage run/threshold, no npm audit.** pip-audit runs (ci.yml:248-251). Rust IS lint-gated.
- macos-preview (30 min) runs on every PR — thorough but expensive per-PR.

## Step 4 — Measured quality metrics

- **ruff (default rules)**: 1 error (1 unused import) across prod Python. Effectively zero.
- **Cyclomatic complexity C901>15**: **19 functions** (server.py 3, provider.py 3, workspace_runtime.py 2, doctor.py 2, rest 1 each). Compare the-hog: 253.
- **Duplication (jscpd, min-5-lines/50-tokens)**: prod Python **1.55%** lines (70 clones); GUI src **2.15%** (40 clones). Both pass the 3-5% gate.
- **Python coverage (pytest --cov)**: **89% line coverage**, 17,356 statements. 1917 passed / 3 skipped in 210s. Lowest: packaging scripts 0% (never imported by tests), calendar.py 65%, evals/runner.py 66%.
- **GUI tests**: 520 passed / 58 files in 26s (vitest).
- **Hygiene markers (prod)**: 0 TODO/FIXME/HACK in Python, 0 in TS. 1 `type: ignore`, 2 `noqa`, 0 `as any` / `@ts-ignore`, 1 `: any`. Cleanest marker profile I have measured.
- **npm audit**: prod **0 vulnerabilities**; dev 5 (1 critical, 1 high) — all in vitest 2.1.9 → vite 5 → esbuild ≤0.24.2 chain. Fix is vitest 3.x upgrade.
- **pip-audit (requirements.lock)**: **No known vulnerabilities found.**
- **Licenses**: Python — all permissive (MIT/BSD/Apache/PSF; 1 MPL-2.0 = certifi-class, no copyleft obligation for local app). npm prod — 197 MIT + permissive only. **No GPL/AGPL/LGPL anywhere.**
- File-size bands, prod Python: 0-100: 17, 100-300: 36, 300-500: 14, 500-1000: 6, 1000-2000: 16, 2000+: 1. **17 of 90 files (19%) ≥1000 LOC** — debt is outlier concentration, worse ratio than the-hog (1.8%).
- Largest: server.py 3,687; store.py 1,946; turn.py 1,743; doctor.py 1,664; people.py 1,663; provider.py 1,610. GUI: api.ts 2,214.

## Step 5 — Architecture map (verified in source)

- Entry: `coworker/run.py` — loopback-only bind (refuses non-loopback, run.py:83-85), per-run token `secrets.token_hex(32)` written 0600 (run.py:44-48,66-74), orphan watchdog for Tauri parent (run.py:15-41), 30s scheduler tick daemon thread (run.py:110-120).
- `server.py:383 create_app()` — **all 63 HTTP routes + WS defined inside one closure** (server.py:1225-2829). The one 2000+ LOC file.
- Turn loop extracted to `turn.py` (WS chat + scheduler share it, turn.py:1). Tool exec via `asyncio.to_thread` (turn.py:1565).
- Providers: StreamProvider protocol, 5 impls — fake/anthropic/openai/deepseek/kimi (provider.py:279,603,953,1218,1364) + retry/failover chain (provider_retry.py), run budgets (run_budget.py), compaction (compaction.py).
- Permission policy single-sourced: AUTO/ASK/RETRY_SAFE frozensets + workspace conditional + MCP write-block (permissions.py:11-78,117-153). RETRY_SAFE documented as subset-of-AUTO invariant (permissions.py:44-46).
- Durable agent runs: agent_runs.db, leases, external-effect fence, quarantine, restart classification; dispatch seam `agent_run_dispatch.guarded_call`; two-store read rule in agent_run_reconcile (desktop/docs/agent-runs.md).
- HITL approved send: draft → re-read → send-approval with reviewed_body_digest + recipient binding (server.py:2443-2496, refusal `recipient_not_bound` at 2478-2486) → inbox decide; at-most-once via 4 independent mechanisms incl. atomic claim UPDATE (desktop/docs/approved-send.md).
- Workspace runtime: grants, typed FS tools, Docker-preferred shell, receipts, host-approval fingerprinting (ADR 0002, accepted risks documented honestly).
- Events: versioned presentation events v2 with replay identity, shared by live WS + HTTP restore (events.py:10-26, chat/protocol.ts:1-14).
- Migrations: registry owns PRAGMA user_version, stores own DDL, backup+rollback per step, doctor migrates deliberately (desktop/docs/agent-runs.md "Versioning").
- Update channel: signed manifests, drain, apply, rollback, credential-scan refusal (update_channel/, Makefile targets).

## Step 6 — Architecture findings (verified)

- **A-DUAL (High)**: `gmail_send` and `apollo_enrich_contact` each have TWO implementations with different guarantees. Thin path: tools.py:770-788 (send) / 812-869 (enrich), reached from turn.py via guarded_call — no recipient↔person binding, no reviewed-body digest, generic ledger event. Thick path: server.py:2443-2531 + people.py — recipient binding, body-version drift refusal, idempotent claim, sequence transitions, Source References. Which one runs depends on which UI path the call arrives through. Both are human-approved; the *guarantees* differ. test_approved_send.py: 20 tests on thick path; thin path has ~1 test asserting none of those properties. (Corroborated by tonight's coworker-walk observation #32500; verified directly in source.)
- **A-DB (Medium)**: five SQLite DBs (club.db store.py:209, people.db people.py:64, agent_runs.db agent_run_repository.py:65, drive_ingestion.db, meeting_evidence.db), each with own connection + RLock, no cross-DB transaction. A turn writes run row + inbox row + person row non-atomically; agent_run_reconcile.py exists precisely to read two stores at once — deliberate, but consistency is by convention. ConversationStore (club.db) alone carries sessions + transcripts + memories + inbox + scheduler + settings.
- **A-GOD (High)**: server.py 3,687 LOC / 63 routes in one closure; api.ts 2,214 LOC mirrors it in the GUI. The two files are the change-cost hotspot of the codebase.
- **A-FLAT (Medium)**: 60+ modules flat in one `coworker/` package; the 9 `agent_run_*` modules and 5 `workspace_*` modules are packages-by-prefix. Navigability currently OK because module docstrings are excellent.
- **A-NAME (Low)**: pre-rename "club" identity persists in load-bearing places: club.db, X-Club-Token, CLUB_EXIT_WITH_PARENT, `club-server` prog, club-gui package, "Club sidecar" banner (run.py:78,100; store.py:209). 15 sidecar files reference it. Migration cost grows with time (db filename is user state).
- **A-TICK (Low)**: scheduler tick thread catches bare Exception and prints traceback (run.py:110-118); entry logging is print-based. One bad tick pattern could repeat-crash silently in a packaged app where stdout goes nowhere.

## Step 7 — Informational scans (sizing the gates, not current-config findings)

- ruff broader (E,W,F,B,SIM,UP,C4): 382 total — 289 line-too-long (style), ~93 substantive incl. 28 deprecated-import, 20 suppressible-exception, **4 B023 function-uses-loop-variable (real bug class)**, 8 B008 (FastAPI default-arg idiom, mostly false-positive in this context).
- mypy --ignore-missing-imports: **86 errors in 20 files** (attr-defined, etc.) — latent type debt invisible today because no checker runs.
- Naming: 15 prod files reference "club".
- No secrets committed: only `.env.example` tracked; `.env.local`/state gitignored. Token file 0600, never logged.

## Step 8 — Existing issue overlap (do not re-file)

#127/#128/#131/#132/#133 (product-logic gaps), #134 (HITL QA checklist), #144/#145/#146 (attention loop), #79 (release program), #159-163 (UI P1/P2s). Audit tickets must add only NEW gaps: dual-path guarantees, CI quality gates, god-seam decomposition, club→sourcecado migration.

## Step 9 — Reports written, tickets filed

Reports:
- `docs/audit/2026-08-29-sourcecado-architecture-assessment.md`
- `docs/audit/2026-08-29-sourcecado-code-quality-report.md`

Tickets opened:
- **#165 (P1)** — dual-path gmail_send / apollo_enrich_contact guarantees (AR1)
- **#166 (P2)** — CI quality gates: ruff, ESLint, coverage floor, npm audit, vitest 3, incremental mypy, commit PR template (G1-G4)
- **#167 (P2)** — decompose server.py routes + api.ts + lift inbox out of ConversationStore (AR2/AR4)
- **#168 (P3)** — finish club→sourcecado rename in load-bearing state (AR6)

Deliberately NOT ticketed (report-only, one line each):
- Five-DB cross-store consistency (AR3): deliberate documented trade; agent_run_reconcile covers it; revisit only if a synced/multi-officer future is scheduled.
- Disk retention for transcripts/receipts (AR8): months of headroom for one operator; a doctor check someday.
- Tick-thread bare-except + print logging (AR7): two-line fix, cheaper to do in passing than to track.
- macos-preview 30-min job on every PR: cost note only; it buys real packaging safety.
