# F3+F4 Model Gateway And Run Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build the hosted Sourcecado Model Gateway and Run Ledger as one vertical slice.

**Architecture:** Sourcecado owns durable observability in Postgres. A top-level `run` contains hierarchical `run_steps` that mirror tracing spans; `model_calls` and `tool_calls` attach detail rows to the relevant step. All product model use flows through `callModel()`, which wraps the Vercel AI SDK, captures payloads by default, and records prompt identity, usage, status, and errors.

**Tech Stack:** Next.js 15, TypeScript, Postgres, pgvector-enabled local DB, Vitest, Vercel AI SDK, DeepSeek provider, OpenAI provider, Zod.

---

## Decisions Locked

- Implement F3 and F4 together.
- Use Vercel AI SDK with `ai`, `@ai-sdk/deepseek`, `@ai-sdk/openai`, and `zod`; inspect installed package types after install because AI SDK APIs move across majors.
- Generation default: `DEEPSEEK_API_KEY` + `SOURCECADO_GENERATION_MODEL=deepseek-chat`.
- Embedding default: `OPENAI_API_KEY` + `SOURCECADO_EMBEDDING_MODEL=text-embedding-3-small`.
- Embedding dimension: `1536`.
- `run_steps.step_kind` uses tracing vocabulary: `agent`, `model`, `embedding`, `tool`, `retrieval`, `rerank`, `artifact`, `evaluation`, `system`.
- Capture raw model/tool inputs and outputs by default; support `capturePayloads: false` / `redactionMode: "suppress"`.
- Status model:
  - `runs`, `model_calls`, `tool_calls`: `running`, `succeeded`, `failed`, `cancelled`
  - `run_steps`: same plus `skipped`
- Callers pass `taskName` and `promptVersion`; gateway stores `prompt_hash`.
- No external tracing service, no retry scheduler, no F5 agent harness in this slice.

## Data Flow

```text
caller
  |
  | optional trace context: runId + parentStepId
  v
callModel()
  |
  +--> run_steps row (kind=model or embedding)
  |
  +--> model_calls row (running, request payload, prompt hash)
  |
  +--> Vercel AI SDK provider call
  |
  +--> model_calls update (usage, response payload, status/error)
  |
  +--> run_steps update (output, status/error)
  v
typed result returned to caller
```

## Task 1: Dependencies And Environment Contract

**Files:**
- Modify: `package.json`
- Modify: `package-lock.json`
- Modify: `.env.example`

- [x] Install dependencies.

Run:

```bash
npm install ai @ai-sdk/deepseek @ai-sdk/openai zod
```

- [x] Inspect installed AI SDK type/API surface before writing gateway code.

Run:

```bash
rg -n "declare function generateText|declare function generateObject|declare function embed|declare function embedMany|class AISDKError|interface LanguageModelUsage" node_modules/ai node_modules/@ai-sdk -g '*.d.ts'
```

Expected: exact installed signatures are visible locally; implementation follows those signatures instead of relying on memory.

- [x] Update `.env.example` with:

```dotenv
# Model Gateway generation provider
DEEPSEEK_API_KEY=
SOURCECADO_GENERATION_MODEL=deepseek-chat

# Model Gateway embedding provider
OPENAI_API_KEY=
SOURCECADO_EMBEDDING_MODEL=text-embedding-3-small
SOURCECADO_EMBEDDING_DIMENSIONS=1536
```

- [x] Verify dependency resolution.

Run:

```bash
npm run test -- tests/db-client.test.ts
```

Expected: existing DB client tests still pass.

## Task 2: Run Ledger And Model Gateway Migration

**Files:**
- Create: `src/migrations/001_run_ledger_model_gateway.sql`
- Modify: `tests/migrate.test.ts`

- [x] Add a failing migration test that runs migrations on a clean schema and asserts these tables exist: `runs`, `run_steps`, `model_calls`, `tool_calls`.
- [x] Add a test cleanup helper that drops `tool_calls`, `model_calls`, `run_steps`, `runs`, and `schema_migrations` in dependency order so new Postgres tests are repeatable on a developer machine.
- [x] Add constraint tests that invalid `run_steps.step_kind`, invalid `run_steps.status`, and invalid `runs.status` are rejected.
- [x] Add FK tests that a `model_calls.run_step_id` and `tool_calls.run_step_id` must point to an existing `run_steps.id`.
- [x] Create migration `001_run_ledger_model_gateway.sql` with:
  - `runs`: id, run_type, title, status, input_json, output_json, metadata_json, error_type, error_message, error_json, started_at, completed_at, created_at, updated_at.
  - `run_steps`: id, run_id, parent_step_id, step_kind, name, status, input_json, output_json, metadata_json, error_type, error_message, error_json, started_at, completed_at, created_at, updated_at.
  - `model_calls`: id, run_id, run_step_id, task_name, prompt_version, prompt_hash, provider, model, call_kind, status, request_json, response_json, usage_json, input_tokens, output_tokens, total_tokens, embedding_dimensions, error_type, error_message, error_json, started_at, completed_at, created_at, updated_at.
  - `tool_calls`: id, run_id, run_step_id, tool_name, status, arguments_json, result_json, metadata_json, error_type, error_message, error_json, started_at, completed_at, created_at, updated_at.
  - Indexes on run/step relationships, status, and started timestamps.
- [x] Run:

```bash
npm run test -- tests/migrate.test.ts
```

Expected: migration tests pass and the migration remains idempotent through the existing migration runner.

## Task 3: Ledger Write And Read Helpers

**Files:**
- Create: `src/lib/ledger.ts`
- Create: `tests/ledger.test.ts`

- [x] Write failing tests for:
  - `startRun()` creates a `running` run.
  - `finishRun()` stores `succeeded`, `output_json`, and `completed_at`.
  - `failRun()` stores `failed`, `error_type`, `error_message`, and `error_json`.
  - `startRunStep()` creates nested steps and rejects a `parentStepId` from another run.
  - `skipRunStep()` marks a step `skipped`.
  - `startToolCall()`, `finishToolCall()`, and `failToolCall()` preserve the same running-to-terminal lifecycle as model calls.
  - `getRunTrace()` returns a run with nested steps and attached model/tool calls.
- [x] Implement `src/lib/ledger.ts` with typed helpers:
  - `startRun(db, input)`
  - `finishRun(db, input)`
  - `failRun(db, input)`
  - `startRunStep(db, input)`
  - `finishRunStep(db, input)`
  - `failRunStep(db, input)`
  - `skipRunStep(db, input)`
  - `startToolCall(db, input)`
  - `finishToolCall(db, input)`
  - `failToolCall(db, input)`
  - `getRunTrace(db, runId)`
- [x] Keep raw payloads as `unknown` in TypeScript and persist them through JSONB.
- [x] Run:

```bash
npm run test -- tests/ledger.test.ts
```

Expected: all ledger helper tests pass.

## Task 4: Model Gateway With Fake Provider Tests

**Files:**
- Create: `src/lib/model-gateway.ts`
- Create: `tests/model-gateway.test.ts`

- [x] Write failing tests using injected fake provider functions, not real network calls:
  - generation call without trace records `model_calls` but no `run_steps`.
  - generation call with trace records a `run_step` and linked `model_calls`.
  - structured object call validates a Zod schema and records response payload.
  - embed call records `embedding_dimensions=1536`.
  - embedMany call records usage and response payload.
  - provider failure records failed `model_calls`, failed `run_steps` when trace exists, and throws `ModelGatewayError`.
  - `capturePayloads: false` stores null request/response payloads.
- [x] Implement `ModelGatewayError` with `code`, `message`, and `cause`.
- [x] Implement `callModel(db, input)` supporting:
  - `kind: "generate_text"`
  - `kind: "generate_object"`
  - `kind: "embed"`
  - `kind: "embed_many"`
- [x] Normalize usage into `input_tokens`, `output_tokens`, `total_tokens`, and `usage_json`.
- [x] Compute `prompt_hash` with SHA-256 over the actual prompt/messages/input text.
- [x] Build default providers from env only inside `src/lib/model-gateway.ts`.
- [x] Do not add a separate provider registry in this slice; use a small internal model resolver that returns the configured DeepSeek language model or OpenAI embedding model.
- [x] Run:

```bash
npm run test -- tests/model-gateway.test.ts
```

Expected: all model gateway tests pass without real provider credentials.

## Task 5: Route Existing LLM Extraction Through Gateway

**Files:**
- Modify: `src/extractors/llm.ts`
- Modify: `tests/extractors.test.ts`
- Modify: `tests/refresh.test.ts`

- [x] Add a test proving `createLlmExtractor()` can use an injected gateway/provider path and does not call OpenAI Responses API directly.
- [x] Preserve existing candidate validation behavior: malformed JSON, invalid candidate shapes, unsupported entity/relationship types, and provider failure still raise `ExtractionError`.
- [x] Replace direct `fetch("https://api.openai.com/v1/responses", ...)` with `callModel({ kind: "generate_object", taskName: "extract_memory_candidates", promptVersion: LLM_SCHEMA_VERSION })`.
- [x] Keep a small test seam so existing extractor tests do not require real DeepSeek/OpenAI keys.
- [x] Run:

```bash
npm run test -- tests/extractors.test.ts tests/refresh.test.ts
```

Expected: extraction and refresh tests pass.

## Task 6: Static Boundary Test

**Files:**
- Create: `tests/model-boundary.test.ts`

- [x] Add a test that scans `src/**/*.ts` and fails if provider imports or direct model HTTP URLs appear outside `src/lib/model-gateway.ts`.
- [x] Allow `src/lib/model-gateway.ts` to import `ai`, `@ai-sdk/deepseek`, and `@ai-sdk/openai`.
- [x] Ensure the test catches the old OpenAI Responses API URL if it is reintroduced.
- [x] Run:

```bash
npm run test -- tests/model-boundary.test.ts
```

Expected: boundary test passes.

## Task 7: Minimal Run Inspector

**Files:**
- Create: `src/app/runs/[id]/page.tsx`
- Test through: `npm run build`

- [x] Add a server component page that reads `getRunTrace(getDb(), id)` and renders:
  - run title/type/status
  - nested step tree
  - model calls with task/model/status/token usage
  - tool calls with tool/status
  - error messages when present
  - raw JSON payload blocks when present
- [x] Render large raw JSON blocks inside collapsed `<details>` sections and truncate visible previews to keep the inspector usable with large prompts/responses.
- [x] For missing run, call Next.js `notFound()`.
- [x] Keep styling utilitarian and consistent with the existing app shell.
- [x] Run:

```bash
npm run build
```

Expected: Next build succeeds.

## Task 8: ADR And Review Report

**Files:**
- Modify: `docs/adr/0002-run-ledger-as-observability-spine.md`
- Modify: this plan file

- [x] Add a short ADR note that the Run Ledger follows a trace/span shape and captures payloads by default with suppression controls.
- [x] Append the `## GSTACK REVIEW REPORT` section after `/plan-eng-review`.
- [x] Ensure the review report is the final `## ` heading in this file.

## Final Verification

- [x] Run targeted tests:

```bash
npm run test -- tests/migrate.test.ts tests/ledger.test.ts tests/model-gateway.test.ts tests/model-boundary.test.ts tests/extractors.test.ts tests/refresh.test.ts
```

- [x] Attempt the full test suite:

```bash
npm run test
```

Expected: pass if the local `better-sqlite3` native binding issue is fixed. If it fails only for the known SQLite binding TODO in `TODOS.md`, record that as an unrelated existing blocker and rely on the targeted hosted/Postgres tests plus build for this slice.

- [x] Run production build:

```bash
npm run build
```

## NOT In Scope

- No F5 ReAct agent harness.
- No external LangSmith/Langfuse/OpenTelemetry exporter.
- No retry scheduler.
- No pricing table or cost estimation beyond token usage capture.
- No pgvector memory schema migration in this slice.
- No real provider integration test requiring live API keys.

## What Already Exists

- `src/lib/db.ts` provides the Postgres singleton used by this slice.
- `src/lib/migrate.ts` applies SQL migrations from `src/migrations`.
- `src/extractors/llm.ts` already has the extraction prompt, schema version, prompt hashing precedent, and candidate validation.
- `src/embeddings.ts` remains the old local memory embedding implementation until the later memory-port slice uses the new gateway embedding path.

## Failure Modes To Cover

- Provider credentials missing: gateway returns a typed configuration error and records failure when trace context exists.
- Provider timeout or thrown error: `model_calls` and `run_steps` end as `failed`.
- Invalid structured object output: gateway records failure and throws `ModelGatewayError`.
- Parent step from a different run: ledger rejects it before writing a child step.
- Suppressed payload capture: rows still record task/status/usage, but raw payload JSON is null.
- Huge prompt/response payload: inspector renders a collapsed/truncated preview instead of locking the page.

## Plan-Eng Review Accepted Changes

- [P1] Database tests must reset new ledger tables in dependency order so local repeat runs do not fail on stale rows.
- [P1] Gateway implementation must inspect installed AI SDK `.d.ts` files after install to avoid coding against stale v4/v5/v6 assumptions.
- [P2] Tool calls need start/finish/fail lifecycle helpers, not a final-only `recordToolCall()`, so the ledger can represent in-flight and failed tool work consistently.
- [P2] Full-suite verification must acknowledge the existing `better-sqlite3` native binding TODO; the branch still must pass the new targeted hosted/Postgres tests and production build.
- [P3] Run inspector should collapse/truncate raw JSON payload display to avoid browser pain on large prompts/responses.

## Test Coverage Diagram

```text
CODE PATHS                                                    TEST COVERAGE
[+] Migration                                                 [planned: tests/migrate.test.ts]
  |-- clean schema creates four tables                         [***]
  |-- status and kind constraints reject bad values             [***]
  `-- FK constraints reject orphan model/tool rows              [***]

[+] Ledger helpers                                             [planned: tests/ledger.test.ts]
  |-- run lifecycle: start -> finish/fail                       [***]
  |-- nested step lifecycle and cross-run parent rejection       [***]
  |-- tool lifecycle: start -> finish/fail                      [***]
  `-- getRunTrace nested tree + attached call rows              [***]

[+] Model Gateway                                              [planned: tests/model-gateway.test.ts]
  |-- generate text/object with fake provider                   [***]
  |-- embed/embedMany with fake provider                        [***]
  |-- trace context creates run_step + model_call               [***]
  |-- provider failure records failed rows + typed error         [***]
  `-- capturePayloads false suppresses raw JSON                 [***]

[+] Existing LLM extractor                                     [planned: tests/extractors.test.ts]
  |-- routes through gateway seam                               [***]
  |-- keeps candidate validation behavior                       [***]
  `-- no direct OpenAI Responses API fetch                      [***]

[+] Minimal inspector page                                     [planned: npm run build]
  |-- server component compiles                                 [**]
  `-- missing run uses notFound()                               [**]
```

Legend: `***` behavior plus edge/error coverage, `**` compile or happy-path coverage.

## Worktree Parallelization Strategy

Sequential implementation is recommended. The migration, ledger helpers, gateway, extractor route-through, and inspector all share the new schema and types, so parallel worktrees would create avoidable merge and contract drift.

## Implementation Tasks

- [x] **T1 (P1, human: ~45m / CC: ~8m)** — migrations/tests — Add repeatable schema cleanup and constraint/FK coverage.
  - Surfaced by: Plan-eng review — migration tests must be repeatable on a real local Postgres database.
  - Files: `tests/migrate.test.ts`, `src/migrations/001_run_ledger_model_gateway.sql`
  - Verify: `npm run test -- tests/migrate.test.ts`
- [x] **T2 (P1, human: ~30m / CC: ~5m)** — model gateway — Inspect installed AI SDK types before implementing provider calls.
  - Surfaced by: Plan-eng review — AI SDK usage fields and provider APIs drift across majors.
  - Files: `src/lib/model-gateway.ts`, `tests/model-gateway.test.ts`
  - Verify: `rg` installed `.d.ts` files and `npm run test -- tests/model-gateway.test.ts`
- [x] **T3 (P2, human: ~45m / CC: ~10m)** — ledger — Add start/finish/fail tool-call lifecycle helpers.
  - Surfaced by: Plan-eng review — tool calls should match the same in-flight lifecycle as model calls.
  - Files: `src/lib/ledger.ts`, `tests/ledger.test.ts`
  - Verify: `npm run test -- tests/ledger.test.ts`
- [x] **T4 (P2, human: ~15m / CC: ~4m)** — verification — Treat the known SQLite binding failure as unrelated if full suite still fails.
  - Surfaced by: Plan-eng review — `TODOS.md` documents an existing `better-sqlite3` native binding blocker.
  - Files: plan verification notes only
  - Verify: targeted hosted/Postgres tests and `npm run build`

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | not run | optional for this backend/runtime slice |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not run | not required before implementation |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clear | 5 issues found, 0 critical gaps, all accepted into plan |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | not run | not required; inspector UI is minimal operational surface |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | not run | not required |

- **VERDICT:** ENG CLEARED — ready to implement.
- **EXECUTION VERIFICATION:** targeted F3/F4 suite passed (6 files, 37 tests); full suite passed after the Next patch (19 files, 117 passing, 1 todo); production build passed on `next@15.5.19`; production audit has no critical/high findings and retains a moderate transitive Next/PostCSS advisory with no sensible non-major npm fix.
NO UNRESOLVED DECISIONS
