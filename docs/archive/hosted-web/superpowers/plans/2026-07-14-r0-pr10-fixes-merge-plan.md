# R0 — PR #10 Fixes + Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the two outstanding review defects on PR #10 (`feat/a-chat-streaming` →
`main`) and merge it, so R1 onward starts from a clean `main` with no known chat-stream
defects.

**Depends on:** none — R0 is the root of the sprint dependency graph
(`R0 → R1 → R2 → R4 → R5 → R9`, `R1 → R3 → R7`). Every later slice's plan assumes R0 is
merged into `main`.

## Context

PR #10 (`https://github.com/FisherXZ/Sourcecado/pull/10`, currently OPEN, base `main`)
added streaming multi-turn Research Chat. Two review defects are open per
`docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md` and
`findings.md`:

1. **Dead error guard** — `src/app/chat/stream.ts:97`:
   ```ts
   if (!res.ok && !res.body) {
     throw new Error(`Stream failed (${res.status})`);
   }
   ```
   `fetch` responses almost always carry a body (the `/api/agent/stream` route's `400`
   path returns `NextResponse.json({ error: "question is required" }, { status: 400 })`,
   which has a body). So this condition is false on every real error response, and
   `runChat` falls through to read that JSON error body as if it were an SSE stream.
   `drainSse` finds no `data:` lines in `{"error":"..."}`, produces zero chunks, and
   `runChat` resolves with an empty, silently-successful-looking turn
   (`{ steps: [], answer: "" }`) instead of surfacing the failure.

2. **Errored exchanges leak into follow-up history** — `src/app/chat/ChatClient.tsx`,
   `submit()`:
   ```ts
   const history: ConversationTurn[] = exchanges
     .filter((e) => e.done && e.turn.answer)
     .flatMap((e) => [...]);
   ```
   When `runChat` rejects, the `.catch()` handler sets `errored: true` **and** writes the
   rejection/timeout message into `turn.answer` (e.g. `"The run timed out before
   completing. Try again."`) so it renders in the alert box. That same non-empty
   `turn.answer` passes the `e.turn.answer` truthy check above, so a failed exchange's
   error text is sent to the model as a fake prior assistant answer on the next turn.

Confirmed by reading both files in full (`src/app/chat/stream.ts`,
`src/app/chat/ChatClient.tsx`) and the stream route
(`src/app/api/agent/stream/route.ts`) plus its test
(`tests/agent-stream-route.test.ts`): the route always returns `200` for an
*internal* agent failure (status carried in the `data-meta` part, per
`"still emits a meta part when the run fails with no answer"`); a non-2xx HTTP status
from this route only occurs for the `400` missing-question case (and, in principle, an
uncaught `500`). So fix 1 only needs to key off `res.ok`, not response content.

**Per the contracts brief (`docs/superpowers/plans/2026-07-14-r-contracts-brief.md`
§7):** R0 owns exactly these two fixes in these two files — `stream.ts`'s guard and
`ChatClient.tsx`'s history filter — nothing else. R5 rewrites both files again later for
the typed `LlmStreamEvent` union; do not anticipate that work here.

## Judgment calls

- **Fix 1 stays minimal — no error-body parsing.** The dead guard's original intent was
  arguably "throw immediately only when there's truly nothing to read," implying an
  unwritten else-branch that parses the body for a message. That's not what's broken (no
  known case needs the parsed message today) and it's not what the spec asks for
  ("error responses must surface, not resolve as empty turns"). Leanest correct fix:
  `if (!res.ok) throw new Error(...)`, unconditionally, using only `res.status` in the
  message — no `res.json()`/`res.text()` attempt. If a later slice wants the server's
  `error` field surfaced verbatim, that's new scope, not this defect.
- **`data-meta` with `status: "failed"` is not touched.** Today, when `answerWithMemory`
  itself reports `status: "failed"` with no `answer` (e.g. max-steps exceeded), the
  fetch still succeeds (`200`), `runChat` resolves normally, `ChatClient` does not set
  `errored`, and the existing `e.turn.answer` truthy check already excludes it from
  history (empty string is falsy). That path already works correctly and is not part of
  either named defect — left untouched per "surgical changes."

## Tasks

### Task 1: Fix the dead error guard in `stream.ts`

**Files:**
- Modify: `src/app/chat/stream.ts`
- Modify: `tests/chat-stream.test.ts`

**What to build:** Replace the dead guard with an unconditional `!res.ok` check so any
non-2xx response throws before the code attempts to read a body that was never an SSE
stream.

- [ ] **Step 1: Write the failing test**

  Append to `tests/chat-stream.test.ts` (new `describe("runChat", ...)` block; needs
  `global.fetch` stubbed since `runChat` isn't exercised by the existing pure-function
  tests in this file). Add `import { vi } from "vitest";` alongside the existing top-of-
  file import (this repo's convention — see `tests/components/ChatClient.test.tsx` —
  is to import `vi` explicitly rather than rely on the `globals: true` injection):

  ```ts
  import { vi } from "vitest";
  import { runChat } from "@/app/chat/stream";

  function sseResponse(body: string, init: { status?: number } = {}): Response {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    });
    return new Response(stream, { status: init.status ?? 200 });
  }

  describe("runChat", () => {
    afterEach(() => {
      vi.unstubAllGlobals();
    });

    it("throws on a non-ok response instead of resolving an empty turn", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify({ error: "question is required" }), { status: 400 })
        )
      );
      await expect(runChat("", [], () => {})).rejects.toThrow(/400/);
    });

    it("resolves the accumulated turn on a 200 SSE response", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          sseResponse('data: {"type":"text-delta","id":"answer","delta":"Hi"}\n\n')
        )
      );
      const turn = await runChat("hi", [], () => {});
      expect(turn.answer).toBe("Hi");
    });
  });
  ```

- [ ] **Step 2: Run the test to verify the first case fails**

  Run: `npx vitest run tests/chat-stream.test.ts`
  Expected: the non-ok test FAILS — with today's guard, `runChat` does not throw (it
  resolves `{ steps: [], answer: "" }`), so `.rejects.toThrow` fails. The 200 case
  already passes (unrelated to the bug).

- [ ] **Step 3: Fix `src/app/chat/stream.ts`**

  Replace:
  ```ts
  if (!res.ok && !res.body) {
    throw new Error(`Stream failed (${res.status})`);
  }
  ```
  with:
  ```ts
  if (!res.ok) {
    throw new Error(`Stream failed (${res.status})`);
  }
  ```

- [ ] **Step 4: Run the test to verify it passes**

  Run: `npx vitest run tests/chat-stream.test.ts`
  Expected: PASS — all tests in the file, including both new `runChat` cases.

- [ ] **Step 5: Commit**

  ```bash
  git add src/app/chat/stream.ts tests/chat-stream.test.ts
  git commit -m "fix(r0): surface non-ok /api/agent/stream responses instead of resolving an empty turn"
  ```

---

### Task 2: Filter errored exchanges out of follow-up history in `ChatClient.tsx`

**Files:**
- Modify: `src/app/chat/ChatClient.tsx`
- Modify: `tests/components/ChatClient.test.tsx`

**What to build:** Add `!e.errored` to the history filter in `submit()` so a rejected/
timed-out exchange's error text (which lives in `turn.answer` for rendering) is never
threaded into the next turn's history as a fake assistant answer.

- [ ] **Step 1: Write the failing test**

  Append to the `describe("ChatClient", ...)` block in `tests/components/ChatClient.test.tsx`:

  ```ts
  it("excludes an errored exchange's message from the next turn's history", async () => {
    runChatMock
      .mockImplementationOnce((_q: string, _h: unknown, onUpdate?: (t: unknown) => void) =>
        onUpdate ? Promise.reject(new Error("stream dropped")) : Promise.resolve({ steps: [], answer: "" })
      )
      .mockResolvedValueOnce({
        steps: [],
        answer: "Second answer.",
        meta: { runId: 2, status: "succeeded", steps: 0, invalidCitations: [] },
      });

    render(<ChatClient />);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "first question" } });
    fireEvent.submit(screen.getByRole("textbox"));
    await screen.findByRole("alert");

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "follow up" } });
    fireEvent.submit(screen.getByRole("textbox"));
    await waitFor(() => expect(screen.getByText("Second answer.")).toBeInTheDocument());

    const secondCallHistory = runChatMock.mock.calls[1][1];
    expect(secondCallHistory).toEqual([]);
  });
  ```

- [ ] **Step 2: Run the test to verify it fails**

  Run: `npx vitest run tests/components/ChatClient.test.tsx`
  Expected: FAIL — today's filter (`e.done && e.turn.answer`) includes the errored
  exchange because `turn.answer` holds the error message "stream dropped", so
  `secondCallHistory` is `[{ role: "user", content: "first question" }, { role:
  "assistant", content: "stream dropped" }]`, not `[]`.

- [ ] **Step 3: Fix `src/app/chat/ChatClient.tsx`**

  In `submit()`, change:
  ```ts
  const history: ConversationTurn[] = exchanges
    .filter((e) => e.done && e.turn.answer)
    .flatMap((e) => [
  ```
  to:
  ```ts
  const history: ConversationTurn[] = exchanges
    .filter((e) => e.done && !e.errored && e.turn.answer)
    .flatMap((e) => [
  ```

- [ ] **Step 4: Run the test to verify it passes**

  Run: `npx vitest run tests/components/ChatClient.test.tsx`
  Expected: PASS — all tests in the file, including the new one.

- [ ] **Step 5: Commit**

  ```bash
  git add src/app/chat/ChatClient.tsx tests/components/ChatClient.test.tsx
  git commit -m "fix(r0): exclude errored exchanges from follow-up chat history"
  ```

---

### Task 3: Full verification + merge PR #10

**Files:** none (verification + merge only).

- [ ] **Step 1: Run the targeted tests, then the full suite**

  Run: `npx vitest run tests/chat-stream.test.ts tests/components/ChatClient.test.tsx`
  Expected: PASS — 100% green. These are the only two files this plan touches, so this
  is the hard gate for Tasks 1–2.

  Then run: `npx vitest run`
  Expected: no *new* failures beyond the known, pre-existing `better-sqlite3`-bindings
  issue (27 test files / 165 tests fail with `Could not locate the bindings file`,
  caused by Node v26.5.0 lacking a compiled native binary for `better-sqlite3` —
  documented in `TODOS.md` and `README.md`). This includes `tests/db.test.ts`,
  `tests/stress/`, etc. That failure class is pre-existing, unrelated to R0, and out of
  scope for this merge — do not attempt to fix it here.

- [ ] **Step 2: Lint**

  Run: `npm run lint`
  Expected: no *new* warnings beyond the existing, pre-existing `src/lib/memory/embed.ts:23`
  ("Unused eslint-disable directive") warning, which is unrelated to this plan and out of
  scope.

- [ ] **Step 3: Build**

  Run: `npm run build`
  Expected: build succeeds; `/chat` route still present in the route list.

- [ ] **Step 4: Push the fixes to the PR branch**

  ```bash
  git push origin feat/a-chat-streaming
  ```
  Expected: push succeeds; PR #10 (`https://github.com/FisherXZ/Sourcecado/pull/10`)
  shows the two new commits.

- [ ] **Step 5: Confirm PR #10 is mergeable and merge it**

  ```bash
  gh pr view 10 --json mergeable,mergeStateStatus,statusCheckRollup
  ```
  Expected: `"mergeable": "MERGEABLE"` (or checks passing/absent — this repo has no CI
  configured yet, so there may be no `statusCheckRollup` entries; that's not a blocker).

  ```bash
  gh pr merge 10 --squash --delete-branch
  ```
  Expected: PR #10 merges into `main`; the fixed chat streaming code is now on `main`
  with no known open defects.

- [ ] **Step 6: Verify `main` is clean post-merge**

  ```bash
  git fetch origin main
  git log --oneline origin/main -5
  ```
  Expected: the merge commit for PR #10 (containing both fix commits) is at the tip of
  `origin/main`.

## Tests

| File | New tests | What it covers |
|---|---|---|
| `tests/chat-stream.test.ts` | `describe("runChat", ...)`: 2 cases | Non-ok response throws (not an empty resolved turn); 200 SSE response still resolves the accumulated turn (regression guard for the fix) |
| `tests/components/ChatClient.test.tsx` | 1 case | An errored exchange's message is excluded from the `ConversationTurn[]` sent as history on the next `runChat` call |

No new test files — both fixes are covered by extending the existing test files for
`stream.ts` and `ChatClient.tsx`. No DB/Postgres dependency for either (both are
client-side, pure-logic-plus-fetch and React-component tests respectively).

## Self-Review

**Spec coverage:** Both R0 defects named in the spec's "Current State" table and in
`docs/superpowers/plans/2026-07-14-r-contracts-brief.md` §7 (`stream.ts` guard fix,
`ChatClient.tsx` history filter fix) are covered — Task 1 and Task 2 respectively. Task 3
covers the spec's "merge PR #10" and acceptance criterion #10 ("PR #10 merged; existing
... tests still green after every slice").

**Scope discipline:** No touch to `src/app/api/agent/stream/route.ts`,
`ReasoningTrace.tsx`, `StepRow.tsx`, or the SSE parsing/event shape itself — those are
R5's rewrite, explicitly out of scope here per the contracts brief's file-ownership
table.

**Placeholder scan:** No TBD/TODO; every step has complete code.

## Eng Review (2026-07-14)

**Verdict: approve (revised)** — the two fixes themselves are correct and minimal; both
must-fix acceptance-criteria issues in Task 3 have been reworded to reflect the current
environment's known, pre-existing, out-of-scope failures.

### What I verified against the real code

- `src/app/chat/stream.ts:97-99` matches the plan's "before" snippet exactly. Traced
  `Response` semantics: a `Response` built from a string body (the 400 JSON error path)
  still has a non-null `.body` ReadableStream in Node's `fetch`/undici implementation, so
  `!res.ok && !res.body` is always `false` for that path — confirms the guard is
  genuinely dead, not a hypothetical bug.
- `src/app/api/agent/stream/route.ts` confirmed: only returns non-2xx for the missing-
  question `400` case; `tests/agent-stream-route.test.ts`'s "still emits a meta part when
  the run fails" test confirms internal agent failures stay `200`. The plan's claim that
  fix 1 only needs `res.ok` — no body inspection — holds.
- `src/app/chat/ChatClient.tsx:61-62` matches the plan's "before" snippet exactly. Traced
  the `.catch()` path: `errored: true` is set alongside `turn.answer = message`, so the
  existing filter's `e.turn.answer` truthy check does let an errored exchange through
  today — the second defect is real, not overstated.
- Ran both new tests' logic by hand against the actual source (pre-fix): the `runChat`
  non-ok test would resolve instead of reject (confirms Step 2's "FAILS" claim); the
  `ChatClient` history test would produce `[{role:user,...},{role:assistant,content:
  "stream dropped"}]` instead of `[]` (confirms Step 2's "FAIL" claim). Both fixes as
  written flip these to pass with no other side effects.
- Confirmed `tests/chat-stream.test.ts` and `tests/components/ChatClient.test.tsx` don't
  currently import `vi` — plan's claim that explicit `vi` import is this repo's
  convention is accurate (`tests/components/ChatClient.test.tsx` and
  `tests/agent-stream-route.test.ts` both do it despite `globals: true` in
  `vitest.config.ts` making it optional).
- Ran the three targeted test files pre-fix: `tests/chat-stream.test.ts`,
  `tests/components/ChatClient.test.tsx`, `tests/agent-stream-route.test.ts` — all 14
  existing tests pass, confirming no test currently depends on the buggy behavior.
- Ran `npm run build`: succeeds, `/chat` route present in the route list — Task 3 Step 3
  is achievable as written.
- Confirmed via `gh pr view 10`: PR #10 is OPEN, `mergeable: MERGEABLE`,
  `mergeStateStatus: CLEAN` against `main` today — Task 3 Steps 4-6 have no blocking
  precondition right now.
- Contracts-brief §7 file-ownership table: plan touches only the two R0-owned files plus
  their test files. No touch to `route.ts`, `ReasoningTrace.tsx`, `StepRow.tsx`, or the
  SSE event shape — confirmed no violation of R5's ownership. No error-body parsing
  added — confirmed no scope creep beyond the named defect.

### Must-fix (resolved)

1. **RESOLVED — Task 3 Step 1's acceptance criterion is false in the current
   environment for reasons unrelated to this plan, and the plan doesn't say so.** Ran
   `npx vitest run`: 27 test files / 165 tests fail with `Could not locate the bindings
   file` from `better-sqlite3` — a pre-existing, already-documented issue (`TODOS.md`
   "Fix better-sqlite3 native bindings", `README.md:45`) caused by Node v26.5.0 not
   having a compiled binary for this native module (confirmed: `npm rebuild
   better-sqlite3` fails with v8 header deprecation errors from node-gyp). This is not
   introduced by R0 and not fixable by this plan, but as written, Step 1 said "Expected:
   PASS — every existing suite ... green, zero regressions," which whoever executes this
   plan will not observe. Fixed: Step 1 now runs
   `tests/chat-stream.test.ts tests/components/ChatClient.test.tsx` first (must be 100%
   green — the only files this plan touches), then the full `npx vitest run` with an
   explicit callout that the known, pre-existing `better-sqlite3`-bindings failures
   (`tests/db.test.ts`, `tests/stress/`, etc., per TODOS.md) are out of scope and don't
   block the merge.
2. **RESOLVED — Task 3 Step 2's lint acceptance criterion has the same problem, smaller
   stakes.** `npm run lint` today already prints one pre-existing warning unrelated to
   this plan (`src/lib/memory/embed.ts:23`, "Unused eslint-disable directive"). The plan
   said "Expected: `✔ No ESLint warnings or errors`," which is not what running it
   produces even before Task 1/2's changes. Fixed: reworded to "no *new* warnings beyond
   the existing `embed.ts` one" so the step doesn't read as failed when it's actually
   pre-existing noise.

### Notes (non-blocking)

- The working tree has unrelated uncommitted changes right now (an import-extension
  cleanup across `src/chunk.ts`, `src/extractors/*`, `src/lib/memory/{chunk-store,chunk,
  extract,ingest}.ts` — 10 files, stripping `.js` from relative imports) plus several
  untracked planning docs. Task 1/2's `git add <specific files>` commands are correctly
  scoped and won't sweep these in, so this isn't a plan defect — but Task 3 Step 4 (push)
  should be preceded by a `git status` sanity check to confirm only the two intended
  commits are queued to push, given how much unrelated WIP is sitting in the tree.
- Task 3 Steps 4-5 (push, then squash-merge) have no stated contingency if `gh pr merge`
  fails (e.g., a concurrent push to `main` flips `mergeable` between Step 5's check and
  the merge call). Low-likelihood on what looks like a single-developer branch, but worth
  one line: "if merge fails, re-run the `gh pr view` check — don't force anything."
- No test-coverage gaps found: the two new test cases are minimal, well-targeted, and
  each follows red-green TDD (write failing test, verify it fails for the *right*
  reason, fix, verify it passes). The `runChat` 200-SSE-still-works case is a good
  regression guard for the `!res.ok` change specifically, not just incidental coverage.
- No rollback step is needed for Tasks 1-2 (both are two-line reversible diffs on a
  feature branch, not yet on `main`); the only irreversible action in the whole plan is
  the squash-merge + branch delete in Task 3 Step 5, which is correctly the last step.

NO UNRESOLVED DECISIONS
