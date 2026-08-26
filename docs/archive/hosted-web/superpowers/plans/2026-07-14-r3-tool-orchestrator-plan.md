# R3 — Tool Orchestrator Choke Point Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one choke-point function, `executeTool()`, that every tool call
in the new provider-agnostic loop passes through — validate → permission gate
→ execute → ledger log → truncate — plus `toLlmToolDefinition()` for turning a
`Tool` into the API `tools:` param shape. Denials and failures return an
`is_error` tool result; nothing throws.

**Depends on:** R1 (Provider adapter layer) — this slice imports
`LlmToolDefinition` from `src/lib/llm/types.ts`, which R1 creates. This plan
assumes R1 is merged. Tasks 1-3 (building `executeTool()`/`toLlmToolDefinition()`
in `orchestrator.ts`) do not depend on R2. **Task 4 does**: it edits
`src/lib/agent-loop.ts` (R2's file) to import from `orchestrator.ts` in place
of the private duplicate R2 built there as a placeholder (R2's plan, Judgment
call #1). So this plan as a whole depends on both **R1 and R2**, not R1
alone — R2 must be merged before Task 4 runs.

## Context

Today `src/lib/harness.ts:259-332` (`executeToolCall`) does validate →
permission gate → JSON.parse(args string) → execute → ledger log inline,
returning a human-readable string, no truncation, no `is_error` shape. Per the
contracts brief (§4), that logic is being pulled out into
`src/lib/tools/orchestrator.ts` as `executeTool()`, changed to:

- Take already-parsed `input: unknown` (native tool-call args, never a JSON
  string — R1's adapters and R2's loop guarantee this upstream).
- Return `ToolExecutionResult { content: string; isError: boolean }` instead
  of a bare string, so R2's loop can set `LlmToolResultBlock.isError` directly.
- Truncate any result/error content over 16,000 chars with a visible
  `[truncated N chars]` notice (both success and error paths).
- Never throw — `unknown_tool` / `permission_denied` / `invalid_args` /
  `tool_error` all return `{ content: "Error (<code>): <message>", isError:
  true }`, matching today's `failTool` message format.

`src/lib/tools/types.ts` and `src/lib/tools/registry.ts` are **read-only
reference** for this slice — `Tool`, `PermissionClass`, `ToolContext`,
`ToolRegistry` all survive untouched. `src/lib/harness.ts` is **not** touched
by this slice (R2 rewrites it as a thin wrapper); this plan does not delete
`executeToolCall` from `harness.ts` — that deletion is R2's job per the
contracts brief §3, since `harness.ts`'s current tests
(`tests/harness.test.ts`, `tests/harness-multiturn.test.ts`,
`tests/harness-onstep.test.ts`) still exercise the old inline path until R2
lands.

Ledger functions used (`src/lib/ledger.ts`, unchanged, already exist):
`startRunStep`, `finishRunStep`, `failRunStep`, `startToolCall`,
`finishToolCall`, `failToolCall` — same signatures `executeToolCall` already
uses today.

## Judgment calls

- **Truncation point:** the brief doesn't say whether truncation happens on
  the raw execute-result JSON string or on the final formatted content
  string. Leanest call: truncate the *final* `content` string (after
  `JSON.stringify` for success, after message formatting for errors) — one
  code path, matches "both success and error content" in the brief verbatim.
- **`toLlmToolDefinition` schema conversion failure:** `z.toJSONSchema` can
  throw on schemas it can't represent (today's `harness.ts:206` already
  guards this with try/catch → `{}`). Same guard here: on throw, fall back to
  `{}` (empty object schema) rather than letting tool-catalog construction
  crash the whole turn.
- **Where `TOOL_RESULT_MAX_CHARS` lives:** exported as a named constant from
  `orchestrator.ts` (not a magic number), since R7's tests may want to assert
  against it directly.
- **Success content format:** kept identical to today's `harness.ts:327`
  (`` `Success: ${JSON.stringify(result)}` ``) for continuity with existing
  `describeObservation`-style summarizers referenced in the brief, then
  truncation is applied on top of that same string.

## Tasks

### Task 1: `executeTool()` orchestrator — happy path + truncation

**Build:** `src/lib/tools/orchestrator.ts` with `ToolExecutionResult`,
`ExecuteToolInput`, `TOOL_RESULT_MAX_CHARS = 16_000`, and `executeTool()`
implementing the full choke point in this order (matches today's
`harness.ts:259-332` `executeToolCall` ordering — ledger rows open
unconditionally before any validation, exactly like the existing code, so
every branch including `unknown_tool`/`permission_denied` is ledger-visible):

1. `const tool = registry.get(name)`.
2. `startRunStep(db, { runId, parentStepId, stepKind: "tool", name, input: { args: input } })`
   then `startToolCall(db, { runId, runStepId: toolStep.id, toolName: name, arguments: input, metadata: { permissionClass: tool?.permissionClass ?? null } })`
   — unconditional, before any check, using the raw unvalidated `input`.
3. `if (!tool)` → fail branch (Task 2) with code `unknown_tool`.
4. `if (!allowed.has(tool.permissionClass))` → fail branch with code
   `permission_denied`.
5. `const parsed = tool.argsSchema.safeParse(input)`; `if (!parsed.success)` →
   fail branch with code `invalid_args`.
6. `try { const result = await tool.execute(parsed.data, { db, runId, parentStepId: toolStep.id }); ... }`:
   - success → `finishToolCall(db, { toolCallId, result })`,
     `finishRunStep(db, { runStepId: toolStep.id, output: result })`, content
     = `truncate(\`Success: ${JSON.stringify(result)}\`)`, return
     `{ content, isError: false }`.
   - throw → fail branch with code `tool_error`, message
     `` `Tool ${name} failed: ${message}` ``.

Task 2 defines the shared fail branch (`failToolCall` + `failRunStep` +
truncated `is_error` content) that steps 3, 4, 5, and 6's catch all call into
— write it as one private helper so the four codes don't duplicate the
ledger-fail + truncate logic.

Note on `ExecuteToolInput.toolUseId`: it is accepted for shape-parity with the
contracts brief §4 (and the future correlation use the brief anticipates), but
this slice does not read it — the caller (`agent-loop.ts`) uses `block.id`
directly to build the `LlmToolResultBlock`. Do NOT wire it into ledger
metadata; accepted-but-unused is intentional, not a typo.

**Exact files:**
- Create `src/lib/tools/orchestrator.ts`.
- Create `tests/tool-orchestrator.test.ts`.

**Acceptance criteria:**
- `executeTool()` on a registered, permitted, valid-args tool returns
  `{ content: "Success: {...}", isError: false }` and the ledger shows one
  `run_steps` row (`stepKind: "tool"`, `status: "succeeded"`) and one
  `tool_calls` row (`status: "succeeded"`, `result` matching the tool's
  return value).
- A result whose `JSON.stringify(result)` exceeds 16,000 chars is truncated
  to `` `Success: ${json.slice(0, 16_000)}\n\n[truncated ${json.length - 16_000} chars]` ``
  and `isError` stays `false`.

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/tool-orchestrator.test.ts -t "executes a registered tool"
npx vitest run tests/tool-orchestrator.test.ts -t "truncates an oversized success result"
```
Expected: both PASS.

---

### Task 2: Denial/failure paths — `is_error` results, never throw

**Build:** the shared fail-branch helper referenced at the end of Task 1, plus
a private `truncate(content: string): string` helper used by every branch
(success and failure alike):

```ts
function truncate(content: string): string {
  if (content.length <= TOOL_RESULT_MAX_CHARS) return content;
  const overflow = content.length - TOOL_RESULT_MAX_CHARS;
  return `${content.slice(0, TOOL_RESULT_MAX_CHARS)}\n\n[truncated ${overflow} chars]`;
}

async function failTool(
  db: Sql,
  runStepId: number,
  toolCallId: number,
  errorType: string,
  message: string,
): Promise<ToolExecutionResult> {
  await failToolCall(db, { toolCallId, errorType, errorMessage: message });
  await failRunStep(db, { runStepId, errorType, errorMessage: message });
  return { content: truncate(`Error (${errorType}): ${message}`), isError: true };
}
```

All four failure codes (`unknown_tool`, `permission_denied`, `invalid_args`,
`tool_error`) call this same `failTool` helper from within `executeTool()`'s
steps 3-6 (Task 1) — no duplicated ledger-fail logic per code path.

**Exact files:** `src/lib/tools/orchestrator.ts` (extend Task 1's file),
`tests/tool-orchestrator.test.ts` (extend Task 1's file).

**Acceptance criteria:**
- Unknown tool name → `{ isError: true, content: "Error (unknown_tool): Unknown tool: <name>." }`; `tool_calls` row `status: "failed"`, `errorType: "unknown_tool"`.
- Tool whose `permissionClass` is not in `allowed` → `isError: true`,
  `errorType: "permission_denied"`; run continues (caller does not throw).
- Tool called with args failing `argsSchema.safeParse` → `isError: true`,
  `errorType: "invalid_args"`.
- Tool whose `execute()` throws → `isError: true`, `errorType: "tool_error"`,
  message includes the thrown error's message.
- An error message string exceeding 16,000 chars is truncated with the same
  `[truncated N chars]` notice as the success path.
- None of the five scenarios above throw out of `executeTool()` — assert via
  `await expect(executeTool(...)).resolves.toMatchObject(...)`, never
  `.rejects`.

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/tool-orchestrator.test.ts
```
Expected: PASS, all cases from Task 1 + Task 2 (7+ tests) green.

---

### Task 3: `toLlmToolDefinition()` — Tool → JSON Schema for the API `tools:` param

**Build:** `toLlmToolDefinition(tool: Tool): LlmToolDefinition` in
`src/lib/tools/orchestrator.ts`, importing `LlmToolDefinition` from
`@/lib/llm/types` (R1). Body:

```ts
export function toLlmToolDefinition(tool: Tool): LlmToolDefinition {
  let inputSchema: unknown = {};
  try {
    inputSchema = z.toJSONSchema(tool.argsSchema);
  } catch {
    inputSchema = {};
  }
  return { name: tool.name, description: tool.description, inputSchema };
}
```

**Exact files:** `src/lib/tools/orchestrator.ts` (extend), `tests/tool-orchestrator.test.ts` (extend).

**Acceptance criteria:**
- For a tool with `argsSchema: z.object({ text: z.string() })`,
  `toLlmToolDefinition(tool).inputSchema` is a JSON Schema object whose
  `properties.text.type === "string"` (exact shape from `z.toJSONSchema`, not
  hand-asserted string-equal — assert on the decoded object's `properties`
  key, not a serialized string, so the test doesn't pin to Zod's exact JSON
  Schema dialect version).
- `name`/`description` pass through unchanged.
- The `z.toJSONSchema` throw path falls back to `{}` instead of throwing out
  of `toLlmToolDefinition`. Test it with
  `vi.spyOn(z, "toJSONSchema").mockImplementationOnce(() => { throw new Error("boom"); })`
  around a call to `toLlmToolDefinition(tool)` with any ordinary `Tool` —
  `z.toJSONSchema` is a standalone function in zod@4.4.3 (it never calls a
  method on the schema object), so Proxy/getter constructions would never be
  invoked; the spy is the one deterministic way to exercise the catch branch.

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/tool-orchestrator.test.ts -t "toLlmToolDefinition"
```
Expected: PASS.

---

### Task 4: Cutover — `agent-loop.ts` imports `executeTool`/`toLlmToolDefinition` from `orchestrator.ts`

**Build:** Edit `src/lib/agent-loop.ts` (R2's file — the one explicitly-flagged
exception this plan makes to "don't touch other slices' files") to import
`executeTool` and `toLlmToolDefinition` from `./tools/orchestrator` in place of
the private `executeToolUseBlock`/`toLlmToolDefinition`/`truncate` functions R2
built there as a deliberate placeholder. Per R2's plan (Judgment call #1): "R3
lifts this logic verbatim into `src/lib/tools/orchestrator.ts` ... then updates
the imports [in `agent-loop.ts`] instead of the local definitions." Delete the
three private functions from `agent-loop.ts` and wire the loop body's call
sites to the imported `executeTool`/`toLlmToolDefinition` instead.
`agent-loop.ts` re-exports `ToolExecutionResult` from `orchestrator.ts` rather
than defining it locally (per R2's plan: "R3 will re-export it from
`orchestrator.ts` instead and delete the local copy").

**Exact files:** `src/lib/agent-loop.ts` (modify). `tests/agent-loop.test.ts`
(unchanged — re-run only, no edits; this is the parity proof, not a rewrite).

**Acceptance criteria:**
- `src/lib/agent-loop.ts` no longer defines `executeToolUseBlock`,
  `toLlmToolDefinition`, `truncate`, or `TOOL_RESULT_MAX_CHARS`; it imports
  `executeTool` and `toLlmToolDefinition` from `./tools/orchestrator` and
  re-exports `ToolExecutionResult` from there.
- `tests/agent-loop.test.ts` (R2's existing suite, not modified by this task)
  passes unchanged — proves behavior parity between the old private copy and
  the lifted `orchestrator.ts` implementation.

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run tests/agent-loop.test.ts tests/tool-orchestrator.test.ts
```
Expected: both suites PASS with zero edits to `tests/agent-loop.test.ts` — the
parity proof this task exists to produce.

---

### Task 5: Full suite + lint + build verification

**Build:** nothing new — final gate confirming R3 didn't regress anything
already green, since `src/lib/tools/types.ts`/`registry.ts` are read-only
reference in this slice and `harness.ts` is untouched (Task 4 touches only
`agent-loop.ts`, per above).

**Exact files:** none (verification only).

**Acceptance criteria:**
- Full existing suite plus the new `tests/tool-orchestrator.test.ts` all
  green.
- `npm run lint` clean.
- `npm run build` succeeds (orchestrator.ts type-checks against `@/lib/llm/types`
  from R1's merged state).

**Verify:**
```bash
export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"
npx vitest run
npm run lint
npm run build
```
Expected: all three succeed; no existing test file's behavior changed
(`tests/harness.test.ts`, `tests/tool-registry.test.ts`, `tests/echo-tool.test.ts`,
`tests/agent-loop.test.ts` untouched and still passing, since this slice adds a
new file, touches only `agent-loop.ts` per Task 4, and does not edit
`harness.ts`, `types.ts`, or `registry.ts`).

---

## Tests

New file: `tests/tool-orchestrator.test.ts`. Mirrors the DB-reset pattern used
in `tests/harness.test.ts` (`resetLedgerTables()` + `runMigrations` in
`beforeEach`, `closeDb()` in `afterAll`, `DATABASE_URL` pointing at the local
Postgres container). Cases (7-9 total, matching the spec's "+4-6 orchestrator"
testing-plan line plus the 2-3 `toLlmToolDefinition` cases folded into the
same file):

1. Executes a registered, permitted tool with valid args → success content +
   succeeded ledger rows (Task 1).
2. Truncates an oversized success result with the `[truncated N chars]`
   notice (Task 1).
3. Unknown tool name → `is_error` + `unknown_tool` ledger row (Task 2).
4. Permission-denied tool → `is_error` + `permission_denied` ledger row, does
   not throw (Task 2).
5. Invalid args (fails `argsSchema.safeParse`) → `is_error` + `invalid_args`
   (Task 2).
6. Tool `execute()` throws → `is_error` + `tool_error`, message includes the
   thrown error (Task 2).
7. Oversized error message is truncated the same way as success (Task 2).
8. `toLlmToolDefinition` produces a JSON Schema whose `properties` reflect
   the tool's `argsSchema` (Task 3).
9. `toLlmToolDefinition` falls back to `{}` when `z.toJSONSchema` throws —
   via the `vi.spyOn(z, "toJSONSchema")` mock, per Task 3 (Task 3).

Exact `it()` titles are locked so the `-t` filters in Tasks 1-3's Verify
commands match verbatim; use these titles exactly:
- Case 1: `"executes a registered tool"`
- Case 2: `"truncates an oversized success result"`
- Cases 8-9: titles must begin with `"toLlmToolDefinition"`
All other cases: any descriptive title (they are only ever run via the
whole-file command in Task 2/Task 5).

## Self-Review

**Spec/brief coverage:**
- Choke-point order (validate → permit → execute → log → truncate) → Tasks 1-2.
- `is_error` tool results, never exceptions → Task 2, asserted via `.resolves`.
- 16k truncation with visible notice, both success and error → Tasks 1, 2.
- `toLlmToolDefinition` JSON-Schema export → Task 3.
- `types.ts`/`registry.ts` untouched → stated in Context; Task 5 verifies no
  existing test regresses.
- `agent-loop.ts`'s private placeholder copy (`executeToolUseBlock`/
  `toLlmToolDefinition`/`truncate`) is actually replaced with an import from
  `orchestrator.ts`, so `executeTool()` is exercised at runtime instead of
  sitting dead — Task 4, verified by re-running R2's unmodified
  `tests/agent-loop.test.ts`.
- Acceptance criterion 3 from the sprint spec ("A permission-denied tool call
  appears in the transcript as an `is_error` tool_result and the run
  continues; nothing throws") → directly covered by Task 2's permission-denial
  case; the "appears in the transcript" half (turning this `ToolExecutionResult`
  into an `LlmToolResultMessage`) is R2's loop-body logic (unchanged by Task 4's
  import swap) — `executeTool()` returning the correctly-shaped
  `{ content, isError }` is this slice's full contribution to that criterion.

**Placeholder scan:** no TBD/TODO; every task has literal code/test content.

**Type consistency:** `ExecuteToolInput`/`ToolExecutionResult`/`executeTool`/
`toLlmToolDefinition` match the contracts brief §4 signatures exactly;
`LlmToolDefinition` imported from `@/lib/llm/types` (R1), not redefined here.

## Eng Review (2026-07-14)

**Verdict: approve (revised)** — the must-fix (#1) and both should-fix items
(#2, #3) below were applied to the plan body on 2026-07-14: Task 3's
acceptance criteria + Tests case 9 now specify the `vi.spyOn(z, "toJSONSchema")`
approach, Task 1 carries the explicit `toolUseId` accepted-but-unused note,
and the Tests section locks the exact `it()` titles the `-t` filters use.
Original review text preserved below.

Grounded against the live codebase, not from memory. Every
line-number citation was checked and is accurate: `harness.ts:259-332`
(`executeToolCall`), `harness.ts:206` (the `z.toJSONSchema` try/catch guard),
`harness.ts:327` (`Success: ${JSON.stringify(result)}`). `src/lib/tools/types.ts`
and `registry.ts` were read in full and match the plan's "read-only reference"
claim exactly — nothing in this plan requires touching them. All six
`src/lib/ledger.ts` functions the plan calls (`startRunStep`, `finishRunStep`,
`failRunStep`, `startToolCall`, `finishToolCall`, `failToolCall`) were checked
against their real interfaces (`StartRunStepInput`, `FinishRunStepInput`, etc.)
and every field name/shape used in the plan's pseudocode matches today's
`ledger.ts` — no invented signatures. Choke-point order (unknown tool →
permission gate → `argsSchema.safeParse` → execute → ledger log → truncate)
matches contracts brief §4 verbatim and matches today's `executeToolCall`
ordering. Cross-checked against R2's plan (`2026-07-14-r2-agent-loop-plan.md`)
and R1's plan (`2026-07-14-r1-provider-adapters-plan.md`): the dependency
chain, the "R3 lifts this verbatim" hand-off language, and R2's placeholder
`executeToolUseBlock`/`toLlmToolDefinition`/`truncate` in `agent-loop.ts` are
consistent with what this plan does in Task 4. Confirmed on the live repo that
none of R1/R2/R3's files exist yet (`src/lib/llm/`, `src/lib/agent-loop.ts`,
`src/lib/tools/orchestrator.ts` all absent) — this plan's "R1 and R2 must
already be merged" dependency is real, not decorative, and the plan correctly
refuses to re-derive or stub those shapes rather than guessing at them.

**Must-fix:**

1. **Task 3's throw-fallback test won't exercise the catch branch as described.**
   Verified directly against the installed `zod@4.4.3`: `z.toJSONSchema(schema)`
   is a **standalone function** that inspects the schema's internal `_zod` def
   — it never calls a `.toJSONSchema()` method *on* the schema object. The
   plan's suggested construction ("a fake `Tool` whose `argsSchema` is an
   object with a `toJSONSchema`-triggering getter that throws") describes a
   getter that would never be accessed, so the test as sketched will not
   exercise `toLlmToolDefinition`'s catch branch — it'll silently pass by
   calling a schema that never throws, or fail to compile because a plain
   object with a getter isn't a valid `z.ZodType`. Fix: specify the test
   directly as `vi.spyOn(z, "toJSONSchema").mockImplementationOnce(() => { throw new Error("boom"); })`
   around a call to `toLlmToolDefinition(tool)` with any ordinary `Tool` —
   deterministic, no schema-construction guesswork, and it actually calls the
   code path being tested. Replace the "Proxy"/"monkeypatch" hedge language in
   the acceptance criteria with this one approach.

**Should-fix:**

2. **`ExecuteToolInput.toolUseId` (contracts brief §4) is never read anywhere
   in Task 1's algorithm.** The Self-Review claims `ExecuteToolInput` "matches
   the contracts brief §4 signatures exactly," which includes `toolUseId:
   string`, but the 6-step algorithm in Task 1 never touches it — ledger
   writes use `runId`/`parentStepId`/`name`/`input` only. This isn't a
   functional bug: the caller (`agent-loop.ts`) already holds `block.id` in
   scope and uses it directly to build the `LlmToolResultBlock` after
   `executeTool()` returns, so nothing is lost. But an implementing agent
   following this plan task-by-task will hit an accepted-but-unused
   destructured field and may either wire it into ledger metadata
   speculatively (out of scope, not asked for) or get stuck wondering if it's
   a typo. Add one line to Task 1 stating explicitly: "`toolUseId` is accepted
   for shape-parity with the brief and the future correlation use the brief
   anticipates; this slice does not read it — the caller uses `block.id`
   directly to build the `LlmToolResultBlock`."
3. **The `-t "<substring>"` filters in Tasks 1-3's Verify commands presuppose
   exact `it(...)` title text** ("executes a registered tool", "truncates an
   oversized success result", "toLlmToolDefinition") that the plan never
   locks down — unlike R1's and R2's sibling plans, which embed the full
   literal test file content so `-t` filters are guaranteed to match. R3's
   Task 1/2/3 give narrative case descriptions ("Executes a registered,
   permitted tool with valid args...") that don't verbatim match the `-t`
   strings used two paragraphs later. Low risk (Vitest `-t` is a substring
   match and the implementing agent controls both the test names and the
   verify command), but tighten it: either quote the exact `it(...)` title
   strings the tests must use, or drop the `-t` filters and just run the
   whole file per task.

**Notes (no action required):**
- R2's plan describes this hand-off as "R3 lifts this logic verbatim into
  `orchestrator.ts`," but R3's actual Task 2 helpers (`truncate(content):
  string` + a named `failTool(db, runStepId, toolCallId, errorType, message)`)
  are not byte-identical to R2's closure-based `truncate(content, isError)` +
  inline `fail` closure — externally-observable behavior matches (same order
  of operations, same ledger calls, same content strings), so this is a
  documentation-language nit, not a functional risk. Worth a one-line
  acknowledgment in the plan so "verbatim" doesn't imply a literal
  copy-paste diff review is sufficient for Task 4.
- Complexity check: 2 new files (`orchestrator.ts`, its test file), 1 modified
  file (`agent-loop.ts`, Task 4 only), 0 new classes. Well under the
  8-file/2-class threshold — no scope-reduction conversation needed.
- Distribution/rollback: this is a library-internal refactor with no new
  artifact type and no external interface change; Tasks 1-3 are additive
  (new file, dead code until Task 4 lands) and independently revertable by
  deleting `orchestrator.ts`; Task 4 is the only file with blast radius
  (`agent-loop.ts`), and its own acceptance criterion (R2's unmodified
  `tests/agent-loop.test.ts` passing unchanged) is a real parity proof, not a
  self-asserted one.

NO UNRESOLVED DECISIONS
