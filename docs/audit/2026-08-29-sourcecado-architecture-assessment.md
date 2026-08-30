# Architecture Assessment — Sourcecado (`desktop/`)

> **Prepared:** 2026-08-29 · **Companion document:** `2026-08-29-sourcecado-code-quality-report.md` · **Focus:** system structure, coupling, failure surfaces, and fit for the product's stated scope (one sourcing director, one machine, multi-officer handoff later). · **Method:** source-level review of the repository as committed, with file:line citations. Runtime behavior on end-user machines (Docker availability, Gmail/Apollo quota state, packaged-app filesystem) is noted where it cannot be confirmed from the repo. · **Out of scope:** `archive/hosted-web/` (historical, per AGENTS.md).

---

## 1. Verdict Up Front

Sourcecado's active stack is a **well-architected local-first desktop system** (~43.5k LOC Python sidecar + ~13.6k LOC React GUI + 208-line Rust/Tauri shell) with an unusually strong safety spine: a single-source permission policy, durable agent runs with an external-effect fence, an at-most-once approved-send chain, and a workspace runtime with a written, honest threat model (ADR 0002).

**It fits its stated scope without restructuring.** The work to budget is targeted, not foundational. The three things that will bite first as the product grows: (1) **`gmail_send` and `apollo_enrich_contact` each exist twice** — a thin tool path and a thick approval path with materially different safety guarantees, selected implicitly by which UI path the call arrives through; (2) **two god seams** — `server.py` (3,687 LOC, all 63 routes in one closure) and `api.ts` (2,214 LOC) — concentrate nearly all change cost; (3) **five SQLite databases with no cross-store transaction**, held consistent by convention and a reconcile pass rather than by mechanism.

**Rewrite risk: none identified. Structural debt: concentrated and addressable incrementally.**

---

## 2. System Context

**What it is:** a local-first desktop assistant for a sourcing director: name a target, find people (Apollo), prepare tailored outreach, enrich deliberately (credit-aware), approve and send (Gmail), keep conversations moving, and leave a person file another officer can pick up. Chat is home; the board is the operating picture; the person file is the durable domain object.

**Stack:** Python 3.14 / FastAPI / uvicorn sidecar · React 18 / Vite / assistant-ui GUI · Tauri 2 (Rust) shell · SQLite + append-only JSONL state · PyInstaller-frozen sidecar shipped inside the app bundle · signed preview update channel.

```
 Operator (sourcing director)
      │
 ┌────▼─────────────────────────────────────────────┐
 │ Tauri shell (Rust, 208 LOC)                      │
 │   spawns sidecar, exit-with-parent watchdog      │
 │ ┌──────────────────────────────────────────────┐ │
 │ │ React/Vite GUI (13.6k LOC)                   │ │
 │ │  chat · board · person file · inbox ·        │ │
 │ │  quarantine · scheduled · settings           │ │
 │ └───────────────┬──────────────────────────────┘ │
 └─────────────────┼────────────────────────────────┘
        HTTP + WS  │  loopback 127.0.0.1:8765
        X-Club-Token (per-run, 0600 file)
 ┌─────────────────▼────────────────────────────────┐
 │ Python sidecar (coworker/, 43.5k LOC)            │
 │  create_app: 63 routes + /ws/chat                │
 │  turn loop · permission policy · agent runs ·    │
 │  inbox approvals · workspace runtime · scheduler │
 │  · doctor · migrations · update channel          │
 └──┬────────┬─────────┬─────────┬─────────┬────────┘
    │        │         │         │         │
 5× SQLite  JSONL   Gmail/    Apollo    LLM providers
 + receipts transcripts Drive/Cal (credits) (anthropic·openai·
 (~/.config/club)   (OAuth)              deepseek·kimi + failover)
```

---

## 3. Architectural Style

**Local sidecar monolith behind a thin native shell.** One Python process owns the API, the agent loop, policy, persistence, scheduling, and self-maintenance; the GUI is a rendering surface over a versioned event protocol; the Rust shell does process supervision only (`src-tauri/src/lib.rs`, 200 LOC). This is the right shape for a single-operator local product.

- **Loopback-only by construction** — `run.py:83-85` refuses to bind off loopback; per-run token generated with `secrets.token_hex(32)`, written mode 0600 (`run.py:44-48, 66-74`), compared constant-time with an origin allowlist (`server.py:339-349`).
- **Versioned presentation events** — event schema v2 with replay/routing identity shared by live WebSocket and HTTP restore (`events.py:10-26`, `chat/protocol.ts:1-14`). This is a real contract, not an ad-hoc stream.
- **Single-source permission policy** — `permissions.py` holds AUTO / ASK / RETRY_SAFE frozensets plus workspace-conditional and an MCP write-block; `RETRY_SAFE` is documented as a deliberate subset of AUTO so a retry can never cover an approval-gated tool (`permissions.py:44-46`).
- **Concurrency model** — uvicorn's asyncio loop; blocking tool and store work pushed through `asyncio.to_thread` (`turn.py:1565`, `server.py:830-831`); a 30s daemon thread ticks the scheduler (`run.py:110-120`); SQLite opened `check_same_thread=False` with per-store RLocks.

---

## 4. Component / Module Map

90 production modules, flat under `coworker/` plus four subpackages. Grouped by concern:

| Group | Modules |
|---|---|
| API + turn runtime | server (3,687), turn (1,743), events, run, provider (1,610), provider_retry, compaction (1,405), context_projection, prompt_contract, run_budget |
| Durable agent runs | agent_run_repository (1,322), agent_run_dispatch, agent_run_approval, agent_run_resume, agent_run_reconcile, agent_run_owner, agent_run_state, agent_runs |
| Domain: people/sourcing | people (1,663), board_tools, apollo, apollo_curation, reply_filing, brief (986), inbox, ledger, run_ledger, run_receipt, evidence_envelope, run_evidence |
| Connectors | gmail (1,058), drive, drive_ingestion (1,048), drive_evidence, calendar, connectors/google_oauth, mcp, mcp_oauth, web (Tavily), granola via MCP |
| Workspace runtime | workspace_runtime (1,083), workspace_files (1,083), workspace_shell (1,077), workspace_policy, workspace_audit, workspace, permissions |
| Evidence & compliance | meeting_evidence, legal_artifacts, bundle_redaction, secret_scan, diagnostic_bundle (1,113) |
| Self-maintenance | doctor (1,664), migrations (1,464), update_channel/ (manifest, drain, apply, redaction), telemetry/ |
| Automation & evals | automation/scheduler, builtin_skills/, personas/, evals/ (runner + sourcing scenarios, run in CI) |

**Genuine strength — documentation-per-subsystem:** every non-trivial subsystem has an engineering reference in `desktop/docs/` (agent-runs, approved-send, update-channel, evaluations, run-budgets, …) plus three ADRs. The docs state invariants and name the tests that assert them (e.g. `test_the_fence_step_never_commits_the_transaction_it_runs_inside`, desktop/docs/agent-runs.md). This is rare and materially lowers onboarding cost.

---

## 5. Request & Turn Lifecycle

### 5.1 Chat turn (the core path)

```
GUI ── WS /ws/chat (token + origin check)          [server.py:2829, :339-349]
  → turn loop (shared by WS chat and scheduler)    [turn.py:1]
  → agent run row + lease in agent_runs.db         [agent_run_repository.py]
  → provider stream w/ retry + failover chain      [provider_retry.py]
  → tool call → permissions.decide()               [permissions.py:117-153]
       AUTO  → execute via asyncio.to_thread       [turn.py:1565]
       ASK   → park approval in inbox; fence the
               external effect via guarded_call    [agent_run_dispatch.py]
  → versioned events streamed; JSONL transcript;
    telemetry spans; run budget metered            [events.py, telemetry/]
```

### 5.2 Approved send (the thick path — a modeled workflow, not a tool call)

```
POST /v1/people/{id}/outreach/draft      recipient comes from the person file;
                                         request cannot supply one     [server.py:2366]
GET  …/outreach/draft/{draft_id}         re-read the live draft (edited in Gmail)
POST …/outreach/send-approval            binds account+draft+recipient+subject+
                                         reviewed_body_digest; refuses drift and
                                         recipient_not_bound           [server.py:2443-2496]
POST /v1/inbox/{approval_id}             allow/deny; at-most-once via atomic
                                         claim UPDATE + 3 more mechanisms
                                         (desktop/docs/approved-send.md)
```

### 5.3 The finding: the thin path skips the thick path's guarantees

`gmail_send` also exists as an ordinary tool: `tools.py:770-788` sends any `draft_id` after a generic chat approval — **no recipient↔person binding, no reviewed-body digest, no sequence transition, generic ledger event**. Same for `apollo_enrich_contact` (`tools.py:812-869`) versus the enrich-approval route (`server.py:2498+`): the thin path drops the credit-spend receipt and Source Reference provenance. Which implementation runs is decided by which UI affordance the call happened to arrive through, not by an explicit contract. The external-effect fence (PR #124) protects both paths against unknown-outcome duplication — but the *identity* guarantees exist only on the thick path. Test evidence matches: ~20 tests on the thick path's idempotency/concurrency/drift, ~1 on the thin path asserting none of those properties. **This is the highest-value architecture fix in the codebase** (AR1 below).

---

## 6. Data Architecture

- **Five SQLite databases**, each owned by one store class with its own connection and RLock: `club.db` (ConversationStore — sessions, transcripts index, memories, inbox, scheduler, settings; `store.py:209`), `people.db` (`people.py:64`), `agent_runs.db` (`agent_run_repository.py:65`), `drive_ingestion.db`, `meeting_evidence.db`. Append-only JSONL transcripts sit beside the index.
- **Migrations are genuinely engineered:** a registry owns `PRAGMA user_version` for every store; stores own DDL; steps are backup-first with rollback; opening an app never silently upgrades a database; Doctor migrates deliberately (desktop/docs/agent-runs.md "Versioning"). Two schema invariants are asserted by dedicated tests rather than argued.
- **Provenance is first-class:** evidence envelopes, run receipts, Source References, and an append-only workspace receipt stream (ADR 0002).

**Concerns:**

- **No cross-store transaction.** One decided approval touches `agent_runs.db` (run/fence), `club.db` (inbox), and `people.db` (timeline) as separate commits. `agent_run_reconcile.py` exists precisely because two stores must be read together after a crash. This is a deliberate, documented trade — but consistency is by convention + reconcile, not mechanism, and every new cross-store write adds a reconcile case.
- **ConversationStore is six stores in one file** (1,946 LOC): sessions, transcripts, memories, inbox, scheduler, settings share one class and one lock. The inbox — the HITL safety surface — deserves its own seam.
- **Unbounded growth path:** JSONL transcripts, receipts, and telemetry grow without retention policy visible in-repo. Compaction manages *context*, not disk. Low urgency for one operator; worth a note in doctor.

---

## 7. Coupling Analysis

### 7.1 God seams (High)

| File | LOC | Role |
|---|---|---|
| `coworker/server.py` | 3,687 | all 63 routes + WS defined inside one `create_app` closure (`server.py:383`, routes 1225-2829) |
| `surfaces/gui/src/api.ts` | 2,214 | entire GUI↔sidecar client surface in one module |
| `coworker/store.py` | 1,946 | six persistence concerns in one class |
| `coworker/turn.py` | 1,743 | turn loop (already an extraction — acceptable) |

`create_app` as a single closure means every route shares one namespace and any state via closure capture; FastAPI's `APIRouter` is the idiomatic seam and `drive_ingestion_api.py` already demonstrates it in-repo (`server.py:88` includes its router). The pattern exists; it has been applied once.

### 7.2 Package-by-prefix (Medium)

60+ modules sit flat in `coworker/`; the nine `agent_run_*` and five `workspace_*` modules are subpackages in everything but directory structure. Navigability holds today because module docstrings are uniformly excellent, but the flat namespace hides the dependency direction between clusters.

### 7.3 What's well-decoupled (credit where due)

- Policy is one module with zero side effects (`permissions.py`) — the model-facing approval class and the runtime decision derive from the same frozensets (`permissions.py:91-114`).
- Providers sit behind a `StreamProvider` protocol with five implementations and a compatible-failover chain (`provider.py:263`, `provider_retry.py`) — swapping vendors is low-risk.
- The turn loop is shared by WS chat and the scheduler rather than duplicated (`turn.py:1`).
- The GUI consumes a versioned event contract, not sidecar internals (`chat/protocol.ts`).
- The workspace runtime is a self-contained authority boundary with typed tools, receipts, and its own risk classes (ADR 0002).

---

## 8. Failure Surfaces (local-first framing)

| Surface | Blast radius | Current mitigation | Severity |
|---|---|---|---|
| Dual-path send/enrich | A send can reach a recipient the director never bound; enrichment spends credits without provenance | Both paths human-approved; effect fence stops duplicates | **High** |
| Sidecar process | GUI is inert without it | Tauri watchdog; orphan exit (`run.py:15-41`); doctor | Medium |
| Five SQLite files | Corruption/partial write splits truth across stores | Backup-first migrations; reconcile pass; receipts | Medium |
| LLM providers | Turn fails | Retry + failover chain, budget stops, safe failure messages (`provider_retry.py`) | Low-Med |
| Gmail/Apollo quota+auth | Outreach/enrich blocked | Typed failure classes, connector status surfaces | Low-Med |
| Update channel | Bad update bricks app | Signed manifests, drain, rollback, packaged smoke test in CI | Low |
| Scheduler tick thread | Silent repeat-crash prints to a stdout nobody sees when packaged | bare-except + traceback print (`run.py:110-118`) | Low |

---

## 9. Scale-Fit Assessment

- **Current scope (one operator, one machine): comfortable.** SQLite, a single process, and thread-offloaded tools are appropriate; run budgets and provider failover already handle the real constraint (LLM cost/availability).
- **Data growth:** compaction handles context; disk retention is unmanaged (§6). Fine for months, worth a doctor check eventually.
- **Multi-officer future:** the person file + handoff endpoint (`server.py:2090`) is the right durable object. The five-DB split and by-convention consistency is the main thing a shared/synced future would have to renegotiate — worth keeping writes behind store seams (already true) so that stays possible.
- **No hard scaling wall.** Nothing here demands re-platforming; the hosted stack was already archived deliberately.

---

## 10. Observability & Operational Readiness

Mature for a local product: local telemetry spans/usage/cost estimates (`telemetry/`), per-turn failure classes surfaced to the operator (PR #137), doctor with repair mode, diagnostic bundle with registered-secret redaction (`diagnostic_bundle.py`, `bundle_redaction.py`), secret-scan tooling with rotation support (Makefile), behavioural evals in CI, and a packaged-sidecar smoke test. The gap is small: entry-point logging is `print`-based and the tick thread swallows exceptions (§8).

---

## 11. Strengths Summary

1. **Right shape for the product** — sidecar monolith + thin shell + event-contract GUI.
2. **Safety spine** — single-source policy, effect fence, at-most-once send chain, workspace authority model with honest accepted-risks.
3. **Self-maintenance as a feature** — doctor, registry migrations with backups, signed update channel with rollback.
4. **Documentation discipline** — per-subsystem references that name their invariant tests; dated docs with explicit precedence.
5. **Provider abstraction** with failover and budget control.

---

## 12. Architecture Risk Register

| ID | Risk | Severity |
|---|---|---|
| AR1 | `gmail_send` / `apollo_enrich_contact` dual implementations with divergent safety guarantees; selection is implicit | **High** |
| AR2 | `server.py` god seam — 63 routes in one closure (3,687 LOC); `api.ts` mirrors it (2,214 LOC) | **High** |
| AR3 | Five SQLite stores, cross-store consistency by convention + reconcile only | Medium |
| AR4 | ConversationStore bundles six concerns incl. the HITL inbox | Medium |
| AR5 | Flat package: agent_run_* / workspace_* clusters are packages-by-prefix | Medium |
| AR6 | "club" identity in load-bearing state (club.db, X-Club-Token, CLUB_* env, club-gui) — migration cost compounds | Low |
| AR7 | Tick-thread bare-except + print logging in packaged app | Low |
| AR8 | No disk retention policy for transcripts/receipts/telemetry | Low |

---

## 13. Evolution Roadmap (incremental — no restructuring)

**Safety first:** 1. **AR1** — make the thick path the only implementation: route the `gmail_send` / `apollo_enrich_contact` tool names through the same authority/verification code the approval endpoints use (or have the thin path refuse when a person binding exists and hand the model the approval-endpoint flow). Port the thick path's test matrix to whatever remains callable.

**Structure (as capacity allows):** 2. **AR2** — extract routers from `create_app` by domain (people, connectors, sessions, workspaces, schedule) using the existing `drive_ingestion_api.py` pattern; split `api.ts` per domain the same way. 3. **AR4** — lift the inbox out of ConversationStore first (it is the safety surface); the rest can wait. 4. **AR5** — fold `agent_run_*` and `workspace_*` into subpackages when files next move.

**Hygiene:** 5. **AR6** — one deliberate rename migration (db filename via the migrations registry, token header, env vars) while state is still one operator's. 6. **AR7** — route entry/tick logging through `logging` with a file handler in the state dir. 7. **AR8** — doctor check for state-dir size.

---

## 14. Bottom Line

This is a **deliberately engineered local-first system** whose safety-critical paths are designed, documented, and tested to a standard well above its size. The debt is legible and concentrated: one implicit dual implementation on the two most consequential tools, two god seams, and by-convention cross-store consistency. All are incremental fixes; none blocks the current spring.

---

*Prepared from a source-level review of the repository as committed. End-user runtime behavior (Docker presence, packaged-app filesystem, connector quota state) and GitHub repository settings are not verifiable here and should be confirmed operationally.*
