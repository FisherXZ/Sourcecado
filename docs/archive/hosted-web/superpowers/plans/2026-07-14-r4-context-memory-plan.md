# R4 — Context Assembly + Two-Layer Memory Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the rigid, prose-driven `MEMORY_INSTRUCTIONS` four-section
contract with a sectioned system prompt built by a new `src/lib/context.ts`
(identity + free-format tool-use guidance + an injected, capped memory index),
and make the citation post-check "check-on-use per turn" instead of
"once over the whole run."

**Depends on:** R0 (PR #10 merged), R1 (provider adapters), R2 (the loop +
`harness.ts` thin wrapper). Per the spec's dependency graph
(`R0 → R1 → R2 → R4 → R5 → R9`), this plan assumes R0–R2 are already merged
and diffs against that resulting state, not current `main`. R3 (tool
orchestrator) and R4 are parallel siblings off R2 — this plan does not depend
on R3 landing first.

**Architecture:** `src/lib/context.ts` exports a small, pure
`buildSystemPrompt(sections)` joiner, an async `buildMemoryIndexSection(db,
actor?)` that queries permission-filtered, capped memory state, two fixed
`SystemPromptSection` constants (identity, tool-use guidance), and a composer
`buildMemoryAnswerInstructions(db, actor?)` that the memory-chat path
(`src/lib/memory/answer.ts`) calls to build the string it hands to
`runAgent()`'s existing per-run `instructions` pass-through slot.
`src/lib/memory/citations.ts` gains a `sinceStepId` scoping parameter and a
check-on-use skip (no `search_memory` call in-scope → nothing to strip).
`MEMORY_INSTRUCTIONS` is deleted from `src/lib/memory/answer-config.ts`.

**Tech Stack:** TypeScript, `postgres` (Sql), Vitest against live Postgres —
same conventions as the rest of `src/lib/memory/*`.

## Context (read this before starting)

- Source spec: `docs/superpowers/specs/2026-07-14-runtime-solidification-sprint-spec.md`
  (R4 section, "Decisions locked", Acceptance Criteria #1).
- Binding contracts: `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`
  §5 ("Context assembly") and §7 (file ownership table) — this plan conforms
  to both; any place it must diverge is called out explicitly below and under
  "Judgment calls."
- Current repo state (pre-R0–R3, verified 2026-07-14): `src/lib/harness.ts`
  still has the old prose ReAct loop (`agentDecisionSchema`,
  `buildAgentSystemPrompt`, `buildUserPrompt`); `src/lib/memory/answer-config.ts`
  exports `MEMORY_INSTRUCTIONS` (the four-section contract) and
  `memoryRegistry()`; `src/lib/memory/answer.ts` is the **only** production
  consumer of both — it calls `runAgent({ ..., instructions: MEMORY_INSTRUCTIONS })`.
  By the time this plan executes, R2 will have rewritten `harness.ts` into a
  thin wrapper per the brief's §3, but `RunAgentInput`'s `instructions?: string`
  pass-through slot survives unchanged (brief §5: "per-run instructions
  (optional) — unchanged pass-through slot ... was `RunAgentInput.instructions`").
  This plan does not touch `harness.ts` (R2-owned) — it only changes what
  string `answer.ts` passes into that existing slot.
- `src/lib/memory/sources.ts` already exists (`listSources`,
  `setSourceArchived`, both actor-scoped via `resolveAllowedSourceIds`). This
  plan adds `listMemoryIndexRows` to it, per brief §5's explicit file
  ownership note.
- `src/lib/memory/citations.ts`'s `verifyAnswerCitations`/
  `collectBundlesFromTrace` are called from exactly one production call site
  (`answer.ts`) and are otherwise imported only by
  `tests/memory-answer.test.ts` for `collectAllowedCitations`/`checkCitations`
  (which this plan does not change).
- `tests/harness-multiturn.test.ts` currently has a describe block
  `"MEMORY_INSTRUCTIONS — multi-turn citation safety (N3)"` asserting on the
  now-deleted export's prose. R2's harness.ts rewrite may already have
  touched this file (it also tests the now-deleted `buildUserPrompt`) — Task 4
  below greps fresh rather than assuming this file's exact current shape.

## Judgment calls

(Per instructions: these are leanest-call resolutions of things the spec/brief
left open for this slice — not relitigations of locked decisions.)

1. **`src/lib/context.ts` may already exist when this plan executes.** The
   brief's file-ownership table marks it "R4, New," but its own §3 describes
   R2's `harness.ts` wrapper calling `buildSystemPrompt` (§5) — which can only
   happen if R2 already created a minimal `context.ts`. Resolution: Task 2
   reads the file first; if absent, creates it fresh with everything below;
   if present, **adds** the exports below without touching/removing whatever
   R2 put there. Either way the end state has all of: `SystemPromptSection`,
   `buildSystemPrompt`, `IDENTITY_SECTION`, `TOOL_USE_GUIDANCE_SECTION`,
   `buildMemoryIndexSection`, `buildMemoryAnswerInstructions`.
2. **`memoryRegistry()` stays in `src/lib/memory/answer-config.ts`.** It's
   tool-registry construction, not context/prompt assembly — a distinct
   concern from what `context.ts` owns. Moving it would be a pure rename with
   no behavior change, so it stays put (brief §7 left this open; minimal diff
   wins).
3. **`src/lib/memory/answer.ts` is touched by this plan** even though the
   brief's file-ownership table doesn't list it. It's the sole production
   caller of `MEMORY_INSTRUCTIONS`/`memoryRegistry()` and must be updated or
   the build breaks the moment `MEMORY_INSTRUCTIONS` is deleted. This is a
   necessary consequence of the locked "delete `MEMORY_INSTRUCTIONS`"
   decision, not scope creep — flagged here per the brief's own instruction
   to call out deviations rather than silently diverge.
4. **The check-on-use skip (no `search_memory` call → nothing to strip)
   lives inside `verifyAnswerCitations` itself**, not as caller-side
   branching in `answer.ts`. The brief's "hook point" language describes the
   externally observable behavior, not which file the branch lives in;
   putting it in the one already-exported choke point keeps `answer.ts`'s
   diff to a single added parameter.
5. **`src/lib/memory/notes.ts` needs no code changes for this slice.**
   `addMemoryNote` already writes `source_type = 'note'` rows; the memory
   index reads them straight out of `source_records` via the new
   `listMemoryIndexRows` in `sources.ts`. (Noted because `notes.ts` was named
   as a primary file for this slice but the brief assigns the index query to
   `sources.ts`.)
6. **`AnswerWithMemoryInput` does not gain an `actor` or `sinceStepId` field
   in this slice.** Single-tenant `DEFAULT_ACTOR` is the existing v1 pattern
   everywhere in `src/lib/memory/*`; per-turn `sinceStepId` scoping has no
   caller until multi-turn chat sessions land in R6. This slice adds the
   *capability* (optional params with safe defaults) without a real caller
   threading non-default values yet.
7. **`MemoryAnswer` gains an additive `messages: LlmMessage[]` field**,
   passed straight through from `result.messages` (R2's `RunAgentResult` now
   carries this field — see R2's plan, Judgment call #6 — sourced from
   `AgentLoopResult.messages`). R6's chat-session persistence reads the
   produced transcript off `answerWithMemory`'s return value via the
   R5-rewritten `/api/agent/stream` route; there is no other call-chain path
   to it. Purely additive, so existing `runId`/`status`/`answer`/`steps`/
   `invalidCitations` consumers are unaffected.

## Global Constraints

- Do not touch `src/lib/harness.ts`, `src/lib/agent-loop.ts`,
  `src/lib/tools/orchestrator.ts`, or anything under `src/lib/llm/` — those
  are R1/R2/R3-owned.
- Do not add a fixed four-section answer format anywhere. Tool-use guidance
  is free-format prose, per the locked decision.
- Memory index hard cap: **4,000 chars** (pre-overflow-notice content).
  Truncate whole lines only, never mid-line.
- `MEMORY_INSTRUCTIONS` must not exist anywhere in `src/` or `tests/` after
  this plan (Task 4's verify step greps for it).
- DB tests reset the memory tables + `runMigrations` in `beforeEach` (mirror
  the pattern already in `tests/memory-sources.test.ts` /
  `tests/memory-notes.test.ts`), inject no external API keys (unset
  `OPENAI_API_KEY` so embedding falls back to the offline hash path — copy
  the existing `beforeEach`/`afterEach` pattern verbatim).
- Run tests with `DATABASE_URL` pointing at the local Postgres:
  `postgresql://sourcecado:sourcecado@localhost:5432/sourcecado` (container
  `sourcecado-db-1`, per `docker-compose.yml`).

---

### Task 1: Memory index query — `listMemoryIndexRows`

**Files:**
- Modify: `src/lib/memory/sources.ts` (append; do not touch `listSources`/`setSourceArchived`)
- Modify: `tests/memory-sources.test.ts` (append new describe block)

**Interfaces produced:**
```ts
export interface MemoryIndexRow {
  sourceId: string;
  title: string | null;
  sourceType: string;
  updatedAt: string; // ISO 8601
}

export interface MemoryIndexRows {
  sources: MemoryIndexRow[];      // all allowed, non-archived, newest-updated first
  recentNotes: MemoryIndexRow[];  // subset where sourceType === "note", capped to 20
}

export async function listMemoryIndexRows(
  db: Sql,
  actor?: MemoryActor
): Promise<MemoryIndexRows>
```

- [x] **Step 1: Append to `src/lib/memory/sources.ts`**

Add at the end of the file (after `setSourceArchived`):

```ts
export interface MemoryIndexRow {
  sourceId: string;
  title: string | null;
  sourceType: string;
  updatedAt: string;
}

export interface MemoryIndexRows {
  sources: MemoryIndexRow[];
  recentNotes: MemoryIndexRow[];
}

// Memory-index query for R4 context assembly (src/lib/context.ts): the
// actor's permitted, non-archived sources (title/type/date), newest-updated
// first, plus the subset that are memory notes capped to the last 20. Kept
// here (not context.ts) — same DB-query-over-source_records concern as
// listSources/setSourceArchived above.
export async function listMemoryIndexRows(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<MemoryIndexRows> {
  const allowed = await resolveAllowedSourceIds(db, actor);
  if (allowed.length === 0) return { sources: [], recentNotes: [] };

  const rows = await db<
    { source_id: string; title: string | null; source_type: string; updated_at: Date }[]
  >`
    SELECT source_id, title, source_type, updated_at
    FROM source_records
    WHERE source_id = ANY(${allowed})
    ORDER BY updated_at DESC
  `;

  const sources: MemoryIndexRow[] = rows.map((r) => ({
    sourceId: r.source_id,
    title: r.title,
    sourceType: r.source_type,
    updatedAt: r.updated_at.toISOString(),
  }));
  const recentNotes = sources.filter((s) => s.sourceType === "note").slice(0, 20);

  return { sources, recentNotes };
}
```

- [x] **Step 2: Append tests to `tests/memory-sources.test.ts`**

Add a new top-level `describe` block (reuse the file's existing
`resetMemoryTables`/`bytes` helpers; add these two local helpers near the top
of the new block):

```ts
describe("listMemoryIndexRows", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) process.env.OPENAI_API_KEY = savedApiKey;
    else delete process.env.OPENAI_API_KEY;
    await closeDb();
  });

  async function insertSourceRow(
    db: ReturnType<typeof getDb>,
    opts: { sourceId: string; title: string; sourceType: string; updatedAt?: string }
  ): Promise<void> {
    await db`
      INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text, updated_at)
      VALUES (
        ${opts.sourceId}, ${"/test/" + opts.sourceId}, ${opts.title}, ${opts.sourceType},
        ${"hash-" + opts.sourceId}, '', ${opts.updatedAt ? new Date(opts.updatedAt) : new Date()}
      )
    `;
  }

  async function grantRead(db: ReturnType<typeof getDb>, sourceId: string): Promise<void> {
    await db`
      INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
      VALUES (${DEFAULT_ACTOR.actorType}, ${DEFAULT_ACTOR.actorId}, ${sourceId}, 'read')
    `;
  }

  it("returns only permitted, non-archived sources with title/type/date", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "a", title: "Alpha", sourceType: "markdown" });
    await insertSourceRow(db, { sourceId: "b", title: "Beta", sourceType: "csv" });
    await grantRead(db, "a");
    // "b" is never granted -> must not appear.

    const { sources, recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources).toHaveLength(1);
    expect(sources[0]).toMatchObject({ sourceId: "a", title: "Alpha", sourceType: "markdown" });
    expect(recentNotes).toHaveLength(0);
  });

  it("excludes archived sources", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "arch", title: "Archived", sourceType: "markdown" });
    await grantRead(db, "arch");
    await db`UPDATE source_records SET archived_at = now() WHERE source_id = 'arch'`;

    const { sources } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources).toHaveLength(0);
  });

  it("caps recentNotes to the 20 most-recently-updated note rows", async () => {
    const db = getDb();
    for (let i = 0; i < 25; i++) {
      const sourceId = `note-${i}`;
      await insertSourceRow(db, {
        sourceId,
        title: `Note ${i}`,
        sourceType: "note",
        updatedAt: new Date(Date.now() - (25 - i) * 1000).toISOString(),
      });
      await grantRead(db, sourceId);
    }

    const { recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(recentNotes).toHaveLength(20);
    // Most recently updated (note-24, inserted with the latest timestamp) is first.
    expect(recentNotes[0].sourceId).toBe("note-24");
  });

  it("returns empty lists when the actor has no permitted sources", async () => {
    const db = getDb();
    const { sources, recentNotes } = await listMemoryIndexRows(db, DEFAULT_ACTOR);
    expect(sources).toEqual([]);
    expect(recentNotes).toEqual([]);
  });
});
```

Add `listMemoryIndexRows` to the existing `import { listSources, setSourceArchived } from "@/lib/memory/sources";` line.

- [x] **Step 3: Run the new tests**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/memory-sources.test.ts`
Expected: PASS — all prior tests in this file plus the 4 new ones (7 total in the file, exact count depends on what's already there; confirm no failures).

- [x] **Step 4: Commit**

```bash
git add src/lib/memory/sources.ts tests/memory-sources.test.ts
git commit -m "feat(r4): add listMemoryIndexRows for the memory-index query"
```

---

### Task 2: `src/lib/context.ts` — sectioned system prompt + memory index

**Files:**
- Create or extend: `src/lib/context.ts` (see Judgment call #1 — read first, don't blindly overwrite)
- Create: `tests/context.test.ts`

**Interfaces produced:**
```ts
export interface SystemPromptSection { title: string; body: string }
export function buildSystemPrompt(sections: SystemPromptSection[]): string
export const IDENTITY_SECTION: SystemPromptSection
export const TOOL_USE_GUIDANCE_SECTION: SystemPromptSection
export async function buildMemoryIndexSection(db: Sql, actor?: MemoryActor): Promise<SystemPromptSection>
export async function buildMemoryAnswerInstructions(db: Sql, actor?: MemoryActor): Promise<string>
```

- [x] **Step 1: Check whether `src/lib/context.ts` already exists**

Run: `test -f src/lib/context.ts && echo EXISTS || echo ABSENT`

If `ABSENT`, write the full file below. If `EXISTS`, read it, then **add**
everything below that it's missing (by export name) without removing or
renaming anything already there.

- [x] **Step 2: Write/extend `src/lib/context.ts`**

```ts
import type { Sql } from "./tools/types";
import { DEFAULT_ACTOR, type MemoryActor } from "./memory/actor";
import { listMemoryIndexRows } from "./memory/sources";

export interface SystemPromptSection {
  title: string;
  body: string;
}

// Joins sections into one system-prompt string. Order matters — callers pass
// sections in the order they want them to appear.
export function buildSystemPrompt(sections: SystemPromptSection[]): string {
  return sections.map((s) => `## ${s.title}\n${s.body}`).join("\n\n");
}

export const IDENTITY_SECTION: SystemPromptSection = {
  title: "Identity",
  body:
    "You are a sourcing agent with access to team memory and tools. Decide when to search, when to answer directly, and when to record a finding.",
};

// Replaces the deleted MEMORY_INSTRUCTIONS four-section contract. Free-format:
// no fixed section headers, no "call search_memory every turn."
export const TOOL_USE_GUIDANCE_SECTION: SystemPromptSection = {
  title: "Tool-Use Guidance",
  body:
    "Search memory when the index below isn't enough to answer confidently — you decide when that is. Whenever you cite memory, cite inline as `sourceId#chunk-N` (or `#row-N`); never invent a citation id. If memory doesn't cover something, say so plainly instead of guessing.",
};

const MEMORY_INDEX_MAX_CHARS = 4000;

// Built once per run from a SQL query (title/date/kind for every permitted,
// non-archived source, plus the last ~20 memory notes), rendered as capped
// markdown. Truncates whole lines only, never mid-line.
export async function buildMemoryIndexSection(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<SystemPromptSection> {
  const { sources, recentNotes } = await listMemoryIndexRows(db, actor);

  const lines: string[] = [];
  if (sources.length === 0) {
    lines.push("No memory sources are indexed yet.");
  } else {
    lines.push("Sources:");
    for (const s of sources) {
      lines.push(
        `- ${s.sourceId} (${s.sourceType}, updated ${s.updatedAt.slice(0, 10)}): ${s.title ?? "(untitled)"}`
      );
    }
  }
  if (recentNotes.length > 0) {
    lines.push("", "Recent notes:");
    for (const n of recentNotes) {
      lines.push(`- ${n.sourceId} (updated ${n.updatedAt.slice(0, 10)}): ${n.title ?? "(untitled)"}`);
    }
  }

  return { title: "Memory Index", body: capMemoryIndexLines(lines) };
}

function capMemoryIndexLines(lines: string[]): string {
  let body = "";
  let shown = 0;
  for (const line of lines) {
    const candidate = body ? `${body}\n${line}` : line;
    if (candidate.length > MEMORY_INDEX_MAX_CHARS) break;
    body = candidate;
    shown++;
  }
  const omitted = lines.length - shown;
  if (omitted > 0) {
    body += `\n...(${omitted} more sources not shown)`;
  }
  return body;
}

// The memory-chat path's system-prompt composer: identity + tool-use
// guidance + the injected memory index, in that order. Callers (e.g.
// answerWithMemory) pass the returned string into runAgent()'s existing
// per-run `instructions` slot.
export async function buildMemoryAnswerInstructions(
  db: Sql,
  actor: MemoryActor = DEFAULT_ACTOR
): Promise<string> {
  const memoryIndex = await buildMemoryIndexSection(db, actor);
  return buildSystemPrompt([IDENTITY_SECTION, TOOL_USE_GUIDANCE_SECTION, memoryIndex]);
}
```

- [x] **Step 3: Write the failing tests first — `tests/context.test.ts`**

```ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import {
  buildSystemPrompt,
  buildMemoryIndexSection,
  buildMemoryAnswerInstructions,
  IDENTITY_SECTION,
  TOOL_USE_GUIDANCE_SECTION,
  type SystemPromptSection,
} from "@/lib/context";

async function resetMemoryTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS source_permissions CASCADE`;
  await db`DROP TABLE IF EXISTS extraction_runs CASCADE`;
  await db`DROP TABLE IF EXISTS semantic_facts CASCADE`;
  await db`DROP TABLE IF EXISTS memory_chunks CASCADE`;
  await db`DROP TABLE IF EXISTS source_records CASCADE`;
  await db`DROP TABLE IF EXISTS schema_migrations CASCADE`;
  await runMigrations(db);
}

async function insertSourceRow(
  db: ReturnType<typeof getDb>,
  opts: { sourceId: string; title: string }
): Promise<void> {
  await db`
    INSERT INTO source_records (source_id, path, title, source_type, content_hash, raw_text)
    VALUES (${opts.sourceId}, ${"/test/" + opts.sourceId}, ${opts.title}, 'markdown', ${"hash-" + opts.sourceId}, '')
  `;
  await db`
    INSERT INTO source_permissions (principal_type, principal_id, source_id, access)
    VALUES (${DEFAULT_ACTOR.actorType}, ${DEFAULT_ACTOR.actorId}, ${opts.sourceId}, 'read')
  `;
}

describe("buildSystemPrompt", () => {
  it("joins sections as '## Title\\nBody' separated by blank lines, in order", () => {
    const sections: SystemPromptSection[] = [
      { title: "One", body: "first" },
      { title: "Two", body: "second" },
    ];
    const prompt = buildSystemPrompt(sections);
    expect(prompt).toBe("## One\nfirst\n\n## Two\nsecond");
  });

  it("returns an empty string for an empty section list", () => {
    expect(buildSystemPrompt([])).toBe("");
  });
});

describe("fixed sections", () => {
  it("IDENTITY_SECTION and TOOL_USE_GUIDANCE_SECTION carry no fixed four-section format language", () => {
    expect(IDENTITY_SECTION.title).toBe("Identity");
    expect(TOOL_USE_GUIDANCE_SECTION.body).not.toMatch(/Answer:|Evidence:|Gaps:|Next Action:/);
    expect(TOOL_USE_GUIDANCE_SECTION.body).toMatch(/sourceId#chunk-N/);
  });
});

describe("buildMemoryIndexSection (postgres)", () => {
  let savedApiKey: string | undefined;

  beforeEach(async () => {
    savedApiKey = process.env.OPENAI_API_KEY;
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    if (savedApiKey !== undefined) process.env.OPENAI_API_KEY = savedApiKey;
    else delete process.env.OPENAI_API_KEY;
    await closeDb();
  });

  it("renders a 'No memory sources are indexed yet.' body when nothing is indexed", async () => {
    const db = getDb();
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.title).toBe("Memory Index");
    expect(section.body).toContain("No memory sources are indexed yet.");
  });

  it("lists permitted sources with title/type/date", async () => {
    const db = getDb();
    await insertSourceRow(db, { sourceId: "acme", title: "Acme" });
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.body).toContain("acme");
    expect(section.body).toContain("Acme");
    expect(section.body).toContain("markdown");
  });

  it("caps the rendered body and appends an overflow notice when too many sources are indexed", async () => {
    const db = getDb();
    const longTitle = "X".repeat(80);
    for (let i = 0; i < 150; i++) {
      await insertSourceRow(db, { sourceId: `src-${i}`, title: longTitle });
    }
    const section = await buildMemoryIndexSection(db, DEFAULT_ACTOR);
    expect(section.body).toMatch(/\.\.\.\(\d+ more sources not shown\)/);
    // Cap check: strip the overflow-notice line before measuring — the
    // capped list content itself must not exceed 4000 chars.
    const withoutNotice = section.body.replace(/\n\.\.\.\(\d+ more sources not shown\)$/, "");
    expect(withoutNotice.length).toBeLessThanOrEqual(4000);
  });
});

describe("buildMemoryAnswerInstructions (postgres)", () => {
  beforeEach(async () => {
    delete process.env.OPENAI_API_KEY;
    await resetMemoryTables();
  });

  afterEach(async () => {
    await closeDb();
  });

  it("composes identity + tool-use guidance + memory index, in that order", async () => {
    const db = getDb();
    const instructions = await buildMemoryAnswerInstructions(db, DEFAULT_ACTOR);
    const identityIdx = instructions.indexOf("## Identity");
    const guidanceIdx = instructions.indexOf("## Tool-Use Guidance");
    const indexIdx = instructions.indexOf("## Memory Index");
    expect(identityIdx).toBeGreaterThanOrEqual(0);
    expect(guidanceIdx).toBeGreaterThan(identityIdx);
    expect(indexIdx).toBeGreaterThan(guidanceIdx);
  });
});
```

- [x] **Step 4: Run the tests**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/context.test.ts`
Expected: PASS (9 tests).

- [x] **Step 5: Commit**

```bash
git add src/lib/context.ts tests/context.test.ts
git commit -m "feat(r4): sectioned system prompt + injected memory index (context.ts)"
```

---

### Task 3: Check-on-use citations, per turn

**Files:**
- Modify: `src/lib/memory/citations.ts`
- Modify: `tests/memory-answer.test.ts` (append new describe block; do not
  touch the existing `collectAllowedCitations`/`checkCitations`/agentic-flow
  blocks)

**Interfaces produced (signature change, both params additive/optional —
existing call sites with no third/second arg keep behaving as today):**
```ts
export function collectBundlesFromTrace(trace: RunTrace | null, sinceStepId?: number): MemoryBundle[]
export function verifyAnswerCitations(trace: RunTrace | null, answer: string, sinceStepId?: number): { answer: string; invalidCitations: string[] }
```

- [x] **Step 1: Edit `collectBundlesFromTrace` in `src/lib/memory/citations.ts`**

Replace:
```ts
export function collectBundlesFromTrace(trace: RunTrace | null): MemoryBundle[] {
  if (!trace) return [];
  const bundles: MemoryBundle[] = [];
  function walk(steps: RunStepTrace[]) {
    for (const step of steps) {
      for (const tc of step.toolCalls) {
        if (tc.toolName === "search_memory" && tc.status === "succeeded" && tc.result) {
          bundles.push(tc.result as MemoryBundle);
        }
      }
      walk(step.children);
    }
  }
  walk(trace.steps);
  return bundles;
}
```
with:
```ts
export function collectBundlesFromTrace(
  trace: RunTrace | null,
  sinceStepId?: number
): MemoryBundle[] {
  if (!trace) return [];
  const bundles: MemoryBundle[] = [];
  function walk(steps: RunStepTrace[]) {
    for (const step of steps) {
      // Multi-turn chat sessions (R6) nest a fresh "agent" step per turn
      // under the same run; step ids are assigned sequentially, so this
      // scopes the walk to only steps created after the given turn boundary.
      if (sinceStepId !== undefined && step.id <= sinceStepId) continue;
      for (const tc of step.toolCalls) {
        if (tc.toolName === "search_memory" && tc.status === "succeeded" && tc.result) {
          bundles.push(tc.result as MemoryBundle);
        }
      }
      walk(step.children);
    }
  }
  walk(trace.steps);
  return bundles;
}
```

- [x] **Step 2: Edit `verifyAnswerCitations`**

Replace:
```ts
export function verifyAnswerCitations(
  trace: RunTrace | null,
  answer: string
): { answer: string; invalidCitations: string[] } {
  const bundles = collectBundlesFromTrace(trace);
  const allowed = collectAllowedCitations(bundles);
  const { sanitizedAnswer, invalid } = checkCitations(answer, allowed);
  return { answer: sanitizedAnswer, invalidCitations: invalid };
}
```
with:
```ts
export function verifyAnswerCitations(
  trace: RunTrace | null,
  answer: string,
  sinceStepId?: number
): { answer: string; invalidCitations: string[] } {
  const bundles = collectBundlesFromTrace(trace, sinceStepId);
  // Check-on-use: only validate/strip citations on turns where
  // search_memory was actually called in-scope. No bundles -> nothing to
  // validate against, nothing to strip (leave the answer untouched).
  if (bundles.length === 0) {
    return { answer, invalidCitations: [] };
  }
  const allowed = collectAllowedCitations(bundles);
  const { sanitizedAnswer, invalid } = checkCitations(answer, allowed);
  return { answer: sanitizedAnswer, invalidCitations: invalid };
}
```

Also update the doc comment directly above `verifyAnswerCitations` (currently
"Full citation post-check pipeline...") to note it runs check-on-use, scoped
to `sinceStepId` when given.

- [x] **Step 3: Append tests to `tests/memory-answer.test.ts`**

These are pure unit tests over hand-built `RunTrace`-shaped objects — no DB
needed. Add near the existing `checkCitations` describe block (import
`verifyAnswerCitations` alongside the existing citations imports at the top
of the file):

```ts
describe("verifyAnswerCitations — check-on-use per turn", () => {
  function fakeTrace(steps: Array<{ id: number; bundle?: MemoryBundle }>): import("@/lib/ledger").RunTrace {
    return {
      id: 1,
      runType: "agent_chat",
      status: "succeeded",
      title: null,
      input: null,
      output: null,
      metadata: null,
      errorType: null,
      errorMessage: null,
      error: null,
      startedAt: new Date(),
      completedAt: null,
      createdAt: new Date(),
      steps: steps.map((s) => ({
        id: s.id,
        runId: 1,
        parentStepId: null,
        stepKind: "tool",
        name: "search_memory",
        status: "succeeded",
        input: null,
        output: null,
        metadata: null,
        errorType: null,
        errorMessage: null,
        error: null,
        startedAt: new Date(),
        completedAt: null,
        createdAt: new Date(),
        children: [],
        modelCalls: [],
        toolCalls: s.bundle
          ? [
              {
                id: s.id,
                runId: 1,
                runStepId: s.id,
                toolName: "search_memory",
                status: "succeeded",
                arguments: null,
                result: s.bundle,
                metadata: null,
                errorType: null,
                errorMessage: null,
                startedAt: new Date(),
                completedAt: null,
                createdAt: new Date(),
              },
            ]
          : [],
      })),
    } as unknown as import("@/lib/ledger").RunTrace;
  }

  const bundleWithCitation = (citation: string): MemoryBundle => ({
    intent: "generic",
    acceptedFacts: [],
    gapFacts: [],
    chunks: [{ text: "t", citation, score: 0.9 }],
  });

  it("skips the check (answer untouched) when no search_memory call is in scope", () => {
    const trace = fakeTrace([]); // no steps at all this turn
    const answer = "Made-up cite ghost#chunk-1 with nothing to validate against.";
    const { answer: result, invalidCitations } = verifyAnswerCitations(trace, answer);
    expect(result).toBe(answer); // unchanged, not stripped
    expect(invalidCitations).toHaveLength(0);
  });

  it("runs the check when a search_memory call is in scope, stripping invented citations", () => {
    const trace = fakeTrace([{ id: 1, bundle: bundleWithCitation("real#chunk-1") }]);
    const answer = "See real#chunk-1 and ghost#chunk-9.";
    const { answer: result, invalidCitations } = verifyAnswerCitations(trace, answer);
    expect(result).toContain("real#chunk-1");
    expect(result).toContain("[unverified citation removed]");
    expect(invalidCitations).toContain("ghost#chunk-9");
  });

  it("sinceStepId excludes an earlier turn's search_memory bundle from this turn's allow-list", () => {
    const trace = fakeTrace([
      { id: 1, bundle: bundleWithCitation("turn1#chunk-1") }, // earlier turn
      { id: 5, bundle: bundleWithCitation("turn2#chunk-1") }, // this turn
    ]);
    const answer = "turn1#chunk-1 turn2#chunk-1";
    const { invalidCitations } = verifyAnswerCitations(trace, answer, 3);
    // turn1's citation is out of scope (step id 1 <= sinceStepId 3) -> invalid.
    expect(invalidCitations).toContain("turn1#chunk-1");
    expect(invalidCitations).not.toContain("turn2#chunk-1");
  });

  it("returns unchanged answer for a null trace", () => {
    const { answer, invalidCitations } = verifyAnswerCitations(null, "no trace here");
    expect(answer).toBe("no trace here");
    expect(invalidCitations).toHaveLength(0);
  });
});
```

Add `verifyAnswerCitations` to the existing citations import line at the top
of `tests/memory-answer.test.ts`.

- [x] **Step 4: Run the tests**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/memory-answer.test.ts`
Expected: PASS — all existing tests in this file plus the 4 new ones.

- [x] **Step 5: Commit**

```bash
git add src/lib/memory/citations.ts tests/memory-answer.test.ts
git commit -m "feat(r4): check-on-use citation post-check, scoped per turn"
```

---

### Task 4: Delete `MEMORY_INSTRUCTIONS`, wire the memory-chat path

**Files:**
- Modify: `src/lib/memory/answer-config.ts` (delete `MEMORY_INSTRUCTIONS`; keep `memoryRegistry`)
- Modify: `src/lib/memory/answer.ts` (call `buildMemoryAnswerInstructions` instead)
- Modify: whichever test file(s) still reference `MEMORY_INSTRUCTIONS` (discovered via grep — see Step 1)
- Modify: `tests/agent-route.test.ts`, `tests/agent-route-history.test.ts`,
  `tests/agent-stream-route.test.ts` (add a `@/lib/context` mock — see Step 4a;
  none of these reference `MEMORY_INSTRUCTIONS`, so the Step 1 grep won't
  surface them, but they call the real, now-DB-hitting `answerWithMemory`
  against a mocked `getDb() => {}` and will 500 without this mock)

- [x] **Step 1: Discover every remaining reference**

Run: `grep -rn "MEMORY_INSTRUCTIONS" src tests`
Expected output at this point: `src/lib/memory/answer-config.ts` (the
definition), `src/lib/memory/answer.ts` (the one production import/use), and
zero or more test files (depends on what R2's `harness.ts` rewrite already
cleaned up — R2 deletes `buildUserPrompt`, which shares a test file with the
`MEMORY_INSTRUCTIONS` block in `tests/harness-multiturn.test.ts` today).

- [x] **Step 2: Delete `MEMORY_INSTRUCTIONS` from `src/lib/memory/answer-config.ts`**

Replace the whole file with:
```ts
import { createToolRegistry } from "../tools/registry";
import type { ToolRegistry } from "../tools/registry";
import { searchMemoryTool } from "../tools/search-memory";

export function memoryRegistry(): ToolRegistry {
  return createToolRegistry([searchMemoryTool]);
}
```

- [x] **Step 3: Wire `src/lib/memory/answer.ts` to the new composer**

First read `src/lib/harness.ts`'s current `RunAgentInput` interface to confirm
the per-run instructions field name (expected: `instructions?: string`,
per the brief — if R2 renamed it, use the actual name here instead).

Change the imports at the top of `src/lib/memory/answer.ts` from:
```ts
import { memoryRegistry, MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";
```
to:
```ts
import { memoryRegistry } from "@/lib/memory/answer-config";
import { buildMemoryAnswerInstructions } from "@/lib/context";
```

Change the body of `answerWithMemory` from:
```ts
export async function answerWithMemory(db: Sql, input: AnswerWithMemoryInput): Promise<MemoryAnswer> {
  const registry = memoryRegistry();
  const result = await runAgent({
    question: input.question,
    history: input.history,
    registry,
    allowedClasses: new Set(["read"]),
    instructions: MEMORY_INSTRUCTIONS,
    db,
    onStep: input.onStep,
  });
```
to:
```ts
export async function answerWithMemory(db: Sql, input: AnswerWithMemoryInput): Promise<MemoryAnswer> {
  const registry = memoryRegistry();
  const instructions = await buildMemoryAnswerInstructions(db);
  const result = await runAgent({
    question: input.question,
    history: input.history,
    registry,
    allowedClasses: new Set(["read"]),
    instructions,
    db,
    onStep: input.onStep,
  });
```

Also add the additive `messages: LlmMessage[]` field to the `MemoryAnswer`
interface and its return object (`return { ..., messages: result.messages };`)
per Judgment call #7 above.

Do not change anything else in this file (the citation-post-check call
`verifyAnswerCitations(trace, answer)` stays as-is — no `sinceStepId` yet,
per Judgment call #6).

- [x] **Step 4: Clean up any surviving test references**

For each test file the Step 1 grep still found (besides the two source
files just edited): open it, delete only the `describe`/`it` block(s) that
assert on `MEMORY_INSTRUCTIONS` content and the now-unused import line. Do
not touch unrelated tests in the same file (e.g. `buildUserPrompt` tests in
`tests/harness-multiturn.test.ts` are R2's concern — leave them exactly as
R2 left them).

- [x] **Step 4a: Mock `@/lib/context` in the three route test files that call the real `answerWithMemory`**

`tests/agent-route.test.ts`, `tests/agent-route-history.test.ts`, and
`tests/agent-stream-route.test.ts` each mock `@/lib/harness` (`runAgent`) and
`@/lib/db` (`getDb` returning a bare `{}`) but leave `answerWithMemory` itself
real. After this task's Step 3, `answerWithMemory` calls the real
`buildMemoryAnswerInstructions(db)` before it ever reaches the mocked
`runAgent` — and that call chain runs `` db`SELECT ...` `` as a tagged
template against the non-callable `{}` mock, throwing `TypeError: db is not
a function` and turning all 12 tests in these three files into an unrelated
500. Add to each file's existing mock block:
```ts
vi.mock("@/lib/context", () => ({
  buildMemoryAnswerInstructions: vi.fn().mockResolvedValue("stub instructions"),
}));
```

- [x] **Step 5: Verify no reference remains**

Run: `grep -rn "MEMORY_INSTRUCTIONS" src tests`
Expected: no output (zero matches).

- [x] **Step 6: Run the affected test files**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/memory-answer.test.ts tests/context.test.ts tests/memory-sources.test.ts tests/agent-route.test.ts tests/agent-route-history.test.ts tests/agent-stream-route.test.ts`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/lib/memory/answer-config.ts src/lib/memory/answer.ts \
  tests/agent-route.test.ts tests/agent-route-history.test.ts tests/agent-stream-route.test.ts
git commit -m "feat(r4): delete MEMORY_INSTRUCTIONS, wire memory-chat path to context.ts"
```

(If Step 4 touched additional test files, `git add` those too before
committing, or split into a second `test:` commit — either is fine.)

---

### Task 5: Full verification

- [x] **Step 1: Run the full test suite**

Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run`
Expected: PASS — every existing suite plus the new/modified files from
Tasks 1–4, all green.

- [x] **Step 2: Lint**

Run: `npm run lint`
Expected: `✔ No ESLint warnings or errors`.

- [x] **Step 3: Build**

Run: `npm run build`
Expected: build succeeds (confirms no stale import of `MEMORY_INSTRUCTIONS`
anywhere, including any app route file not covered by the grep in Task 4).

- [x] **Step 4: Cleanup-only pass over this slice's own additions**

Re-read `src/lib/context.ts`, `src/lib/memory/sources.ts`'s new function, and
the `citations.ts` diff. Confirm: no orphaned imports, no leftover
`MEMORY_INSTRUCTIONS`-shaped comments, no speculative parameters beyond what
this plan specified. Fix anything found; do not refactor unrelated code in
the same files.

- [x] **Step 5: Commit** (only if Step 4 produced changes)

```bash
git add -A
git commit -m "chore(r4): cleanup pass on context/memory changes"
```

---

## Tests

| File | New tests |
|---|---|
| `tests/memory-sources.test.ts` | `listMemoryIndexRows`: permitted/non-archived filtering, archived exclusion, 20-note cap + ordering, empty-actor case (4) |
| `tests/context.test.ts` (new) | `buildSystemPrompt` join format + empty list; fixed-section content (no four-section language); `buildMemoryIndexSection` empty/populated/overflow-cap; `buildMemoryAnswerInstructions` section ordering (7) |
| `tests/memory-answer.test.ts` | `verifyAnswerCitations` check-on-use skip, runs-when-in-scope, `sinceStepId` turn-scoping, null-trace passthrough (4) |

Total: ~15 new tests, within the spec's Testing Plan estimate for the
"Context" row (+4–6) plus incidental coverage folded into existing files.

## Self-Review

**Spec/brief coverage:**
- Sectioned system prompt (`buildSystemPrompt`) → Task 2.
- Injected, capped memory index (`buildMemoryIndexSection` /
  `listMemoryIndexRows`) → Tasks 1–2.
- Free-format tool-use guidance replacing the four-section contract → Task 2
  (`TOOL_USE_GUIDANCE_SECTION`).
- `MEMORY_INSTRUCTIONS` deleted → Task 4.
- Check-on-use citations, per turn (`sinceStepId`) → Task 3.
- Acceptance Criterion #1 (agent skips `search_memory` when the index
  suffices, calls it with valid citations when depth is needed) — this slice
  builds the machinery (index injection + check-on-use gate); the *observed
  live* behavior in AC #1 is validated in R9's e2e proof, not here.

**Placeholder scan:** no TBD/TODO; every code and test step has full content.

**Type consistency:** `SystemPromptSection`, `buildSystemPrompt`,
`buildMemoryIndexSection`, `buildMemoryAnswerInstructions`,
`MemoryIndexRow`/`MemoryIndexRows`/`listMemoryIndexRows`, and the
`sinceStepId?: number` addition to `collectBundlesFromTrace`/
`verifyAnswerCitations` are used identically across Tasks 1–4 and their
tests.

## Eng Review (2026-07-14)

**Method:** Read the plan and `docs/superpowers/plans/2026-07-14-r-contracts-brief.md`
in full, then grounded every claim against the actual pre-R0 repo state on
`feat/a-chat-streaming`: `src/lib/memory/sources.ts`, `permissions.ts`,
`citations.ts`, `answer.ts`, `answer-config.ts`, `actor.ts`,
`src/lib/harness.ts`, `src/lib/ledger.ts` (`RunStepRecord`/`ToolCallRecord`/
`RunTrace`), `src/migrations/002_memory.sql`/`003_archived.sql`, `vitest.config.ts`,
and every test file this plan touches or that touches `answerWithMemory`
(`tests/agent-route.test.ts`, `tests/agent-route-history.test.ts`,
`tests/agent-stream-route.test.ts`, `tests/harness-multiturn.test.ts`,
`tests/memory-answer.test.ts`). Cross-checked the R2 plan
(`2026-07-14-r2-agent-loop-plan.md`) for the two things R4 depends on it for:
`RunAgentInput.instructions` surviving unchanged, and `RunAgentResult` gaining
`messages: LlmMessage[]`. Ran `tests/agent-route.test.ts` and
`tests/agent-stream-route.test.ts` against current `main`-equivalent state to
confirm their present pass/fail baseline before reasoning about the plan's
effect on them.

**What checks out:** The plan's "Context" section accurately describes the
current repo (verified line-for-line against `answer.ts`/`answer-config.ts`/
`citations.ts`/`sources.ts`). `resolveAllowedSourceIds`'s archived-exclusion
default (verified in `permissions.ts`) makes Task 1's `listMemoryIndexRows`
query correct without an extra `archived_at IS NULL` clause — the plan gets
this right by relying on the existing chokepoint rather than duplicating the
filter. Task 1/2/3's test fixtures match the real `source_records` schema and
`RunTrace`/`RunStepTrace`/`ToolCallRecord` shapes exactly (checked every field
name and type against `002_memory.sql` and `ledger.ts`). The `sinceStepId`
filtering logic in Task 3 is correct for the two hand-built test cases and
matches brief §5 verbatim. File ownership (Task 1 → `sources.ts`, Task 3 →
`citations.ts`, `memoryRegistry()` staying in `answer-config.ts`) all conform
to brief §7 and its "open items" list, with each deviation/judgment call
explicitly stated per the brief's own requirement. The Global Constraint grep
verify step (Task 4 Step 5) is a real, mechanical check, not a vibes-based one.

**MUST-FIX — RESOLVED**

1. **RESOLVED.** Task 4's wiring change breaks 12 currently-passing tests in three files
   the plan never touches, and its own verify steps won't catch it.**
   `tests/agent-route.test.ts` (6 tests), `tests/agent-route-history.test.ts`
   (3 tests), and `tests/agent-stream-route.test.ts` (3 tests) all do:
   ```ts
   vi.mock("@/lib/harness", () => ({ runAgent: runAgentMock }));
   vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));
   ```
   `answerWithMemory` itself is real (unmocked) in these tests. After Task 4,
   `answerWithMemory` calls the real `buildMemoryAnswerInstructions(db)`
   *before* calling the mocked `runAgent`. That call chain
   (`buildMemoryAnswerInstructions` → `buildMemoryIndexSection` →
   `listMemoryIndexRows` → `resolveAllowedSourceIds`) executes
   `` db`SELECT sp.source_id ...` `` as a tagged-template call. Since the
   mocked `db` is a bare `{}`, not callable, this throws `TypeError: db is
   not a function` — caught by the route handler's own `catch`, turning
   every one of these 12 tests' expected 200/mocked-500 into an unrelated
   500 with the wrong error message. I confirmed all 9 tests in
   `agent-route.test.ts`/`agent-stream-route.test.ts` pass today (ran them
   directly), and confirmed both mock `getDb` the same way (checked
   `agent-route-history.test.ts` too — same pattern).

   Task 4's Step 1 discovery (`grep -rn "MEMORY_INSTRUCTIONS" src tests`)
   will not surface this — none of these three files reference that
   constant. Task 4's own Step 6 verify list (`tests/memory-answer.test.ts
   tests/context.test.ts tests/memory-sources.test.ts`) also excludes them,
   so Task 4 will self-report green while it has actually broken three other
   files. The break only surfaces at Task 5 Step 1 (full suite), by which
   point it reads as a confusing, unrelated failure with no guidance in the
   plan for what caused it.

   **Fix — applied:** the three files are now in Task 4's "Files" list, a new
   Step 4a adds the `vi.mock("@/lib/context", ...)` stub to each alongside
   their existing `@/lib/db`/`@/lib/harness`/`@/lib/ledger` mocks, and Task
   4's Step 6 verify command now runs all six affected files (the three from
   this task plus the original three), so Task 4's own pass/fail signal is
   trustworthy instead of only Task 5's full-suite run catching it.

**Notes (not blocking, worth a look)**

- **No test exercises `answerWithMemory`'s actual wiring.** Even after the
  must-fix mocks above make the route tests pass again, nothing asserts that
  `runAgent` is actually called with the `buildMemoryAnswerInstructions`
  output as `instructions` — the mock just needs to resolve to *something*
  callable. `answerWithMemory` has zero direct test coverage today (verified:
  `grep -rln "answerWithMemory" tests/ src/` only shows the two route files
  and `answer.ts` itself) and this plan doesn't add any. Cheap addition: one
  assertion in the updated route tests (`expect(runAgentMock.mock.calls[0][0].instructions).toBe("stub instructions")`) closes this for close to free.
- **`capMemoryIndexLines` can zero out the whole index on one pathological
  line.** The loop `break`s on the first line whose addition would exceed
  4,000 chars — it doesn't skip that line and keep trying shorter ones after
  it. A single source with an ~4,000+ char title (user-controlled, e.g. a
  pasted email subject) would empty the entire memory index for that run,
  including otherwise-short entries that would fit. This matches the letter
  of "truncate whole lines only, never mid-line" but not obviously the
  intent. Not tested (Task 2's overflow test uses 150 uniform 80-char-title
  rows, never one huge line). Low priority — flag for a follow-up test, not
  a blocker for this slice.
- **`sinceStepId` boundary semantics have no real caller yet (correctly
  deferred to R6 per Judgment call #6), but the off-by-one convention should
  be nailed down before R6 wires it.** `step.id <= sinceStepId` is exclusive
  of the boundary step. If R6 passes "the last step id of the previous turn"
  as `sinceStepId`, this is correct as long as every step in the *current*
  turn (including its own root "agent" step) gets an id strictly greater
  than that boundary — true only if ids are assigned in creation order
  across the whole run, which the code comment asserts but nothing in this
  plan tests end-to-end (Task 3's tests use ids picked to avoid the boundary
  case entirely: 1/5 with a cut at 3, never testing `sinceStepId` equal to a
  turn's own root id). Worth a boundary-exact test when R6 adds the real
  caller; not blocking for R4.
- **Minor:** Task 4's instructions to add `messages: LlmMessage[]` to
  `MemoryAnswer` don't spell out the new `import type { LlmMessage } from
  "@/lib/llm/types"` line `answer.ts` will need. Trivial, but worth stating
  explicitly since the plan is otherwise exhaustive about import lines
  elsewhere (e.g. Task 4 Step 3's `buildMemoryAnswerInstructions` import).

**Sequencing/rollback:** Standard for this sprint's slice structure — R4
correctly refuses to touch anything under R2's ownership, defensively
re-reads `RunAgentInput` before relying on the `instructions` field name
(Task 4 Step 3), and greps fresh for `MEMORY_INSTRUCTIONS` rather than
assuming R2 already cleaned up `tests/harness-multiturn.test.ts` (verified:
R2's own plan explicitly leaves that file's `MEMORY_INSTRUCTIONS` describe
block untouched, confirming R4's Context section is accurate on this point).
No rollback concerns beyond the standard "this diffs against R0–R2's landed
state, not current `main`" caveat the plan already states up front.

**Test coverage:** ~15 new/modified tests are well-targeted at the new
surface (Task 1 index query, Task 2 prompt assembly, Task 3 check-on-use).
The gap is entirely on the *integration* side — Task 4 changes
`answerWithMemory`'s only call site behavior but the must-fix above is the
only thing standing between "tests pass" and "tests silently break."

| Area | Status |
|---|---|
| Architecture / file ownership vs. brief §5, §7 | Conforms |
| Global Constraints (4,000-char cap, no four-section format, grep verify) | Conforms |
| Judgment calls (7) | All grounded, correctly scoped, no relitigation needed |
| Hidden regression in route tests (Task 4) | **Found — resolved above** |
| Test coverage for new surface (Tasks 1–3) | Sufficient |
| Test coverage for integration wiring (Task 4) | Gap — see notes |
| Sequencing vs. R0–R2 | Sound, defensively written |

**VERDICT: approve (revised)** — the one concrete, verified regression (12
tests across 3 files) that Task 4 as written would have introduced is now
fixed in-plan: three `vi.mock` additions (new Step 4a) plus updated Task 4
file list/verify command. Everything else in the plan is solid and grounded
in the real codebase.

NO UNRESOLVED DECISIONS
