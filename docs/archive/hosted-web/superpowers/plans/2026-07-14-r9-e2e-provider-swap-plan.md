# R9 — E2E + Provider-Swap Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove, with a real (non-mocked) scripted chat flow, that the runtime-solidified
agent — question → agent-decided search → cited answer → finding write → retrievable in a
new run — works identically against both `anthropic` and `deepseek`, by running the exact
same test logic twice with only `SOURCECADO_GENERATION_PROVIDER` flipped. Document the
provider-swap env contract in `.env.example`.

**Depends on:** R0 (PR #10 merged), R1 (provider adapters), R2 (the loop + `harness.ts`
thin wrapper), R4 (context assembly + memory index + check-on-use citations), R5
(streaming rewire). Per the spec's dependency graph
(`R0 → R1 → R2 → R4 → R5 → R9`), this plan assumes R0/R1/R2/R4/R5 are already merged and
diffs against that resulting state, not current `main`. R3 and R7 are parallel siblings
off R2/R3 — this plan does not depend on either landing first, and does not touch any file
they own.

## Context (read this before starting)

- Source spec: `docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md`
  (R9 section: *"Scripted chat e2e (question → agent-decided search → cited answer →
  finding write → retrievable in new session); run the same flow twice with
  `SOURCECADO_GENERATION_PROVIDER=anthropic` and `=deepseek`; document env in
  `.env.example`."*), Acceptance Criteria #1, #4, #5.
- Binding contracts: `docs/superpowers/plans/2026-07-14-r-contracts-brief.md` §7 (file
  ownership — `.env.example` is R9's only source-tree row; this plan otherwise touches
  only `tests/`).
- **Library-level e2e, not HTTP/browser.** This repo's existing e2e test
  (`tests/run-ledger-e2e.test.ts`) exercises the shared library functions directly
  (`callModel`, ledger writes) rather than booting a server or a browser — there is no
  existing HTTP-server-lifecycle or browser test harness in this repo to reuse. This plan
  follows that precedent: it calls `answerWithMemory()` (from `src/lib/memory/answer.ts`)
  and `runAgent()` (from `src/lib/harness.ts`) directly — the exact same production
  functions both `/api/agent` and `/api/agent/stream` call. R5's typed-SSE-event wiring
  and UI rendering are proven by R5's own route/component tests; this slice proves the
  underlying agent behavior + provider swap, not the transport.
- **Provider swap is env-var-only, not a new code parameter.** `resolveProviderName`/
  `resolveModel` in `src/lib/model-gateway.ts` already read
  `process.env.SOURCECADO_GENERATION_PROVIDER`/`SOURCECADO_GENERATION_MODEL` whenever the
  caller doesn't pass an explicit `providerName`/`model` — and neither `answerWithMemory`
  nor `runAgent` pass one. So "swap the provider" here means: set
  `process.env.SOURCECADO_GENERATION_PROVIDER` before the run, call the identical
  production functions, restore the env after. This is the literal, simplest reading of
  Acceptance Criterion #4 ("changes zero loop/tool/UI code paths") — no `providerName`
  parameter is threaded through any call in this plan.
- **`resolveModel` checks `SOURCECADO_GENERATION_MODEL` before any provider-conditional
  default** (`src/lib/model-gateway.ts`, `resolveModel`): if that env var is already set
  (e.g. to `claude-sonnet-4-6` for local anthropic use), it wins regardless of
  `SOURCECADO_GENERATION_PROVIDER`. The deepseek half of this plan's flow must `delete
  process.env.SOURCECADO_GENERATION_MODEL` before running, or every deepseek call would
  silently try to use an Anthropic model name. Handled explicitly in Task 1.
- **Embeddings always use OpenAI regardless of chat provider** — `resolveProviderName`
  hard-codes `"openai"` for `kind === "embed"`/`"embed_many"`. `OPENAI_API_KEY` must stay
  set (unlike the R4 plan's unit tests, which deliberately delete it to force the offline
  hash fallback) — this e2e wants real embeddings so `search_memory`'s hybrid retrieval
  actually works.
- **`memoryRegistry()` (`src/lib/memory/answer-config.ts`) registers only
  `search_memory`.** R4's plan locks this in explicitly ("`memoryRegistry()` stays...");
  `add_memory_note` is a real `Tool` (`src/lib/tools/add-memory-note.ts`) but is not wired
  into the registry `answerWithMemory()` uses. Per this plan's file ownership (tests/ and
  `.env.example` only), it cannot fix that wiring — see Judgment call #1 below for the
  concrete resolution.
- **"New session" = a fresh `runAgent`/`answerWithMemory` call with no `history` passed.**
  R6 (persisted `chat_sessions`) is not in R9's dependency chain, so there is no
  session-resume mechanism yet to exercise. A call with an empty transcript and no prior
  turns is the correct stand-in for "starts fresh" at this point in the sprint.
- Current test file to model style on: `tests/run-ledger-e2e.test.ts` (single-file e2e,
  drops + re-runs migrations in `beforeEach`, no mocks). Live-smoke skip pattern to reuse:
  `tests/anthropic-base-url.test.ts` / R7's `it.skipIf(!process.env.TAVILY_API_KEY)`
  convention (see `docs/superpowers/plans/2026-07-14-r7-external-tools-plan.md`), applied
  here as `describe.skipIf`.
- Relevant existing signatures (verified in the current repo, expected unchanged by R2 per
  the brief's "byte-for-byte" guarantee):
  - `answerWithMemory(db, { question, history?, onStep? }): Promise<MemoryAnswer>` —
    `MemoryAnswer = { runId, status, answer?, steps, invalidCitations }`
    (`src/lib/memory/answer.ts`).
  - `runAgent(input: RunAgentInput): Promise<RunAgentResult>` — `RunAgentInput` includes
    `question, registry, allowedClasses?, maxSteps?, provider?, db?, instructions?,
    history?, onStep?` (`src/lib/harness.ts`).
  - `getRunTrace(db, runId): Promise<RunTrace | null>` — `RunTrace.steps: RunStepTrace[]`,
    each with `children: RunStepTrace[]` and `toolCalls: ToolCallRecord[]`
    (`{ toolName, status, result, ... }`) (`src/lib/ledger.ts`).
  - `createToolRegistry(tools?)`, `searchMemoryTool`, `addMemoryNoteTool` — unchanged (R3's
    file-ownership row marks `tools/registry.ts`/`types.ts` "unchanged").
  - `buildMemoryAnswerInstructions(db, actor?): Promise<string>` — R4's composer
    (`src/lib/context.ts`); used here to build the same system-prompt instructions
    `answerWithMemory` uses, for the one sub-scenario that needs a custom registry (see
    Judgment call #1).
  - `addMemoryNote(db, { title, text, actor? }): Promise<{ sourceId: string }>`
    (`src/lib/memory/notes.ts`) — used directly (not through the agent) to seed the
    "deep question" fact, so seeding doesn't depend on the model choosing the right tool.

## Judgment calls

1. **The finding-write sub-scenario calls `runAgent()` directly with a custom registry
   (`search_memory` + `add_memory_note`), not `answerWithMemory()`.** Since
   `memoryRegistry()` doesn't include `add_memory_note` (locked by R4, out of this plan's
   file ownership), the only way to exercise "finding write via the agent, then retrieved
   in a new session" through real production code is to build a registry that has both
   tools and pass `allowedClasses: new Set(["read", "write_internal"])`, using
   `buildMemoryAnswerInstructions` for the same system prompt `answerWithMemory` would
   build. Retrieval afterward still goes through the real `answerWithMemory()` (unmodified
   production registry), since a written note is just an ordinary indexed, searchable
   source once written — no special registry needed to read it back. This proves the
   underlying loop/tool/context machinery supports the full flow without touching
   `answer-config.ts`, which is out of scope here (flagged as an open question below —
   whether `add_memory_note` should be wired into the live chat registry is a decision for
   whichever slice owns that file).
2. **The "index sufficiency" sub-scenario asserts on answer correctness, not on whether
   `search_memory` was actually skipped.** A live model's choice to search or not is not
   deterministic enough to hard-fail a ralph-loop-executed test on. The test still
   constructs a question answerable purely from the injected memory index (so a
   reasonably capable model *should* skip search) and logs whether `search_memory` was
   called for manual confirmation of Acceptance Criterion #1's "observed live" language —
   but the pass/fail gate is answer correctness only. The paired "depth needed" question
   *does* hard-assert `search_memory` was called, since that question's answer is
   genuinely unavailable without it (a fact never shown in the index) — that direction is
   deterministic enough to assert on.
3. **One `it()` per provider, not one per sub-scenario.** The four sub-scenarios (seed →
   shallow → deep → write-then-retrieve) are sequential and share one seeded DB state
   within a provider's run; splitting them into separate `it()`s would require re-seeding
   or leaking state through module-level variables. A single `it()` running a shared
   `runScriptedFlow(providerName)` helper keeps the DB state and ordering explicit.
4. **Both provider blocks are gated on their own API key with `describe.skipIf`,
   matching R7's precedent** (`it.skipIf(!process.env.TAVILY_API_KEY)`). Today
   `DEEPSEEK_API_KEY` is unset in this environment, so the deepseek half will show as
   skipped until a key is supplied — flagged in openQuestions, not silently worked around
   (there is no way to "prove" a provider without a live key for it).

## Global Constraints

- Do not touch `src/lib/memory/answer-config.ts`, `src/lib/context.ts`,
  `src/lib/harness.ts`, `src/lib/tools/*`, or any other `src/` file — this plan's only
  source-tree file is `.env.example`. Everything else is new test files.
- No mocked `ModelGatewayProvider`/`fetch` anywhere in this plan's test file — the whole
  point is a real, live round trip. (Contrast with every other slice's tests, which mock
  the provider.)
- Each `it()` makes several sequential live model calls (each potentially multi-step, up
  to `maxSteps: 8`) plus live OpenAI embedding calls; give each test a generous explicit
  timeout (120,000 ms) via vitest's third `it()` argument — the global default (5,000 ms)
  will not be enough.
- DB reset: drop and re-run migrations in `beforeEach`, combining the full ledger +
  memory table set (mirrors `tests/run-ledger-e2e.test.ts` + `tests/context.test.ts`):
  `tool_calls, model_calls, run_steps, runs, source_permissions, extraction_runs,
  semantic_facts, memory_chunks, source_records, schema_migrations`.
- Run tests with `DATABASE_URL` pointing at the local Postgres:
  `postgresql://sourcecado:sourcecado@localhost:5432/sourcecado` (container
  `sourcecado-db-1`).
- Never print API key values in test output or commit messages.

---

### Task 1: Scripted e2e flow + provider-swap test file

**Files:**
- Create: `tests/e2e-chat-provider-swap.test.ts`

**What to build:** A single test file exporting no public API — just two
`describe.skipIf` blocks (one per provider), each running one `it()` that calls a shared
`runScriptedFlow(providerName)` helper defined in the same file. The helper: seeds a memory
note with a fact that is not visible from the memory index alone, asks a shallow
index-answerable question, asks a deep question that requires `search_memory`, writes a
new finding via `add_memory_note` through a custom `runAgent()` call, then retrieves that
finding through a fresh `answerWithMemory()` call with no history.

- [ ] **Step 1: Write `tests/e2e-chat-provider-swap.test.ts`**

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import type { RunStepTrace, RunTrace } from "@/lib/ledger";
import { getRunTrace } from "@/lib/ledger";
import { runAgent } from "@/lib/harness";
import { answerWithMemory } from "@/lib/memory/answer";
import { addMemoryNote } from "@/lib/memory/notes";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { buildMemoryAnswerInstructions } from "@/lib/context";
import { createToolRegistry } from "@/lib/tools/registry";
import { searchMemoryTool } from "@/lib/tools/search-memory";
import { addMemoryNoteTool } from "@/lib/tools/add-memory-note";

const E2E_TIMEOUT_MS = 120_000;

async function resetAllTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS tool_calls CASCADE`;
  await db`DROP TABLE IF EXISTS model_calls CASCADE`;
  await db`DROP TABLE IF EXISTS run_steps CASCADE`;
  await db`DROP TABLE IF EXISTS runs CASCADE`;
  await db`DROP TABLE IF EXISTS source_permissions CASCADE`;
  await db`DROP TABLE IF EXISTS extraction_runs CASCADE`;
  await db`DROP TABLE IF EXISTS semantic_facts CASCADE`;
  await db`DROP TABLE IF EXISTS memory_chunks CASCADE`;
  await db`DROP TABLE IF EXISTS source_records CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

// Recursively walks a RunTrace looking for a succeeded call to `toolName`,
// anywhere in the step tree (steps nest tool calls under themselves, and
// steps can nest further child steps).
function wasToolCalled(trace: RunTrace | null, toolName: string): boolean {
  if (!trace) return false;
  function walk(steps: RunStepTrace[]): boolean {
    for (const step of steps) {
      if (step.toolCalls.some((tc) => tc.toolName === toolName && tc.status === "succeeded")) {
        return true;
      }
      if (walk(step.children)) return true;
    }
    return false;
  }
  return walk(trace.steps);
}

// The full scripted flow: seed -> shallow (index-answerable) question ->
// deep (search-required) question -> finding write -> retrieval in a fresh
// run. Run identically for both providers; only SOURCECADO_GENERATION_PROVIDER
// differs between the two describe blocks that call this.
async function runScriptedFlow(providerName: "anthropic" | "deepseek"): Promise<void> {
  const db = getDb();

  // --- Seed: a fact that is NOT visible from the memory index (titles/dates
  // only) -- forces a real search_memory call for the "deep" question below.
  await addMemoryNote(db, {
    title: "Acme Rotors funding note",
    text: "Acme Rotors closed a $12M Series A led by Northbridge Ventures in March 2026.",
  });

  // --- Sub-scenario A: shallow, index-answerable question. A reasonably
  // capable model should be able to answer this from the injected memory
  // index alone, without calling search_memory. We do not hard-assert on
  // that choice (live model behavior is not perfectly deterministic) -- we
  // log it for manual confirmation of AC#1's "observed live" requirement,
  // and hard-assert only on the answer being non-empty.
  const shallow = await answerWithMemory(db, {
    question: "List the titles of the memory sources currently indexed. Answer from what you already know in context; do not search for anything new.",
  });
  expect(shallow.status).toBe("succeeded");
  expect(shallow.answer).toBeTruthy();
  const shallowTrace = await getRunTrace(db, shallow.runId);
  // eslint-disable-next-line no-console
  console.log(
    `[e2e:${providerName}] shallow question called search_memory=${wasToolCalled(shallowTrace, "search_memory")}`,
  );

  // --- Sub-scenario B: deep question. The answer (amount + investor) is not
  // in the index -- only in the seeded note's body -- so a correct answer is
  // only possible if the agent decided to call search_memory and cited it.
  const deep = await answerWithMemory(db, {
    question: "How much did Acme Rotors raise in their last funding round, and who led it?",
  });
  expect(deep.status).toBe("succeeded");
  expect(deep.answer).toBeTruthy();
  expect(deep.answer).toMatch(/\$?12\s?[Mm](illion)?/);
  expect(deep.answer).toMatch(/Northbridge/i);
  expect(deep.invalidCitations).toEqual([]);
  expect(deep.answer).toMatch(/#chunk-\d+|#row-\d+/);
  const deepTrace = await getRunTrace(db, deep.runId);
  expect(wasToolCalled(deepTrace, "search_memory")).toBe(true);

  // --- Sub-scenario C: finding write, then retrieval in a new run (no
  // history threaded -- stands in for "a new session" ahead of R6's
  // persisted chat sessions). memoryRegistry() only registers search_memory
  // (locked by R4), so this sub-scenario builds its own registry with both
  // tools to exercise the write path through the real runAgent()/loop code.
  const writeRegistry = createToolRegistry([searchMemoryTool, addMemoryNoteTool]);
  const writeInstructions = await buildMemoryAnswerInstructions(db, DEFAULT_ACTOR);
  const writeResult = await runAgent({
    question:
      "Use the add_memory_note tool to record a note titled 'Acme Rotors contact' with this text: The primary sourcing contact at Acme Rotors is Priya Shah, VP of Talent.",
    registry: writeRegistry,
    allowedClasses: new Set(["read", "write_internal"]),
    instructions: writeInstructions,
    db,
  });
  expect(writeResult.status).toBe("succeeded");
  const writeTrace = await getRunTrace(db, writeResult.runId);
  expect(wasToolCalled(writeTrace, "add_memory_note")).toBe(true);

  const retrieval = await answerWithMemory(db, {
    question: "Who is the primary sourcing contact at Acme Rotors?",
  });
  expect(retrieval.status).toBe("succeeded");
  expect(retrieval.answer).toMatch(/Priya Shah/i);
  expect(retrieval.invalidCitations).toEqual([]);
}

interface ProviderEnvSnapshot {
  provider: string | undefined;
  model: string | undefined;
}

function snapshotProviderEnv(): ProviderEnvSnapshot {
  return {
    provider: process.env.SOURCECADO_GENERATION_PROVIDER,
    model: process.env.SOURCECADO_GENERATION_MODEL,
  };
}

function restoreProviderEnv(snapshot: ProviderEnvSnapshot): void {
  if (snapshot.provider === undefined) delete process.env.SOURCECADO_GENERATION_PROVIDER;
  else process.env.SOURCECADO_GENERATION_PROVIDER = snapshot.provider;
  if (snapshot.model === undefined) delete process.env.SOURCECADO_GENERATION_MODEL;
  else process.env.SOURCECADO_GENERATION_MODEL = snapshot.model;
}

describe.skipIf(!process.env.ANTHROPIC_API_KEY)("E2E scripted chat flow — anthropic", () => {
  let snapshot: ProviderEnvSnapshot;

  beforeEach(async () => {
    snapshot = snapshotProviderEnv();
    process.env.SOURCECADO_GENERATION_PROVIDER = "anthropic";
    delete process.env.SOURCECADO_GENERATION_MODEL; // let the anthropic-conditional default apply
    await resetAllTables();
  });

  afterEach(async () => {
    restoreProviderEnv(snapshot);
    await closeDb();
  });

  it(
    "runs the full scripted flow (search-when-needed, cited answer, finding write + retrieval) live against Anthropic",
    async () => {
      await runScriptedFlow("anthropic");
    },
    E2E_TIMEOUT_MS,
  );
});

describe.skipIf(!process.env.DEEPSEEK_API_KEY)("E2E scripted chat flow — deepseek", () => {
  let snapshot: ProviderEnvSnapshot;

  beforeEach(async () => {
    snapshot = snapshotProviderEnv();
    process.env.SOURCECADO_GENERATION_PROVIDER = "deepseek";
    delete process.env.SOURCECADO_GENERATION_MODEL; // let the deepseek-conditional default apply
    await resetAllTables();
  });

  afterEach(async () => {
    restoreProviderEnv(snapshot);
    await closeDb();
  });

  it(
    "runs the full scripted flow (search-when-needed, cited answer, finding write + retrieval) live against DeepSeek",
    async () => {
      await runScriptedFlow("deepseek");
    },
    E2E_TIMEOUT_MS,
  );
});
```

**Acceptance criteria:**
- With `ANTHROPIC_API_KEY` set (as it is in this repo's `.env.local`) and
  `DATABASE_URL` pointing at the local Postgres, the anthropic `describe` block runs (not
  skipped) and its `it` passes: both sub-scenario B and C's hard assertions succeed
  (correct funding fact + investor name, valid citation format, zero invalid citations,
  `search_memory` was called for the deep question, `add_memory_note` was called and the
  written fact is retrievable by a fresh `answerWithMemory` call).
- The vitest summary explicitly reports the anthropic suite as **passed**, not
  **skipped** — since Vitest has no dotenv/env-loading configured, running the Verify
  command without first sourcing `.env.local` would leave `ANTHROPIC_API_KEY` undefined,
  make `describe.skipIf` skip the anthropic block too, and still exit 0; that outcome does
  not satisfy this criterion and must be treated as a failed verification.
- With `DEEPSEEK_API_KEY` unset (today's state), the deepseek `describe` block is reported
  as **skipped**, not failed.
- If a `DEEPSEEK_API_KEY` is later added to the environment, the deepseek block runs the
  identical `runScriptedFlow` and is expected to pass the same assertions (this plan
  cannot verify that today without the key — see Judgment call #4 / openQuestions).
- Console output includes one `[e2e:anthropic] shallow question called
  search_memory=...` line (and, once a DeepSeek key exists, one `[e2e:deepseek] ...` line)
  for manual confirmation of Acceptance Criterion #1's "both observed live" language.

**Verify:**
```bash
set -a; source .env.local; set +a
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/e2e-chat-provider-swap.test.ts
```
Vitest has no `setupFiles`/`envDir`/dotenv loading configured (checked `vitest.config.ts`
and `package.json`; no `.envrc` exists either), so `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` are
`undefined` inside the test process unless the shell invoking `npx vitest run` already has
them exported — hence the `source .env.local` line above is required, not optional.

Expected: the anthropic block's test PASSes (may take up to ~2 minutes — several live
model round trips); the deepseek block reports as skipped. Read the console output for the
`shallow question called search_memory=...` line and confirm it reads `false` (or, if
`true`, that the model still produced a correct, non-search-dependent answer — re-read the
printed answer to judge whether AC#1's "skip when index suffices" intent was actually
honored; this is the one place in this plan that asks for a human judgment call on live
output, per Judgment call #2). **Also confirm the vitest summary line itself reports the
anthropic describe block as passed** (e.g. `Tests  1 passed | 1 skipped`) **rather than
both blocks showing as skipped** — a 0-failure run with everything skipped is not a pass
for this slice's acceptance criterion that the anthropic block "runs (not skipped)."

- [ ] **Step 2: Commit**

```bash
git add tests/e2e-chat-provider-swap.test.ts
git commit -m "test(r9): scripted live chat e2e, run identically against anthropic and deepseek"
```

---

### Task 2: `.env.example` provider-swap docs + full verification

**Files:**
- Modify: `.env.example`

**What to build:** Document, next to the existing `SOURCECADO_GENERATION_PROVIDER` block,
that this e2e test exists and how its per-provider skip logic works, so a future
maintainer supplying a `DEEPSEEK_API_KEY` knows what will start running.

- [ ] **Step 1: Append to `.env.example`**, directly after the existing
  `DEEPSEEK_API_KEY=` line (do not touch any other line in the file):

```
# R9 E2E provider-swap proof: tests/e2e-chat-provider-swap.test.ts runs the same
# scripted chat flow twice -- once with SOURCECADO_GENERATION_PROVIDER=anthropic
# (needs ANTHROPIC_API_KEY) and once with =deepseek (needs DEEPSEEK_API_KEY). Each
# half is skipped automatically (not failed) when its own key is absent.
```

- [ ] **Step 2: Confirm the addition doesn't duplicate an existing line**

Run: `grep -c "R9 E2E provider-swap proof" .env.example`
Expected: `1`.

- [ ] **Step 3: Run the full test suite**

```bash
set -a; source .env.local; set +a
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run
```
Expected: PASS — every existing suite plus `tests/e2e-chat-provider-swap.test.ts` (anthropic
running live, deepseek skipped), zero regressions. Confirm in the summary that the
anthropic `describe` block is reported as **passed**, not **skipped** — same reasoning as
Task 1's Verify block: without sourcing `.env.local` first, `ANTHROPIC_API_KEY` would be
undefined and both provider blocks would silently skip while vitest still exits 0.

- [ ] **Step 4: Lint**

```bash
npm run lint
```
Expected: `✔ No ESLint warnings or errors`.

- [ ] **Step 5: Build**

```bash
npm run build
```
Expected: build succeeds (no new routes/components touched by this slice).

- [ ] **Step 6: Cleanup-only pass over this slice's own additions**

Re-read `tests/e2e-chat-provider-swap.test.ts`. Confirm: no orphaned imports, no
speculative parameters, `E2E_TIMEOUT_MS` used consistently, no leftover debug code beyond
the one intentional `console.log` line described above. Fix anything found; do not touch
unrelated files.

- [ ] **Step 7: Commit**

```bash
git add .env.example
git commit -m "docs(r9): document the anthropic/deepseek e2e provider-swap proof in .env.example"
```

(If Step 6 produced changes to the test file, fold that into this commit or a separate
`chore:` commit — either is fine.)

---

## Tests

| File | New tests | What it covers |
|---|---|---|
| `tests/e2e-chat-provider-swap.test.ts` (new) | 2 `it`s (one per provider `describe.skipIf` block) | Full scripted chat flow live: index-answerable question (logged, not hard-asserted, per Judgment call #2); search-required question with a correct, cited answer and zero invalid citations (hard-asserted `search_memory` was called); a finding written via `add_memory_note` through a custom registry, retrieved via a fresh `answerWithMemory` call with no history. Each `it` is skipped, not failed, when its provider's API key is absent. This is the sprint's Testing Plan "E2E" row (+1), read here as "+1 scripted flow, parameterized over both providers." |

No other test files change. This is a live, non-mocked test — it makes real API calls (the
only such test file that does at the agent-flow level in this repo) and therefore is not
run in isolation from a hermetic environment; it depends on `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY` (embeddings), and `DATABASE_URL` all being set.

## Self-Review

**Spec/brief coverage:**
- "Scripted chat e2e (question → agent-decided search → cited answer → finding write →
  retrievable in new session)" → Task 1, sub-scenarios A–C.
- "Run the same flow twice with `SOURCECADO_GENERATION_PROVIDER=anthropic` and
  `=deepseek`" → Task 1, the two `describe.skipIf` blocks sharing `runScriptedFlow`.
- "Document env in `.env.example`" → Task 2.
- Acceptance Criterion #1 (search-when-needed vs. skip-when-index-suffices, both observed
  live) → sub-scenarios A/B + the console log + the Task 1 verify step's explicit
  human-judgment instruction.
- Acceptance Criterion #4 (provider swap changes zero loop/tool/UI code) → the shared
  `runScriptedFlow` helper called identically by both describe blocks; only the env var
  differs.
- Acceptance Criterion #5 (a finding written mid-flow is retrieved in a new run) →
  sub-scenario C.
- Acceptance Criteria #2, #3, #6, #8, #9, #10 are proven by other slices' own tests (adapter
  unit tests, orchestrator tests, tool tests, the full suite) — not re-verified here, per
  the spec's per-slice Testing Plan table assigning "E2E" to R9 alone.

**Scope discipline:** No source file outside `.env.example` is touched. No new
`add_memory_note` wiring into `memoryRegistry()` (flagged as an open question, not
silently fixed here — that decision belongs to whichever slice owns
`src/lib/memory/answer-config.ts`). No touch to R3/R7-owned tool files (only imported,
read-only).

**Placeholder scan:** No TBD/TODO; every step has complete code.

## Eng Review (2026-07-14)

**Verdict: approve (revised)** — the one must-fix (the live-key verify gap below) is now
resolved in the plan text; everything else in the plan is sound and well-grounded. No
architectural rework needed.

Reviewed against the actual repo state on `feat/a-chat-streaming` (not a hypothetical future
state) and against `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`. Every signature
this plan cites was checked against either the live file or the owning slice's own plan doc.

### Must-fix

1. **RESOLVED.** **[P1] (confidence: 8/10) — The Verify steps (Task 1 and Task 2 Step 3) don't load the
   API keys the test needs, and this repo has no mechanism that does it for you.**
   `vitest.config.ts` has no `setupFiles`/`envDir`/`loadEnv`, and `grep -rn dotenv src/`
   returns nothing — Vitest does not auto-read `.env.local` the way `next dev` does. The
   Verify block only runs `export DATABASE_URL=...` before `npx vitest run`; it never
   exports `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`. If the operator runs the literal Verify
   command in a shell that doesn't already have those exported (no `.envrc`/direnv exists
   in this repo either — checked), `process.env.ANTHROPIC_API_KEY` is `undefined` inside
   the test process, `describe.skipIf(!process.env.ANTHROPIC_API_KEY)` is `true`, and
   **both** provider blocks report "skipped" — vitest still exits 0. That's a silent
   pass-with-nothing-asserted outcome for the one slice whose entire job is "prove this
   happened live," and Task 1's own acceptance criteria ("the anthropic block runs (not
   skipped)") is not actually guaranteed by anything in the plan.
   - This isn't a new problem this plan invented — `tests/run-ledger-e2e.test.ts:123` already
     makes a live `callModel` embedding call with the same missing-env-loading gap — but R9
     is the slice that depends on it hardest (3 keys, and the explicit deliverable is "prove
     it happened live"), so it's the right place to close it.
   - Fix: add `set -a; source .env.local; set +a;` (or equivalent) to both Verify blocks
     before `npx vitest run`, and add one line to Task 1's acceptance criteria: "confirm the
     vitest summary reports the anthropic suite as **passed**, not **skipped** — a 0-failure
     run with everything skipped is not a pass for this slice."
   - **Applied:** both Verify blocks (Task 1 and Task 2 Step 3) now `source .env.local`
     before `npx vitest run`; Task 1's acceptance criteria gained an explicit "vitest summary
     reports the anthropic suite as passed, not skipped" check, and both Verify blocks'
     "Expected" text now calls out the same distinction.

### Notes (no plan change required, but worth knowing)

2. **[P2] (confidence: 9/10, verified against the real repo) — This plan is not executable
   today, and that's correctly disclosed, not hidden.** None of `src/lib/context.ts`,
   `src/lib/agent-loop.ts`, `src/lib/tools/orchestrator.ts`, or `src/lib/llm/types.ts` exist
   yet on `feat/a-chat-streaming` or `main` (checked directly) — R1/R2/R4/R5 haven't landed.
   `src/lib/harness.ts` still has today's `agentDecisionSchema`/`buildAgentSystemPrompt`
   shape. The plan states this dependency explicitly up front, so this isn't a gap in the
   plan — it's a reminder that Judgment call #1's `buildMemoryAnswerInstructions(db, actor?)`
   import must be re-verified against R4's *actual* landed code (not just R4's plan doc) once
   R4 merges, in case implementation drifts from what R4's plan currently promises (verified
   today: R4's plan file does define that exact signature at
   `r4-context-memory-plan.md:340`, so the cross-plan contract is consistent as written).

3. **[P3] (confidence: 8/10, verified by reading the file in full) — Wrong citation, no code
   impact.** The Context section says the "Live-smoke skip pattern to reuse" is
   `tests/anthropic-base-url.test.ts` alongside R7's `it.skipIf(...)` convention. Read
   `tests/anthropic-base-url.test.ts` in full: it has zero `skipIf`/env-gating — it's an
   unconditional unit test of `resolveAnthropicBaseUrl`. The real precedent is only
   R7's `it.skipIf(!process.env.TAVILY_API_KEY)` (confirmed at
   `r7-external-tools-plan.md:192`). The actual Task 1 code correctly implements
   `describe.skipIf`, so nothing downstream breaks — just drop the wrong half of the
   citation so a future reader doesn't go looking for a skip pattern that isn't there.

4. **[P3] (confidence: 5/10) — Stale signature citation, no functional impact.** The
   "Relevant existing signatures" list documents `RunAgentInput` as including `provider?`.
   R2's own plan (the slice that rewrites `harness.ts`) renames that field to
   `providerName?: string` plus a new `adapter?: LlmAdapter` seam
   (`r2-agent-loop-plan.md:1512`). This plan's Task 1 code never references `provider`/
   `providerName` at all (by design — the swap is env-var-only), so nothing here actually
   breaks; the bullet will just read as stale the moment R2 lands. Fine to leave as-is or
   fix opportunistically when R2 merges.

5. **[P3] (confidence: 6/10) — No CI exists in this repo** (checked `.github/workflows` —
   absent), so this e2e will only ever run when a human manually exports keys and invokes
   it. Combined with must-fix #1, that means regressions in the scripted flow (from R2's
   `harness.ts` rewrite, R4's `context.ts`, or R5's streaming rewire, all of which touch code
   this test exercises) won't be caught automatically. Worth a TODO — not a blocker for this
   slice — to re-run `tests/e2e-chat-provider-swap.test.ts` manually after each of those
   slices lands, since none of their own plans schedule that re-run.

### What already exists (correctly reused, not rebuilt)

- `tests/run-ledger-e2e.test.ts`'s DB-reset-in-`beforeEach` + no-mocks pattern — reused
  as-is; the table drop list (verified against `src/migrations/*.sql`) is complete for the
  current schema (`runs, run_steps, model_calls, tool_calls, source_records, memory_chunks,
  semantic_facts, extraction_runs, source_permissions, schema_migrations` — all 10 tables
  that exist today, correctly matched).
- `resolveProviderName`/`resolveModel`'s env-var resolution (`src/lib/model-gateway.ts:386-406`,
  read directly) — reused via the env var, not reimplemented or threaded as a parameter.
- Permission-class wiring for the write sub-scenario: `addMemoryNoteTool.permissionClass ===
  "write_internal"` and `searchMemoryTool.permissionClass === "read"` (both verified in
  `src/lib/tools/{add-memory-note,search-memory}.ts`) — the plan's
  `allowedClasses: new Set(["read", "write_internal"])` is exactly sufficient for both tools;
  no permission-gate surprise waiting at implementation time.

### NOT in scope (plan's own disclosures, verified against the contracts brief)

- Wiring `add_memory_note` into the live `memoryRegistry()` — explicitly deferred by this
  plan and matches the contracts brief's own "Deferred to v2" ticket
  (`r-contracts-brief.md:399-409`), which names the same gap independently. Consistent, not
  silently dropped.
- DeepSeek-path verification — cannot be proven today without a live `DEEPSEEK_API_KEY`;
  correctly disclosed as an open, unresolvable-today item rather than worked around.

### Failure modes

- Live model doesn't call `search_memory` for the deep question → hard `expect(...).toBe(true)`
  fails loudly. Covered, not silent.
- Live model produces a citation the ledger can't verify → `expect(deep.invalidCitations).toEqual([])`
  fails loudly. Covered.
- API key silently absent → not silent in vitest's own reporter (shows as "skipped" in the
  summary), but nothing in the plan forces an operator to notice that distinction — this is
  must-fix #1 above, downgraded from "critical" only because vitest's default reporter does
  surface skip vs. pass, so a careful read of the Verify output would catch it today.

### Test coverage

No new production code path exists (this slice is test-file + docs only), so branch-coverage
diagramming doesn't apply in the usual sense. The four sub-scenarios each map 1:1 to an
Acceptance Criterion and share one `runScriptedFlow` helper across both provider blocks
(good DRY — avoids duplicating live-call assertions per provider). No coverage gaps found
in the scripted flow itself; the only gap is the operational one above (must-fix #1).

### Sequencing / rollback

This plan touches no source file except `.env.example` (append-only, one new comment block) —
rollback is a single revert with no blast radius. Sequencing risk is fully disclosed by the
plan itself (depends on R1/R2/R4/R5 landing first) and is not a defect in this plan's own
scope.
