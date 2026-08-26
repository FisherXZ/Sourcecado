# J3 — `ask_user`, Scoped Standing Grants, and Real Gmail Drafts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent can suspend a run to ask the signed-in director a question, gate every `draft`/`admin` tool call behind an approval the director can turn into a standing per-target grant, and use that gate on a real Gmail draft tool.

**Architecture:** One suspend mechanism, two question kinds. A tool that needs a human either throws `ToolSuspend` (`ask_user`) or trips the grant gate in `executeTool` (`draft`/`admin` classes). Either way `executeTool` returns a `suspend` request instead of a tool result; `runAgentLoop` stops with `status: "suspended"`, leaving the assistant message's `tool_use` blocks deliberately unanswered; the route persists a `pending_questions` row, streams a `data-question` part, and closes. The director's answer arrives at `POST /api/agent/answer`, which mints a grant if asked, completes the deferred `tool_result` message, and re-enters the loop as a new run.

**Tech Stack:** Next.js 15 (App Router), Auth.js v5 (JWT strategy, no adapter), `postgres` (raw SQL migrations), Zod v4, Vitest, Tailwind v4 + `src/components/ui` per DESIGN.md. Gmail via `fetch` against the REST API — **no `googleapis` dependency** (`tests/package-deps.test.ts` enforces the dependency list).

## Global Constraints

- **Branch/worktree:** `feat/j3-approvals` off `main`, in `../sc-j3`, database `sourcecado_j3`. Setup commands are in Task 0.
- **Migrations are append-only and numbered.** `main` ends at `007_users_auth.sql`. This plan adds `008_approvals.sql` and `009_oauth_tokens.sql`. Never edit an existing migration.
- **No new runtime dependencies.** `tests/package-deps.test.ts` asserts the exact dependency set; adding one fails that suite. Gmail uses `fetch`, crypto uses `node:crypto`.
- **Actor is required, never defaulted.** `ToolContext.actor`, `RunAgentInput.actor`, `AnswerWithMemoryInput.actor` are required by H1 so a forgotten actor is a compile error. Everything added here follows that rule; `actorId` for a signed-in director is `String(users.id)`.
- **Fail closed.** A `draft`/`admin` tool with no declared `targetArg`, or an empty target, is denied — never silently allowed. "Yes, always" on anything `grantEntries()` rejects degrades to a one-shot yes; it never creates a broader grant.
- **One open question per session**, enforced by a partial unique index, not by application code.
- **Grant format is openworker's:** `"tool target"` — one tool, one space, one exact target. Never a bare tool name (that is the blanket permission this design exists to avoid).
- **UI follows DESIGN.md.** Reuse `src/components/ui` (`Button`, `Card`, `Input`); accent `bg-accent`/`text-accent-deep`, error `bg-neg-bg`/`text-neg-tx`, radius `rounded-[8px]`, body text `text-[13px]`. No new colors.
- **Tests:** `npm test` (Vitest, single worker, shared Postgres). DB suites reset their own tables and call `runMigrations`. Every task ends green.
- **Commit per task**, message prefix `feat(J3):` / `test(J3):` / `docs(J3):`.

## File Structure

New code clusters into three directories, split by responsibility rather than by layer:

| File | Responsibility |
|---|---|
| `src/lib/approvals/suspend.ts` | The `ToolSuspend` signal + `SuspendRequest` shape. Standalone so tools can raise it without importing the orchestrator. |
| `src/lib/approvals/grants.ts` | Grant format, the fail-closed validator, and grant storage. |
| `src/lib/approvals/pending.ts` | The suspended-run record: create, read, resolve, cancel. |
| `src/lib/approvals/resume.ts` | Turning an answer back into a valid transcript: `cancelStalePending`, `resolveAndBuildResumeMessage`. |
| `src/lib/approvals/view.ts` | The one wire shape shared by SSE, page loader and client. |
| `src/lib/auth/crypto.ts` | AES-256-GCM for secrets at rest. Nothing OAuth-specific. |
| `src/lib/auth/tokens.ts` | Google token persistence. |
| `src/lib/gmail/client.ts` | Token refresh, RFC-2822 assembly, the drafts call. |
| `src/lib/tools/ask-user.ts`, `src/lib/tools/draft-email.ts` | The two new tools. |
| `src/app/api/agent/answer/route.ts` | The answer half of the suspend/resume pair. |
| `src/app/api/grants/`, `src/app/api/grants/[id]/` | List and revoke. |
| `src/app/chat/AskCard.tsx` | The inline card, both shapes. |

Modified files stay within their existing responsibility: `orchestrator.ts` gains the gate (it is already the choke point), `agent-loop.ts` gains the suspend return, `harness.ts`/`answer.ts` gain pass-through fields, `stream.ts`/`ChatClient.tsx` gain one part type and one handler.

---

### Task 0: Worktree, database, baseline green

**Files:** none created — environment only.

**Interfaces:**
- Consumes: nothing.
- Produces: a working tree at `../sc-j3` on `feat/j3-approvals`, `DATABASE_URL` pointing at `sourcecado_j3`, a green baseline.

- [ ] **Step 1: Create the worktree and install**

```bash
cd /Users/fisher/Documents/GitHub2026/Sourcecado
git fetch origin
git worktree add ../sc-j3 -b feat/j3-approvals origin/main
cd ../sc-j3
npm install
```

- [ ] **Step 2: Create the isolated database**

```bash
docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
  psql -U sourcecado -d postgres -c 'CREATE DATABASE sourcecado_j3;'
docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
  psql -U sourcecado -d sourcecado_j3 -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

- [ ] **Step 3: Point env at it**

```bash
cp ../Sourcecado/.env.local .env.local
```

Then edit `.env.local` so `DATABASE_URL` and `POSTGRES_DB` both name `sourcecado_j3`. Do not print the file; check only that the names changed:

```bash
grep -c 'sourcecado_j3' .env.local   # expect 2
```

- [ ] **Step 4: Migrate and verify baseline green**

```bash
set -a; source .env.local; set +a
npm run migrate
npm test 2>&1 | tail -5
```

Expected: migrations `000`–`007` applied; test suite passes (480 tests as of `c73713c`). **If the baseline is red, stop and report — do not start Task 1 on a red tree.**

- [ ] **Step 5: Commit nothing, record the baseline**

No commit. Note the passing test count; every later task compares against it.

---

### Task 1: Migration 008 — `grants` and `pending_questions`

**Files:**
- Create: `src/migrations/008_approvals.sql`
- Test: `tests/approvals-migrate.test.ts`

**Interfaces:**
- Consumes: `chat_sessions(id)` from `005_chat_sessions.sql`.
- Produces: tables `grants` and `pending_questions`; the partial unique index `pending_questions_one_open_per_session`; the unique index `grants_unique`. Tasks 2 and 3 read and write these.

- [ ] **Step 1: Write the failing test**

Create `tests/approvals-migrate.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetApprovalTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await runMigrations(db);
}

describe("008_approvals migration", () => {
  beforeEach(async () => {
    await resetApprovalTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("creates both tables", async () => {
    const db = getDb();
    const rows = await db<{ table_name: string }[]>`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name IN ('grants', 'pending_questions')
      ORDER BY table_name
    `;
    expect(rows.map((r) => r.table_name)).toEqual(["grants", "pending_questions"]);
  });

  it("allows only one open pending question per session", async () => {
    const db = getDb();
    const [session] = await db<{ id: number }[]>`
      INSERT INTO chat_sessions (actor_type, actor_id) VALUES ('test_client', 'mig-test') RETURNING id
    `;
    const insert = (status: string) => db`
      INSERT INTO pending_questions
        (session_id, actor_type, actor_id, kind, tool_use_id, tool_name, question, status)
      VALUES (${session.id}, 'test_client', 'mig-test', 'question', 'tu_1', 'ask_user', 'why?', ${status})
    `;
    await insert("open");
    await expect(insert("open")).rejects.toThrow();
    await expect(insert("cancelled")).resolves.toBeDefined();
  });

  it("rejects a duplicate grant for the same scope, tool and target", async () => {
    const db = getDb();
    const insert = () => db`
      INSERT INTO grants (actor_type, actor_id, scope_kind, scope_id, tool, target)
      VALUES ('user', '1', 'session', '9', 'draft_email', 'sarah@acme.com')
    `;
    await insert();
    await expect(insert()).rejects.toThrow();
  });

  it("is idempotent", async () => {
    const db = getDb();
    await expect(runMigrations(db)).resolves.toBeDefined();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/approvals-migrate.test.ts`
Expected: FAIL — `relation "grants" does not exist`.

- [ ] **Step 3: Write the migration**

Create `src/migrations/008_approvals.sql`:

```sql
-- 008_approvals.sql — J3 human-in-the-loop: suspended runs and standing grants.
--
-- pending_questions holds a run that stopped mid-flight waiting on a director.
-- The suspended tool_use block is deliberately left without a tool_result in
-- chat_messages while the row is open; POST /api/agent/answer writes the
-- completed tool_result message and the transcript becomes provider-valid
-- again. insert_index is where the resumed result slots back into that
-- message's block array (results for blocks before the suspend are in
-- partial_results_json, blocks after it are recorded there as not_executed).
--
-- grants is the openworker "tool target" model: one tool, one exact target,
-- never a bare tool name. scope_kind is 'session' today; E1's routine model
-- becomes 'routine' with no schema change, which is why the owner is
-- (scope_kind, scope_id) rather than a session_id FK.

CREATE TABLE IF NOT EXISTS grants (
  id          BIGSERIAL PRIMARY KEY,
  actor_type  TEXT NOT NULL,
  actor_id    TEXT NOT NULL,
  scope_kind  TEXT NOT NULL CHECK (scope_kind IN ('session', 'routine')),
  scope_id    TEXT NOT NULL,
  tool        TEXT NOT NULL,
  target      TEXT NOT NULL CHECK (target <> ''),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS grants_unique
  ON grants(actor_type, actor_id, scope_kind, scope_id, tool, target);
CREATE INDEX IF NOT EXISTS grants_actor_idx ON grants(actor_type, actor_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pending_questions (
  id                    BIGSERIAL PRIMARY KEY,
  session_id            BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  run_id                BIGINT,
  actor_type            TEXT NOT NULL,
  actor_id              TEXT NOT NULL,
  kind                  TEXT NOT NULL CHECK (kind IN ('question', 'approval')),
  tool_use_id           TEXT NOT NULL,
  tool_name             TEXT NOT NULL,
  tool_args_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
  question              TEXT NOT NULL,
  header                TEXT,
  options_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
  allow_text            BOOLEAN NOT NULL DEFAULT TRUE,
  multi                 BOOLEAN NOT NULL DEFAULT FALSE,
  target                TEXT,
  insert_index          INTEGER NOT NULL DEFAULT 0,
  partial_results_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
  status                TEXT NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'answered', 'cancelled')),
  answer                TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at           TIMESTAMPTZ
);

-- The invariant lives in the database, not in application code: a session can
-- have at most one unanswered question, so a second suspend cannot orphan the
-- first (which would wedge the transcript with two dangling tool_use blocks).
CREATE UNIQUE INDEX IF NOT EXISTS pending_questions_one_open_per_session
  ON pending_questions(session_id) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS pending_questions_actor_idx
  ON pending_questions(actor_type, actor_id, created_at DESC);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run tests/approvals-migrate.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/migrations/008_approvals.sql tests/approvals-migrate.test.ts
git commit -m "feat(J3): add grants and pending_questions tables"
```

---

### Task 2: Grants data layer

**Files:**
- Create: `src/lib/approvals/grants.ts`
- Test: `tests/approvals-grants.test.ts`

**Interfaces:**
- Consumes: `grants` table (Task 1); `MemoryActor` from `src/lib/memory/actor.ts`; `Sql` from `src/lib/tools/types.ts`.
- Produces:
  - `interface GrantScope { actor: MemoryActor; scopeKind: "session" | "routine"; scopeId: string }`
  - `interface Grant { id: number; tool: string; target: string; scopeKind: string; scopeId: string; createdAt: string }`
  - `interface ProposedGrant { tool: string; target: string; access: string }`
  - `grantEntry(tool: string, target?: string | null): string`
  - `grantParts(entry: string): { tool: string; target: string | null }`
  - `grantEntries(proposed: unknown, targetArgFor: (tool: string) => string | null): string[]`
  - `hasGrant(db: Sql, scope: GrantScope, tool: string, target: string): Promise<boolean>`
  - `insertGrant(db: Sql, scope: GrantScope, tool: string, target: string): Promise<void>`
  - `listGrants(db: Sql, actor: MemoryActor): Promise<Grant[]>`
  - `revokeGrant(db: Sql, actor: MemoryActor, id: number): Promise<boolean>`

  Task 4 calls `hasGrant`. Task 9 calls `grantEntries` + `insertGrant`. Task 17 calls `listGrants` + `revokeGrant`.

- [ ] **Step 1: Write the failing test**

Create `tests/approvals-grants.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import type { MemoryActor } from "@/lib/memory/actor";
import {
  grantEntries,
  grantEntry,
  grantParts,
  hasGrant,
  insertGrant,
  listGrants,
  revokeGrant,
  type GrantScope,
} from "@/lib/approvals/grants";

const ACTOR: MemoryActor = { actorType: "user", actorId: "grants-test-1" };
const OTHER: MemoryActor = { actorType: "user", actorId: "grants-test-2" };
const SCOPE: GrantScope = { actor: ACTOR, scopeKind: "session", scopeId: "77" };

// draft_email declares `to`; add_memory_note declares nothing (write_internal,
// no external target) — the same shape target_arg_for has in openworker.
const targetArgFor = (tool: string) => (tool === "draft_email" ? "to" : null);

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await runMigrations(db);
}

describe("grant entry format", () => {
  it("joins tool and target with one space", () => {
    expect(grantEntry("draft_email", "sarah@acme.com")).toBe("draft_email sarah@acme.com");
  });

  it("splits on the first space only, so targets may contain spaces", () => {
    expect(grantParts("draft_email Sarah Chen")).toEqual({ tool: "draft_email", target: "Sarah Chen" });
  });

  it("reads a bare tool name as a null target", () => {
    expect(grantParts("draft_email")).toEqual({ tool: "draft_email", target: null });
  });
});

describe("grantEntries validation (fail-closed)", () => {
  it("accepts a write item on a tool that declares a target", () => {
    const entries = grantEntries(
      [{ tool: "draft_email", target: "sarah@acme.com", access: "write" }],
      targetArgFor
    );
    expect(entries).toEqual(["draft_email sarah@acme.com"]);
  });

  it("drops read items — reads are disclosure-only, never stored", () => {
    expect(grantEntries([{ tool: "draft_email", target: "sarah@acme.com", access: "read" }], targetArgFor))
      .toEqual([]);
  });

  it("drops a tool that declares no target argument", () => {
    expect(grantEntries([{ tool: "add_memory_note", target: "anything", access: "write" }], targetArgFor))
      .toEqual([]);
  });

  it("drops an empty or whitespace target", () => {
    expect(grantEntries([{ tool: "draft_email", target: "   ", access: "write" }], targetArgFor)).toEqual([]);
  });

  it("drops malformed items instead of throwing", () => {
    expect(grantEntries([null, "nope", 42, {}], targetArgFor)).toEqual([]);
    expect(grantEntries(undefined, targetArgFor)).toEqual([]);
  });

  it("de-duplicates identical entries", () => {
    const item = { tool: "draft_email", target: "sarah@acme.com", access: "write" };
    expect(grantEntries([item, { ...item }], targetArgFor)).toEqual(["draft_email sarah@acme.com"]);
  });
});

describe("grant storage", () => {
  beforeEach(async () => {
    await reset();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("hasGrant is false before anything is granted", async () => {
    expect(await hasGrant(getDb(), SCOPE, "draft_email", "sarah@acme.com")).toBe(false);
  });

  it("hasGrant is true for the exact target only", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    expect(await hasGrant(db, SCOPE, "draft_email", "sarah@acme.com")).toBe(true);
    expect(await hasGrant(db, SCOPE, "draft_email", "mike@othercorp.com")).toBe(false);
    expect(await hasGrant(db, SCOPE, "send_email", "sarah@acme.com")).toBe(false);
  });

  it("does not leak across actors or scopes", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    expect(await hasGrant(db, { ...SCOPE, actor: OTHER }, "draft_email", "sarah@acme.com")).toBe(false);
    expect(await hasGrant(db, { ...SCOPE, scopeId: "78" }, "draft_email", "sarah@acme.com")).toBe(false);
  });

  it("insertGrant is idempotent", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    await expect(insertGrant(db, SCOPE, "draft_email", "sarah@acme.com")).resolves.toBeUndefined();
    expect(await listGrants(db, ACTOR)).toHaveLength(1);
  });

  it("listGrants returns only the actor's own grants", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    await insertGrant(db, { ...SCOPE, actor: OTHER }, "draft_email", "mike@othercorp.com");
    const mine = await listGrants(db, ACTOR);
    expect(mine.map((g) => g.target)).toEqual(["sarah@acme.com"]);
  });

  it("revokeGrant deletes only the actor's own row", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    const [grant] = await listGrants(db, ACTOR);
    expect(await revokeGrant(db, OTHER, grant.id)).toBe(false);
    expect(await hasGrant(db, SCOPE, "draft_email", "sarah@acme.com")).toBe(true);
    expect(await revokeGrant(db, ACTOR, grant.id)).toBe(true);
    expect(await hasGrant(db, SCOPE, "draft_email", "sarah@acme.com")).toBe(false);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/approvals-grants.test.ts`
Expected: FAIL — cannot resolve `@/lib/approvals/grants`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/approvals/grants.ts`:

```ts
import type { MemoryActor } from "../memory/actor";
import type { Sql } from "../tools/types";

// Standing scoped approvals, ported from openworker's automation/models.py.
// An entry is "tool target" — one space, tool names never contain spaces —
// binding the allowance to one exact target (recipient address, channel, …).
// A bare tool name is deliberately NOT supported on the write path: that is
// the blanket permission this design exists to avoid.

export interface GrantScope {
  actor: MemoryActor;
  // 'session' today. E1's routine model becomes 'routine' with no schema
  // change — which is why the owner is (scopeKind, scopeId), not a session FK.
  scopeKind: "session" | "routine";
  scopeId: string;
}

export interface Grant {
  id: number;
  tool: string;
  target: string;
  scopeKind: string;
  scopeId: string;
  createdAt: string;
}

export interface ProposedGrant {
  tool: string;
  target: string;
  access: string;
}

export function grantEntry(tool: string, target?: string | null): string {
  return target ? `${tool} ${target}` : tool;
}

export function grantParts(entry: string): { tool: string; target: string | null } {
  const trimmed = entry.trim();
  const space = trimmed.indexOf(" ");
  if (space === -1) return { tool: trimmed, target: null };
  const target = trimmed.slice(space + 1).trim();
  return { tool: trimmed.slice(0, space), target: target || null };
}

// Validate proposed grants down to the entries actually grantable. Only
// `access: "write"` items become grants; the tool must declare a target
// argument (which excludes exec/destructive tools by construction) and the
// target must be non-empty. Reads are disclosure-only — shown on the approval
// card, never stored. Anything else is dropped, fail-closed: a "yes, always"
// that lands here and produces [] degrades to a one-shot yes rather than
// creating a broader permission.
export function grantEntries(
  proposed: unknown,
  targetArgFor: (tool: string) => string | null
): string[] {
  const entries: string[] = [];
  if (!Array.isArray(proposed)) return entries;

  for (const item of proposed) {
    if (typeof item !== "object" || item === null) continue;
    const candidate = item as Partial<ProposedGrant>;
    if (String(candidate.access ?? "").toLowerCase() !== "write") continue;
    const tool = String(candidate.tool ?? "").trim();
    const target = String(candidate.target ?? "").trim();
    if (!tool || !target || targetArgFor(tool) === null) continue;
    const entry = grantEntry(tool, target);
    if (!entries.includes(entry)) entries.push(entry);
  }
  return entries;
}

export async function hasGrant(
  db: Sql,
  scope: GrantScope,
  tool: string,
  target: string
): Promise<boolean> {
  if (!target) return false; // no target ⇒ nothing a grant could bind to
  const rows = await db<{ id: number }[]>`
    SELECT id FROM grants
    WHERE actor_type = ${scope.actor.actorType}
      AND actor_id   = ${scope.actor.actorId}
      AND scope_kind = ${scope.scopeKind}
      AND scope_id   = ${scope.scopeId}
      AND tool       = ${tool}
      AND target     = ${target}
    LIMIT 1
  `;
  return rows.length > 0;
}

export async function insertGrant(
  db: Sql,
  scope: GrantScope,
  tool: string,
  target: string
): Promise<void> {
  await db`
    INSERT INTO grants (actor_type, actor_id, scope_kind, scope_id, tool, target)
    VALUES (${scope.actor.actorType}, ${scope.actor.actorId}, ${scope.scopeKind},
            ${scope.scopeId}, ${tool}, ${target})
    ON CONFLICT DO NOTHING
  `;
}

export async function listGrants(db: Sql, actor: MemoryActor): Promise<Grant[]> {
  const rows = await db<
    { id: number; tool: string; target: string; scope_kind: string; scope_id: string; created_at: Date }[]
  >`
    SELECT id, tool, target, scope_kind, scope_id, created_at FROM grants
    WHERE actor_type = ${actor.actorType} AND actor_id = ${actor.actorId}
    ORDER BY created_at DESC
  `;
  return rows.map((r) => ({
    id: Number(r.id),
    tool: r.tool,
    target: r.target,
    scopeKind: r.scope_kind,
    scopeId: r.scope_id,
    createdAt: r.created_at.toISOString(),
  }));
}

// Scoped to the actor in the WHERE clause, not checked after the fact: a
// director must not be able to revoke (or probe the existence of) another
// director's grant by guessing an id.
export async function revokeGrant(db: Sql, actor: MemoryActor, id: number): Promise<boolean> {
  const rows = await db<{ id: number }[]>`
    DELETE FROM grants
    WHERE id = ${id} AND actor_type = ${actor.actorType} AND actor_id = ${actor.actorId}
    RETURNING id
  `;
  return rows.length > 0;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run tests/approvals-grants.test.ts`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/approvals/grants.ts tests/approvals-grants.test.ts
git commit -m "feat(J3): scoped standing grants with fail-closed validation"
```

---

### Task 3: Pending-question data layer

**Files:**
- Create: `src/lib/approvals/pending.ts`
- Test: `tests/approvals-pending.test.ts`

**Interfaces:**
- Consumes: `pending_questions` table (Task 1); `LlmToolResultBlock` from `src/lib/llm/types.ts`.
- Produces:
  - `interface PendingQuestion { id, sessionId, runId, actor, kind, toolUseId, toolName, toolArgs, question, header, options, allowText, multi, target, insertIndex, partialResults, createdAt }`
  - `createPendingQuestion(db, input: CreatePendingInput): Promise<PendingQuestion>`
  - `getOpenPendingQuestion(db, sessionId: number): Promise<PendingQuestion | null>`
  - `getOpenPendingQuestionById(db, id: number): Promise<PendingQuestion | null>`
  - `resolvePendingQuestion(db, id: number, answer: string): Promise<boolean>`
  - `cancelPendingQuestion(db, id: number): Promise<boolean>`

  Task 8 creates and cancels; Task 9 reads by id and resolves; Task 16 reads the open one for page load.

- [ ] **Step 1: Write the failing test**

Create `tests/approvals-pending.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import type { MemoryActor } from "@/lib/memory/actor";
import { createSession } from "@/lib/chat/sessions";
import {
  cancelPendingQuestion,
  createPendingQuestion,
  getOpenPendingQuestion,
  getOpenPendingQuestionById,
  resolvePendingQuestion,
} from "@/lib/approvals/pending";

const ACTOR: MemoryActor = { actorType: "user", actorId: "pending-test" };

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await runMigrations(db);
}

function input(sessionId: number) {
  return {
    sessionId,
    runId: 123,
    actor: ACTOR,
    kind: "question" as const,
    toolUseId: "tu_abc",
    toolName: "ask_user",
    toolArgs: { question: "Which Sarah?" },
    question: "Which Sarah do you mean?",
    header: "Contact",
    options: ["Sarah Chen", "Sarah Patel"],
    allowText: true,
    multi: false,
    target: null,
    insertIndex: 0,
    partialResults: [],
  };
}

describe("pending questions", () => {
  beforeEach(async () => {
    await reset();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("round-trips every field", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const created = await createPendingQuestion(db, input(session.id));
    const loaded = await getOpenPendingQuestion(db, session.id);
    expect(loaded).not.toBeNull();
    expect(loaded!.id).toBe(created.id);
    expect(loaded!.question).toBe("Which Sarah do you mean?");
    expect(loaded!.options).toEqual(["Sarah Chen", "Sarah Patel"]);
    expect(loaded!.allowText).toBe(true);
    expect(loaded!.multi).toBe(false);
    expect(loaded!.toolUseId).toBe("tu_abc");
    expect(loaded!.actor).toEqual(ACTOR);
    expect(loaded!.partialResults).toEqual([]);
  });

  it("round-trips partial results and insert index", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const partialResults = [
      { toolUseId: "tu_first", toolName: "search_memory", content: "Success: {}", isError: false },
    ];
    await createPendingQuestion(db, { ...input(session.id), insertIndex: 1, partialResults });
    const loaded = await getOpenPendingQuestion(db, session.id);
    expect(loaded!.insertIndex).toBe(1);
    expect(loaded!.partialResults).toEqual(partialResults);
  });

  it("returns null when the session has no open question", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    expect(await getOpenPendingQuestion(db, session.id)).toBeNull();
  });

  it("resolving stores the answer and closes the row", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const created = await createPendingQuestion(db, input(session.id));
    expect(await resolvePendingQuestion(db, created.id, "Sarah Chen")).toBe(true);
    expect(await getOpenPendingQuestion(db, session.id)).toBeNull();
    expect(await getOpenPendingQuestionById(db, created.id)).toBeNull();
    const [row] = await db<{ status: string; answer: string }[]>`
      SELECT status, answer FROM pending_questions WHERE id = ${created.id}
    `;
    expect(row.status).toBe("answered");
    expect(row.answer).toBe("Sarah Chen");
  });

  it("resolving twice is refused, so an answer cannot be replayed", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const created = await createPendingQuestion(db, input(session.id));
    expect(await resolvePendingQuestion(db, created.id, "Sarah Chen")).toBe(true);
    expect(await resolvePendingQuestion(db, created.id, "Sarah Patel")).toBe(false);
  });

  it("cancelling frees the session for a new question", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const first = await createPendingQuestion(db, input(session.id));
    expect(await cancelPendingQuestion(db, first.id)).toBe(true);
    const second = await createPendingQuestion(db, input(session.id));
    expect(second.id).not.toBe(first.id);
  });

  it("a second open question in one session is rejected by the database", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    await createPendingQuestion(db, input(session.id));
    await expect(createPendingQuestion(db, input(session.id))).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/approvals-pending.test.ts`
Expected: FAIL — cannot resolve `@/lib/approvals/pending`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/approvals/pending.ts`:

```ts
import type postgres from "postgres";
import type { LlmToolResultBlock } from "../llm/types";
import type { MemoryActor } from "../memory/actor";
import type { Sql } from "../tools/types";

export type PendingKind = "question" | "approval";

export interface PendingQuestion {
  id: number;
  sessionId: number;
  runId: number | null;
  actor: MemoryActor;
  kind: PendingKind;
  // The tool_use block this run suspended on. Its tool_result is deliberately
  // absent from chat_messages until the answer lands.
  toolUseId: string;
  toolName: string;
  // The validated args the tool was called with. An approval re-executes the
  // tool from these on "yes" — the model does not get to restate them, so the
  // director approves exactly what was proposed.
  toolArgs: Record<string, unknown>;
  question: string;
  header: string | null;
  options: string[];
  allowText: boolean;
  multi: boolean;
  target: string | null;
  // Where the resumed result slots back into the deferred tool_result
  // message's block array.
  insertIndex: number;
  partialResults: LlmToolResultBlock[];
  createdAt: string;
}

export interface CreatePendingInput {
  sessionId: number;
  runId: number | null;
  actor: MemoryActor;
  kind: PendingKind;
  toolUseId: string;
  toolName: string;
  toolArgs: Record<string, unknown>;
  question: string;
  header: string | null;
  options: string[];
  allowText: boolean;
  multi: boolean;
  target: string | null;
  insertIndex: number;
  partialResults: LlmToolResultBlock[];
}

interface PendingRow {
  id: number;
  session_id: number;
  run_id: number | null;
  actor_type: string;
  actor_id: string;
  kind: string;
  tool_use_id: string;
  tool_name: string;
  tool_args_json: unknown;
  question: string;
  header: string | null;
  options_json: unknown;
  allow_text: boolean;
  multi: boolean;
  target: string | null;
  insert_index: number;
  partial_results_json: unknown;
  created_at: Date;
}

const SELECT_COLUMNS = `id, session_id, run_id, actor_type, actor_id, kind, tool_use_id,
  tool_name, tool_args_json, question, header, options_json, allow_text, multi, target,
  insert_index, partial_results_json, created_at`;

function toPending(row: PendingRow): PendingQuestion {
  return {
    id: Number(row.id),
    sessionId: Number(row.session_id),
    runId: row.run_id === null ? null : Number(row.run_id),
    actor: { actorType: row.actor_type as MemoryActor["actorType"], actorId: row.actor_id },
    kind: row.kind as PendingKind,
    toolUseId: row.tool_use_id,
    toolName: row.tool_name,
    toolArgs: (row.tool_args_json ?? {}) as Record<string, unknown>,
    question: row.question,
    header: row.header,
    options: (row.options_json ?? []) as string[],
    allowText: row.allow_text,
    multi: row.multi,
    target: row.target,
    insertIndex: Number(row.insert_index),
    partialResults: (row.partial_results_json ?? []) as LlmToolResultBlock[],
    createdAt: row.created_at.toISOString(),
  };
}

function toJson(db: postgres.Sql | postgres.TransactionSql, value: unknown) {
  return db.json(value as postgres.JSONValue);
}

export async function createPendingQuestion(
  db: Sql,
  input: CreatePendingInput
): Promise<PendingQuestion> {
  const [row] = await db<PendingRow[]>`
    INSERT INTO pending_questions
      (session_id, run_id, actor_type, actor_id, kind, tool_use_id, tool_name, tool_args_json,
       question, header, options_json, allow_text, multi, target, insert_index, partial_results_json)
    VALUES
      (${input.sessionId}, ${input.runId}, ${input.actor.actorType}, ${input.actor.actorId},
       ${input.kind}, ${input.toolUseId}, ${input.toolName}, ${toJson(db, input.toolArgs)},
       ${input.question}, ${input.header}, ${toJson(db, input.options)}, ${input.allowText},
       ${input.multi}, ${input.target}, ${input.insertIndex}, ${toJson(db, input.partialResults)})
    RETURNING ${db.unsafe(SELECT_COLUMNS)}
  `;
  return toPending(row);
}

export async function getOpenPendingQuestion(
  db: Sql,
  sessionId: number
): Promise<PendingQuestion | null> {
  const [row] = await db<PendingRow[]>`
    SELECT ${db.unsafe(SELECT_COLUMNS)} FROM pending_questions
    WHERE session_id = ${sessionId} AND status = 'open'
    LIMIT 1
  `;
  return row ? toPending(row) : null;
}

export async function getOpenPendingQuestionById(
  db: Sql,
  id: number
): Promise<PendingQuestion | null> {
  const [row] = await db<PendingRow[]>`
    SELECT ${db.unsafe(SELECT_COLUMNS)} FROM pending_questions
    WHERE id = ${id} AND status = 'open'
    LIMIT 1
  `;
  return row ? toPending(row) : null;
}

// The `status = 'open'` predicate makes this a compare-and-set: a second
// answer for the same question returns false rather than resuming the run
// twice (a double-click must not send two Gmail drafts).
export async function resolvePendingQuestion(
  db: Sql,
  id: number,
  answer: string
): Promise<boolean> {
  const rows = await db<{ id: number }[]>`
    UPDATE pending_questions
    SET status = 'answered', answer = ${answer}, resolved_at = now()
    WHERE id = ${id} AND status = 'open'
    RETURNING id
  `;
  return rows.length > 0;
}

export async function cancelPendingQuestion(db: Sql, id: number): Promise<boolean> {
  const rows = await db<{ id: number }[]>`
    UPDATE pending_questions
    SET status = 'cancelled', resolved_at = now()
    WHERE id = ${id} AND status = 'open'
    RETURNING id
  `;
  return rows.length > 0;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run tests/approvals-pending.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lib/approvals/pending.ts tests/approvals-pending.test.ts
git commit -m "feat(J3): pending-question store for suspended runs"
```

---

### Task 4: Suspend signal, `Tool.targetArg`, and the grant gate in `executeTool`

**Files:**
- Create: `src/lib/approvals/suspend.ts`
- Modify: `src/lib/tools/types.ts` (add `targetArg` to `Tool`)
- Modify: `src/lib/tools/orchestrator.ts` (gate + suspend handling)
- Test: `tests/approvals-gate.test.ts`

**Interfaces:**
- Consumes: `hasGrant`, `GrantScope` (Task 2).
- Produces:
  - `src/lib/approvals/suspend.ts`: `interface SuspendRequest { kind: "question" | "approval"; question: string; header: string | null; options: string[]; allowText: boolean; multi: boolean; target: string | null }` and `class ToolSuspend extends Error { readonly request: SuspendRequest }`
  - `Tool.targetArg?: string` — the args key holding the grantable target. Required for `draft`/`admin`.
  - `ToolExecutionResult.suspend?: SuspendRequest` (existing `content`/`isError` unchanged).
  - `ExecuteToolInput.grantScope?: GrantScope | null` and `ExecuteToolInput.grantOverride?: boolean`.
  - `targetArgFor(registry, tool): string | null` exported from `orchestrator.ts` for Task 9's `grantEntries` call.

  Task 5 throws `ToolSuspend`. Task 6 reads `result.suspend`. Task 9 passes `grantOverride`.

- [ ] **Step 1: Write the failing test**

Create `tests/approvals-gate.test.ts`:

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { startRun, startRunStep } from "@/lib/ledger";
import { createToolRegistry } from "@/lib/tools/registry";
import { executeTool } from "@/lib/tools/orchestrator";
import { ToolSuspend } from "@/lib/approvals/suspend";
import { insertGrant, type GrantScope } from "@/lib/approvals/grants";
import type { MemoryActor } from "@/lib/memory/actor";
import type { PermissionClass, Tool } from "@/lib/tools/types";

const ACTOR: MemoryActor = { actorType: "user", actorId: "gate-test" };
const SCOPE: GrantScope = { actor: ACTOR, scopeKind: "session", scopeId: "5" };
const ALLOWED = new Set<PermissionClass>(["read", "draft", "admin"]);

let executions = 0;

const draftTool: Tool<{ to: string; body: string }, { ok: true }> = {
  name: "fake_draft",
  description: "Draft something to a recipient.",
  permissionClass: "draft",
  targetArg: "to",
  argsSchema: z.object({ to: z.string(), body: z.string() }),
  async execute() {
    executions += 1;
    return { ok: true };
  },
};

// draft class, but declares no target — must be denied, never allowed.
const untargetedTool: Tool<Record<string, never>, { ok: true }> = {
  name: "fake_untargeted",
  description: "A draft-class tool that forgot to declare a target.",
  permissionClass: "draft",
  argsSchema: z.object({}),
  async execute() {
    executions += 1;
    return { ok: true };
  },
};

const suspendingTool: Tool<{ question: string }, never> = {
  name: "fake_ask",
  description: "Suspends the run.",
  permissionClass: "read",
  argsSchema: z.object({ question: z.string() }),
  async execute(args) {
    throw new ToolSuspend({
      kind: "question",
      question: args.question,
      header: "Contact",
      options: ["A", "B"],
      allowText: true,
      multi: false,
      target: null,
    });
  },
};

const registry = createToolRegistry([draftTool, untargetedTool, suspendingTool]);

async function ledgerIds() {
  const db = getDb();
  const run = await startRun(db, { runType: "agent_chat", title: "gate", input: {}, actor: ACTOR });
  const step = await startRunStep(db, { runId: run.id, stepKind: "agent", name: "loop", input: {} });
  return { runId: run.id, parentStepId: step.id };
}

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await runMigrations(db);
  executions = 0;
}

describe("grant gate in executeTool", () => {
  beforeEach(async () => {
    await reset();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("suspends a draft-class call with no grant, without executing the tool", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_1",
      name: "fake_draft",
      input: { to: "sarah@acme.com", body: "hi" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    expect(result.suspend).toBeDefined();
    expect(result.suspend!.kind).toBe("approval");
    expect(result.suspend!.target).toBe("sarah@acme.com");
    expect(result.suspend!.question).toContain("sarah@acme.com");
    expect(executions).toBe(0);
  });

  it("executes without prompting when a grant exists for that exact target", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "fake_draft", "sarah@acme.com");
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_2",
      name: "fake_draft",
      input: { to: "sarah@acme.com", body: "hi" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    expect(result.suspend).toBeUndefined();
    expect(result.isError).toBe(false);
    expect(executions).toBe(1);
  });

  it("still prompts for a different target", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "fake_draft", "sarah@acme.com");
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_3",
      name: "fake_draft",
      input: { to: "mike@othercorp.com", body: "hi" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    expect(result.suspend?.target).toBe("mike@othercorp.com");
    expect(executions).toBe(0);
  });

  it("grantOverride executes once without consulting or creating a grant", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_4",
      name: "fake_draft",
      input: { to: "sarah@acme.com", body: "hi" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      grantOverride: true,
      ...ids,
    });
    expect(result.suspend).toBeUndefined();
    expect(executions).toBe(1);
    const rows = await db`SELECT id FROM grants`;
    expect(rows).toHaveLength(0);
  });

  it("denies a draft-class tool that declares no targetArg", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_5",
      name: "fake_untargeted",
      input: {},
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    expect(result.isError).toBe(true);
    expect(result.content).toContain("ungrantable_tool");
    expect(result.suspend).toBeUndefined();
    expect(executions).toBe(0);
  });

  it("denies a draft-class call whose target is empty", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_6",
      name: "fake_draft",
      input: { to: "   ", body: "hi" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    expect(result.isError).toBe(true);
    expect(result.content).toContain("missing_target");
    expect(executions).toBe(0);
  });

  it("denies a draft-class call when there is no grant scope at all", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_7",
      name: "fake_draft",
      input: { to: "sarah@acme.com", body: "hi" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: null,
      ...ids,
    });
    expect(result.isError).toBe(true);
    expect(result.content).toContain("no_approval_channel");
    expect(executions).toBe(0);
  });

  it("turns a ToolSuspend throw into a question suspend, not an error", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    const result = await executeTool({
      toolUseId: "tu_8",
      name: "fake_ask",
      input: { question: "Which Sarah?" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    expect(result.suspend).toEqual({
      kind: "question",
      question: "Which Sarah?",
      header: "Contact",
      options: ["A", "B"],
      allowText: true,
      multi: false,
      target: null,
    });
    expect(result.isError).toBe(true); // the block carries no usable result yet
  });

  it("records a suspended call in the ledger as awaiting_user", async () => {
    const db = getDb();
    const ids = await ledgerIds();
    await executeTool({
      toolUseId: "tu_9",
      name: "fake_ask",
      input: { question: "Which Sarah?" },
      registry,
      allowed: ALLOWED,
      db,
      actor: ACTOR,
      grantScope: SCOPE,
      ...ids,
    });
    const [row] = await db<{ status: string; error_type: string }[]>`
      SELECT status, error_type FROM tool_calls WHERE run_id = ${ids.runId} ORDER BY id DESC LIMIT 1
    `;
    expect(row.status).toBe("failed");
    expect(row.error_type).toBe("awaiting_user");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/approvals-gate.test.ts`
Expected: FAIL — cannot resolve `@/lib/approvals/suspend`.

- [ ] **Step 3a: Write the suspend signal**

Create `src/lib/approvals/suspend.ts`:

```ts
// The signal a tool raises when it cannot proceed without a human. Thrown by
// `ask_user`; constructed directly by the grant gate in executeTool. Kept in
// its own module so tools can raise it without importing the orchestrator
// (which imports the registry, which imports the tools).

export interface SuspendRequest {
  kind: "question" | "approval";
  question: string;
  header: string | null;
  options: string[];
  allowText: boolean;
  multi: boolean;
  // The grantable target, for `approval` only. `question` suspends never
  // carry one — an open question is not a permission.
  target: string | null;
}

export class ToolSuspend extends Error {
  readonly request: SuspendRequest;

  constructor(request: SuspendRequest) {
    super("tool suspended pending user input");
    this.name = "ToolSuspend";
    this.request = request;
  }
}
```

- [ ] **Step 3b: Add `targetArg` to the Tool interface**

In `src/lib/tools/types.ts`, add to the `Tool` interface, after `permissionClass`:

```ts
  permissionClass: PermissionClass;
  // The args key holding this tool's grantable target (e.g. "to" for a
  // recipient address). REQUIRED for `draft` and `admin` tools: the grant gate
  // denies a targetless write rather than allowing it, which is what keeps
  // exec/destructive-shaped tools out of the standing-grant model by
  // construction (openworker's target_arg_for, ported).
  targetArg?: string;
```

- [ ] **Step 3c: Add the gate to the orchestrator**

In `src/lib/tools/orchestrator.ts`:

Add imports at the top:

```ts
import { hasGrant, type GrantScope } from "../approvals/grants";
import { ToolSuspend, type SuspendRequest } from "../approvals/suspend";
```

Extend the result and input interfaces:

```ts
export interface ToolExecutionResult {
  content: string; // final, already-truncated text for a tool_result block
  isError: boolean;
  // Present when this call needs a human before it can produce a result. The
  // caller (runAgentLoop) must NOT append `content` as this block's
  // tool_result — the whole tool_result message is deferred until the answer
  // lands. See src/lib/approvals/suspend.ts.
  suspend?: SuspendRequest;
}
```

```ts
export interface ExecuteToolInput {
  // ...existing fields unchanged...
  actor: MemoryActor;
  // Where a standing grant would be looked up. Null means this surface has no
  // way to ask a human (e.g. an unattended path), and draft/admin calls are
  // denied outright rather than executed unapproved.
  grantScope?: GrantScope | null;
  // One-shot approval already given by the director for exactly this call
  // (the resume path). Skips the grant lookup; never writes a grant.
  grantOverride?: boolean;
}
```

Classes that need approval — add above `executeTool`:

```ts
// Every class that can act outside Sourcecado on a named target. `enrich` is
// deliberately absent: it spends credits but writes nothing outward, and is
// governed by the enrich spend ceiling instead.
const APPROVAL_CLASSES: ReadonlySet<PermissionClass> = new Set(["draft", "admin"]);

// The args key a tool's grants bind to, or null if it declares none.
// Signature matches openworker's target_arg_for; Task 9 passes this into
// grantEntries so the validator and the gate agree on what is grantable.
export function targetArgFor(registry: ToolRegistry, tool: string): string | null {
  return registry.get(tool)?.targetArg ?? null;
}
```

Insert the gate in `executeTool`, immediately after the `argsSchema.safeParse` block and before the `try`:

```ts
  if (APPROVAL_CLASSES.has(tool.permissionClass) && !opts.grantOverride) {
    if (!tool.targetArg) {
      return failTool(
        db,
        toolStep.id,
        toolCall.id,
        "ungrantable_tool",
        `Tool ${name} is class ${tool.permissionClass} but declares no target argument, so it cannot be approved. This is a registration bug, not a permission the director can give.`
      );
    }
    const rawTarget = (parsed.data as Record<string, unknown>)[tool.targetArg];
    const target = typeof rawTarget === "string" ? rawTarget.trim() : "";
    if (!target) {
      return failTool(
        db,
        toolStep.id,
        toolCall.id,
        "missing_target",
        `Tool ${name} requires a non-empty "${tool.targetArg}" to be approved.`
      );
    }
    if (!opts.grantScope) {
      return failTool(
        db,
        toolStep.id,
        toolCall.id,
        "no_approval_channel",
        `Tool ${name} needs the director's approval, and this run has no way to ask.`
      );
    }
    if (!(await hasGrant(db, opts.grantScope, name, target))) {
      return suspendTool(db, toolStep.id, toolCall.id, {
        kind: "approval",
        question: `Allow ${name} for ${target}?`,
        header: "Approval",
        options: [],
        allowText: false,
        multi: false,
        target,
      });
    }
  }
```

Add the `ToolSuspend` catch inside the existing `try/catch`, as the first line of the `catch` block:

```ts
  } catch (error) {
    if (error instanceof ToolSuspend) {
      return suspendTool(db, toolStep.id, toolCall.id, error.request);
    }
    const message = error instanceof Error ? error.message : String(error);
    return failTool(db, toolStep.id, toolCall.id, "tool_error", `Tool ${name} failed: ${message}`);
  }
```

Add the helper next to `failTool`:

```ts
// A suspended call produced no result, so the ledger records it as failed with
// errorType `awaiting_user` rather than claiming success. The resumed run opens
// its own step; `awaiting_user` is how a trace reader tells "stopped for a
// human" apart from a real failure, and how K2 can exclude it from error rates.
async function suspendTool(
  db: Sql,
  runStepId: number,
  toolCallId: number,
  request: SuspendRequest
): Promise<ToolExecutionResult> {
  const message = `Awaiting the director's answer: ${request.question}`;
  await failToolCall(db, { toolCallId, errorType: "awaiting_user", errorMessage: message });
  await failRunStep(db, { runStepId, errorType: "awaiting_user", errorMessage: message });
  return { content: truncate(`Error (awaiting_user): ${message}`), isError: true, suspend: request };
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/approvals-gate.test.ts`
Expected: PASS (9 tests).

Then confirm nothing regressed in the existing orchestrator suites:

Run: `npx vitest run tests/permissions.test.ts tests/agent-loop.test.ts tests/echo-tool.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/approvals/suspend.ts src/lib/tools/types.ts src/lib/tools/orchestrator.ts tests/approvals-gate.test.ts
git commit -m "feat(J3): gate draft/admin tool calls behind scoped grants"
```

---

### Task 5: The `ask_user` tool

**Files:**
- Create: `src/lib/tools/ask-user.ts`
- Modify: `src/lib/memory/answer-config.ts` (register it)
- Test: `tests/ask-user-tool.test.ts`

**Interfaces:**
- Consumes: `ToolSuspend` (Task 4); `Tool` from `src/lib/tools/types.ts`.
- Produces: `askUserTool: Tool<AskUserArgs, never>` with `name: "ask_user"`, `permissionClass: "read"`. Present in `memoryRegistry()`.

**Design note:** `permissionClass` is `"read"` because asking a human writes nothing and spends nothing — it is the lowest-risk tool in the registry, and gating it behind approval would deadlock (the gate's own escape hatch cannot itself need approval). It is deliberately in the default chat allowlist (`read, write_internal, enrich`).

- [ ] **Step 1: Write the failing test**

Create `tests/ask-user-tool.test.ts`:

```ts
import { askUserTool } from "@/lib/tools/ask-user";
import { ToolSuspend } from "@/lib/approvals/suspend";
import { memoryRegistry } from "@/lib/memory/answer-config";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import type { Sql } from "@/lib/tools/types";

const ctx = { db: {} as Sql, runId: 1, parentStepId: 1, actor: DEFAULT_ACTOR };

async function suspendFrom(args: unknown): Promise<ToolSuspend> {
  const parsed = askUserTool.argsSchema.parse(args);
  try {
    await askUserTool.execute(parsed, ctx);
  } catch (error) {
    if (error instanceof ToolSuspend) return error;
    throw error;
  }
  throw new Error("ask_user resolved instead of suspending");
}

describe("ask_user tool", () => {
  it("is a read-class tool named ask_user", () => {
    expect(askUserTool.name).toBe("ask_user");
    expect(askUserTool.permissionClass).toBe("read");
  });

  it("always suspends — it never returns a value", async () => {
    const suspend = await suspendFrom({ question: "Which Sarah do you mean?" });
    expect(suspend.request.kind).toBe("question");
    expect(suspend.request.question).toBe("Which Sarah do you mean?");
  });

  it("defaults to free text allowed, single select, no options", async () => {
    const suspend = await suspendFrom({ question: "Why?" });
    expect(suspend.request.allowText).toBe(true);
    expect(suspend.request.multi).toBe(false);
    expect(suspend.request.options).toEqual([]);
    expect(suspend.request.header).toBeNull();
  });

  it("carries options, multi and header through", async () => {
    const suspend = await suspendFrom({
      question: "Which regions?",
      options: ["EMEA", "APAC"],
      multi: true,
      allow_text: false,
      header: "Region",
    });
    expect(suspend.request.options).toEqual(["EMEA", "APAC"]);
    expect(suspend.request.multi).toBe(true);
    expect(suspend.request.allowText).toBe(false);
    expect(suspend.request.header).toBe("Region");
  });

  it("never carries a target — an open question is not a permission", async () => {
    const suspend = await suspendFrom({ question: "Why?" });
    expect(suspend.request.target).toBeNull();
  });

  it("re-opens free text when allow_text is false and no options are given", async () => {
    // Otherwise the director gets a card with nothing to click and no way to type.
    const suspend = await suspendFrom({ question: "Why?", allow_text: false });
    expect(suspend.request.allowText).toBe(true);
  });

  it("rejects an empty question", () => {
    expect(() => askUserTool.argsSchema.parse({ question: "" })).toThrow();
  });

  it("is registered in the memory registry", () => {
    expect(memoryRegistry().get("ask_user")).toBeDefined();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/ask-user-tool.test.ts`
Expected: FAIL — cannot resolve `@/lib/tools/ask-user`.

- [ ] **Step 3a: Write the tool**

Create `src/lib/tools/ask-user.ts`:

```ts
import { z } from "zod";
import { ToolSuspend } from "../approvals/suspend";
import type { Tool } from "./types";

// The human-in-the-loop Q&A primitive, ported from openworker's
// coworker/tools/ask.py. There is no return value and no fallback: execute()
// always throws ToolSuspend, the orchestrator turns that into a suspended run,
// and the director's answer comes back as this call's tool_result. The
// snake_case arg names match ask.py so the model-facing schema is the one the
// reference design documents.
const argsSchema = z.object({
  question: z.string().min(1),
  options: z.array(z.string()).default([]),
  allow_text: z.boolean().default(true),
  multi: z.boolean().default(false),
  header: z.string().max(24).default(""),
});

export type AskUserArgs = z.infer<typeof argsSchema>;

export const askUserTool: Tool<AskUserArgs, never> = {
  name: "ask_user",
  description:
    "Ask the director a question and wait for their answer — use when you genuinely need a human " +
    "decision or a fact you cannot infer (a preference, a missing detail, a choice between real " +
    "alternatives). Prefer this over guessing or stalling. `options` offers quick replies when the " +
    "answer is one of a few discrete choices; leave it empty for an open question. `allow_text` " +
    "keeps a typed answer available alongside options. `multi` lets them pick more than one. " +
    "`header` is a short (<=24 char) label for the card. The run pauses until they answer, so do " +
    "not ask what you can reasonably decide yourself.",
  // Asking a human writes nothing and spends nothing. It must also never be
  // gated by the approval flow, since it IS the approval flow's escape hatch.
  permissionClass: "read",
  argsSchema,
  async execute(args): Promise<never> {
    throw new ToolSuspend({
      kind: "question",
      question: args.question,
      header: args.header.trim() || null,
      options: args.options,
      // A card with no options and no text box is unanswerable; treat
      // allow_text: false with no options as the model's mistake, not a
      // dead end for the director.
      allowText: args.allow_text || args.options.length === 0,
      multi: args.multi,
      target: null,
    });
  },
};
```

- [ ] **Step 3b: Register it**

In `src/lib/memory/answer-config.ts`, import `askUserTool` and add it to the tool list passed to `createToolRegistry`, alongside the existing memory tools.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/ask-user-tool.test.ts tests/memory-answer.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/tools/ask-user.ts src/lib/memory/answer-config.ts tests/ask-user-tool.test.ts
git commit -m "feat(J3): add the ask_user tool"
```

---

### Task 6: Agent loop suspends instead of finishing

**Files:**
- Modify: `src/lib/agent-loop.ts`
- Test: `tests/agent-loop-suspend.test.ts`

**Interfaces:**
- Consumes: `ToolExecutionResult.suspend`, `ExecuteToolInput.grantScope` (Task 4).
- Produces:
  - `AgentLoopResult.status` widens to `"succeeded" | "failed" | "suspended"`.
  - `AgentLoopResult.pending?: { request: SuspendRequest; toolUseId: string; toolName: string; toolArgs: Record<string, unknown>; insertIndex: number; partialResults: LlmToolResultBlock[] }`
  - `AgentLoopInput.grantScope?: GrantScope | null` (forwarded to `executeTool`).
  - New event `{ type: "suspended"; request: SuspendRequest }` on `AgentLoopEvent`.

  Task 7 reads `result.pending`. Task 8 persists it and streams the event.

**Critical invariant:** when the loop suspends, `messages` ends with the assistant message whose `tool_use` blocks are **not yet answered**. The `tool_result` message is deferred in its entirety — never half-written — because a transcript with a `tool_result` message that omits one of the assistant's `tool_use` ids is rejected by both providers.

- [ ] **Step 1: Write the failing test**

Create `tests/agent-loop-suspend.test.ts`:

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { startRun, startRunStep } from "@/lib/ledger";
import { runAgentLoop, type AgentLoopEvent } from "@/lib/agent-loop";
import { createToolRegistry } from "@/lib/tools/registry";
import { ToolSuspend } from "@/lib/approvals/suspend";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import type { GrantScope } from "@/lib/approvals/grants";
import type { LlmAdapter, LlmMessage } from "@/lib/llm/types";
import type { PermissionClass, Tool } from "@/lib/tools/types";

const SCOPE: GrantScope = { actor: DEFAULT_ACTOR, scopeKind: "session", scopeId: "1" };
const ALLOWED = new Set<PermissionClass>(["read"]);

let sideEffects: string[] = [];

const okTool: Tool<{ q: string }, { ok: true }> = {
  name: "ok_tool",
  description: "Succeeds.",
  permissionClass: "read",
  argsSchema: z.object({ q: z.string() }),
  async execute(args) {
    sideEffects.push(`ok:${args.q}`);
    return { ok: true };
  },
};

const askTool: Tool<{ question: string }, never> = {
  name: "ask_tool",
  description: "Suspends.",
  permissionClass: "read",
  argsSchema: z.object({ question: z.string() }),
  async execute(args): Promise<never> {
    sideEffects.push("ask");
    throw new ToolSuspend({
      kind: "question",
      question: args.question,
      header: null,
      options: [],
      allowText: true,
      multi: false,
      target: null,
    });
  },
};

const registry = createToolRegistry([okTool, askTool]);

// Adapter that emits one assistant turn with the given tool_use blocks, then
// (if reached again) a plain text turn.
function adapterWith(blocks: { id: string; name: string; input: unknown }[]): LlmAdapter {
  let call = 0;
  return {
    name: "stub",
    async *stream() {
      call += 1;
      if (call === 1) {
        return {
          message: {
            role: "assistant" as const,
            content: blocks.map((b) => ({ type: "tool_use" as const, ...b })),
          },
          stopReason: "tool_use" as const,
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }
      return {
        message: { role: "assistant" as const, content: [{ type: "text" as const, text: "done" }] },
        stopReason: "end" as const,
        usage: { inputTokens: 0, outputTokens: 0 },
      };
    },
  } as unknown as LlmAdapter;
}

async function loopWith(blocks: { id: string; name: string; input: unknown }[]) {
  const db = getDb();
  const run = await startRun(db, {
    runType: "agent_chat",
    title: "suspend",
    input: {},
    actor: DEFAULT_ACTOR,
  });
  const step = await startRunStep(db, { runId: run.id, stepKind: "agent", name: "loop", input: {} });
  const events: AgentLoopEvent[] = [];
  const messages: LlmMessage[] = [
    { role: "system", content: "test" },
    { role: "user", content: "go" },
  ];
  const result = await runAgentLoop({
    messages,
    registry,
    allowed: ALLOWED,
    db,
    runId: run.id,
    parentStepId: step.id,
    actor: DEFAULT_ACTOR,
    grantScope: SCOPE,
    adapter: adapterWith(blocks),
    onEvent: (event) => {
      events.push(event);
    },
  });
  return { result, events };
}

describe("agent loop suspension", () => {
  beforeEach(async () => {
    await runMigrations(getDb());
    sideEffects = [];
  });
  afterAll(async () => {
    await closeDb();
  });

  it("returns status suspended with the pending request", async () => {
    const { result } = await loopWith([
      { id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
    ]);
    expect(result.status).toBe("suspended");
    expect(result.pending?.request.question).toBe("Which Sarah?");
    expect(result.pending?.toolUseId).toBe("tu_1");
    expect(result.pending?.toolName).toBe("ask_tool");
    expect(result.pending?.toolArgs).toEqual({ question: "Which Sarah?" });
  });

  it("leaves the tool_use unanswered — no tool_result message is appended", async () => {
    const { result } = await loopWith([
      { id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
    ]);
    expect(result.messages.at(-1)!.role).toBe("assistant");
    expect(result.messages.some((m) => m.role === "tool_result")).toBe(false);
  });

  it("keeps results for blocks executed before the suspend, in order", async () => {
    const { result } = await loopWith([
      { id: "tu_0", name: "ok_tool", input: { q: "first" } },
      { id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
    ]);
    expect(result.pending?.insertIndex).toBe(1);
    expect(result.pending?.partialResults.map((b) => b.toolUseId)).toEqual(["tu_0"]);
    expect(result.pending!.partialResults[0].isError).toBe(false);
  });

  it("does not execute blocks after the suspend, but records them as not_executed", async () => {
    const { result } = await loopWith([
      { id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
      { id: "tu_2", name: "ok_tool", input: { q: "after" } },
    ]);
    expect(sideEffects).toEqual(["ask"]);
    const after = result.pending!.partialResults.find((b) => b.toolUseId === "tu_2");
    expect(after?.isError).toBe(true);
    expect(after?.content).toContain("not_executed");
    expect(result.pending?.insertIndex).toBe(0);
  });

  it("emits a suspended event", async () => {
    const { events } = await loopWith([
      { id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
    ]);
    const suspended = events.find((e) => e.type === "suspended");
    expect(suspended).toBeDefined();
  });

  it("stops the loop — the model is not called again", async () => {
    const { result } = await loopWith([
      { id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
    ]);
    expect(result.steps).toBe(1);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/agent-loop-suspend.test.ts`
Expected: FAIL — `status` is `"failed"`, `pending` is undefined.

- [ ] **Step 3: Modify the agent loop**

In `src/lib/agent-loop.ts`:

Add imports:

```ts
import type { GrantScope } from "./approvals/grants";
import type { SuspendRequest } from "./approvals/suspend";
```

Extend the input, event and result types:

```ts
export interface AgentLoopInput {
  // ...existing fields unchanged...
  actor: MemoryActor;
  // Where standing grants are looked up for draft/admin calls, and implicitly
  // whether this surface can ask a human at all. Null/omitted denies those
  // classes outright — see executeTool's `no_approval_channel`.
  grantScope?: GrantScope | null;
  // ...
}

export type AgentLoopEvent =
  | { type: "llm"; event: LlmStreamEvent }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "tool_end"; id: string; name: string; result: ToolExecutionResult }
  | { type: "suspended"; request: SuspendRequest };

export interface PendingToolCall {
  request: SuspendRequest;
  toolUseId: string;
  toolName: string;
  toolArgs: Record<string, unknown>;
  // Index at which this call's result slots back into the deferred
  // tool_result message's block array.
  insertIndex: number;
  // Results for every OTHER block in the same assistant message: executed
  // ones before the suspend, not_executed placeholders after it.
  partialResults: LlmToolResultBlock[];
}

export interface AgentLoopResult {
  status: "succeeded" | "failed" | "suspended";
  messages: LlmMessage[];
  finalText?: string;
  stopReason: StopReason;
  steps: number;
  pending?: PendingToolCall;
}
```

Replace the tool-execution `for` loop body (currently lines ~131-152) with:

```ts
    const resultBlocks: LlmToolResultBlock[] = [];
    let pending: PendingToolCall | null = null;

    for (const block of toolUseBlocks) {
      // Once one call has suspended, the rest of this turn's calls do NOT run.
      // The director has not seen them, and executing them while the run is
      // parked would be work they never agreed to. They still need a
      // tool_result so the deferred message answers every tool_use id.
      if (pending) {
        resultBlocks.push({
          toolUseId: block.id,
          toolName: block.name,
          content: "Error (not_executed): the run suspended for the director's answer before this tool call could execute.",
          isError: true,
        });
        continue;
      }

      await input.onEvent?.({ type: "tool_start", id: block.id, name: block.name, input: block.input });
      const result = await executeTool({
        toolUseId: block.id,
        name: block.name,
        input: block.input,
        registry: input.registry,
        allowed: input.allowed,
        db: input.db,
        runId: input.runId,
        parentStepId: input.parentStepId,
        actor: input.actor,
        grantScope: input.grantScope ?? null,
      });
      await input.onEvent?.({ type: "tool_end", id: block.id, name: block.name, result });

      if (result.suspend) {
        pending = {
          request: result.suspend,
          toolUseId: block.id,
          toolName: block.name,
          toolArgs: (block.input ?? {}) as Record<string, unknown>,
          insertIndex: resultBlocks.length,
          partialResults: resultBlocks,
        };
        continue;
      }

      resultBlocks.push({
        toolUseId: block.id,
        toolName: block.name,
        content: result.content,
        isError: result.isError,
      });
    }

    if (pending) {
      // `messages` ends at the assistant message, with its tool_use blocks
      // deliberately unanswered. The whole tool_result message is deferred to
      // the resume path — a partial one (missing the suspended id) is rejected
      // by both providers, so there is no valid half-way state to persist.
      await input.onEvent?.({ type: "suspended", request: pending.request });
      return { status: "suspended", messages, stopReason: "tool_use", steps: step, pending };
    }

    messages.push({ role: "tool_result", content: resultBlocks });
```

Note `pending.partialResults` aliases `resultBlocks`, which keeps accumulating the not_executed blocks after the suspend — that is intentional, and the ordering test covers it.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/agent-loop-suspend.test.ts tests/agent-loop.test.ts tests/harness.test.ts`
Expected: PASS. (Existing agent-loop tests must be untouched — a run with no suspending tool behaves exactly as before.)

- [ ] **Step 5: Commit**

```bash
git add src/lib/agent-loop.ts tests/agent-loop-suspend.test.ts
git commit -m "feat(J3): suspend the agent loop on a pending question"
```

---

### Task 7: Thread suspension and resume through the harness

**Files:**
- Modify: `src/lib/harness.ts`
- Modify: `src/lib/memory/answer.ts`
- Test: `tests/harness-suspend.test.ts`

**Interfaces:**
- Consumes: `AgentLoopResult.status/pending`, `AgentLoopInput.grantScope` (Task 6).
- Produces:
  - `RunAgentInput.grantScope?: GrantScope | null` — forwarded to the loop.
  - `RunAgentInput.resumeMessages?: LlmMessage[]` — when present, the turn is built as `[system, ...priorMessages, ...resumeMessages]` with **no new user message**; `question` is used only for the run title.
  - `RunAgentResult.status` widens to include `"suspended"`; `RunAgentResult.pending?: PendingToolCall`.
  - `AnswerWithMemoryInput.grantScope`, `AnswerWithMemoryInput.resumeMessages`; `MemoryAnswer.status` widens; `MemoryAnswer.pending?: PendingToolCall`.

  Task 8 reads `result.pending`. Task 9 passes `resumeMessages`.

**Ledger note:** a suspended run is closed with `failRun({ errorType: "awaiting_user" })`. `runs.status` has a `CHECK (status IN ('running','succeeded','failed','cancelled'))` and adding a value would mean altering a shipped constraint; `failed + awaiting_user` is the honest encoding that needs no migration, and it is the same `errorType` the suspended tool step carries (Task 4). The resumed run is a **new** run row — record that in the plan's judgment calls, and in K2 exclude `error_type = 'awaiting_user'` from failure rates.

- [ ] **Step 1: Write the failing test**

Create `tests/harness-suspend.test.ts`:

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { runAgent } from "@/lib/harness";
import { createToolRegistry } from "@/lib/tools/registry";
import { ToolSuspend } from "@/lib/approvals/suspend";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import type { GrantScope } from "@/lib/approvals/grants";
import type { LlmAdapter, LlmMessage } from "@/lib/llm/types";
import type { PermissionClass, Tool } from "@/lib/tools/types";

const SCOPE: GrantScope = { actor: DEFAULT_ACTOR, scopeKind: "session", scopeId: "1" };
const ALLOWED = new Set<PermissionClass>(["read"]);

const askTool: Tool<{ question: string }, never> = {
  name: "ask_tool",
  description: "Suspends.",
  permissionClass: "read",
  argsSchema: z.object({ question: z.string() }),
  async execute(args): Promise<never> {
    throw new ToolSuspend({
      kind: "question",
      question: args.question,
      header: null,
      options: [],
      allowText: true,
      multi: false,
      target: null,
    });
  },
};

const registry = createToolRegistry([askTool]);

let seenMessages: LlmMessage[] = [];

function adapter(turns: "suspend" | "text"): LlmAdapter {
  return {
    name: "stub",
    async *stream(request: { messages: LlmMessage[] }) {
      seenMessages = request.messages;
      if (turns === "suspend") {
        return {
          message: {
            role: "assistant" as const,
            content: [
              { type: "tool_use" as const, id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } },
            ],
          },
          stopReason: "tool_use" as const,
          usage: { inputTokens: 0, outputTokens: 0 },
        };
      }
      return {
        message: { role: "assistant" as const, content: [{ type: "text" as const, text: "Got it." }] },
        stopReason: "end" as const,
        usage: { inputTokens: 0, outputTokens: 0 },
      };
    },
  } as unknown as LlmAdapter;
}

describe("harness suspension and resume", () => {
  beforeEach(async () => {
    await runMigrations(getDb());
    seenMessages = [];
  });
  afterAll(async () => {
    await closeDb();
  });

  it("returns suspended with the pending call", async () => {
    const result = await runAgent({
      question: "draft something",
      registry,
      actor: DEFAULT_ACTOR,
      allowedClasses: ALLOWED,
      grantScope: SCOPE,
      db: getDb(),
      adapter: adapter("suspend"),
    });
    expect(result.status).toBe("suspended");
    expect(result.pending?.toolUseId).toBe("tu_1");
  });

  it("closes the suspended run as failed/awaiting_user, not silently running", async () => {
    const db = getDb();
    const result = await runAgent({
      question: "draft something",
      registry,
      actor: DEFAULT_ACTOR,
      allowedClasses: ALLOWED,
      grantScope: SCOPE,
      db,
      adapter: adapter("suspend"),
    });
    const [row] = await db<{ status: string; error_type: string }[]>`
      SELECT status, error_type FROM runs WHERE id = ${result.runId}
    `;
    expect(row.status).toBe("failed");
    expect(row.error_type).toBe("awaiting_user");
  });

  it("resumeMessages replaces the new user message entirely", async () => {
    const priorMessages: LlmMessage[] = [
      { role: "user", content: "draft something" },
      {
        role: "assistant",
        content: [{ type: "tool_use", id: "tu_1", name: "ask_tool", input: { question: "Which Sarah?" } }],
      },
    ];
    const resumeMessages: LlmMessage[] = [
      {
        role: "tool_result",
        content: [
          { toolUseId: "tu_1", toolName: "ask_tool", content: 'Success: {"answer":"Sarah Chen"}', isError: false },
        ],
      },
    ];
    await runAgent({
      question: "draft something",
      registry,
      actor: DEFAULT_ACTOR,
      allowedClasses: ALLOWED,
      grantScope: SCOPE,
      db: getDb(),
      priorMessages,
      resumeMessages,
      adapter: adapter("text"),
    });
    expect(seenMessages.map((m) => m.role)).toEqual(["system", "user", "assistant", "tool_result"]);
    // No second copy of the question — the last message is the tool_result.
    expect(seenMessages.filter((m) => m.role === "user")).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/harness-suspend.test.ts`
Expected: FAIL — `grantScope` / `resumeMessages` are not valid `RunAgentInput` fields.

- [ ] **Step 3a: Modify the harness**

In `src/lib/harness.ts`:

Add imports:

```ts
import type { PendingToolCall } from "./agent-loop";
import type { GrantScope } from "./approvals/grants";
```

Add to `RunAgentInput`:

```ts
  // Where standing grants live for this run, and whether it can ask a human
  // at all. Forwarded to the loop; see AgentLoopInput.grantScope.
  grantScope?: GrantScope | null;
  // Resume path: the messages that complete a previously suspended turn
  // (the deferred tool_result message). When present, NO new user message is
  // appended — `question` is used only for the run title, because the
  // director answered a question rather than asking a new one.
  resumeMessages?: LlmMessage[];
```

Add to `RunAgentResult`:

```ts
  status: "succeeded" | "failed" | "suspended";
  // Set only when status is "suspended".
  pending?: PendingToolCall;
```

Change the message assembly (currently lines ~104-109) to:

```ts
    const messages: LlmMessage[] = input.resumeMessages
      ? [
          { role: "system", content: input.instructions ?? DEFAULT_IDENTITY },
          ...(input.priorMessages ?? []),
          ...input.resumeMessages,
        ]
      : [
          { role: "system", content: input.instructions ?? DEFAULT_IDENTITY },
          ...conversationTurnsToMessages(input.history),
          ...(input.priorMessages ?? []),
          { role: "user", content: input.question },
        ];
```

Forward the scope into `runAgentLoop`:

```ts
      actor: input.actor,
      grantScope: input.grantScope ?? null,
```

And handle the suspended outcome, immediately after the `result.status === "succeeded"` branch:

```ts
    if (result.status === "suspended") {
      // The run stopped for a human. `failed` + errorType `awaiting_user` is
      // the ledger's encoding: runs.status has a CHECK constraint with no
      // 'suspended' value, and claiming success for a turn that produced no
      // answer would be worse than a filterable error type. The answer arrives
      // as a NEW run that continues this transcript.
      const errorMessage = `Awaiting the director's answer: ${result.pending?.request.question ?? "(unknown)"}`;
      await failRunStep(db, { runStepId: agentStep.id, errorType: "awaiting_user", errorMessage });
      await failRun(db, { runId: run.id, errorType: "awaiting_user", errorMessage });
      return {
        runId: run.id,
        status: "suspended",
        steps: result.steps,
        messages: result.messages,
        pending: result.pending,
      };
    }
```

- [ ] **Step 3b: Modify `answerWithMemory`**

In `src/lib/memory/answer.ts`:

- Add `grantScope?: GrantScope | null` and `resumeMessages?: LlmMessage[]` to `AnswerWithMemoryInput`, forwarding both to `runAgent`.
- Widen `MemoryAnswer.status` to `"succeeded" | "failed" | "suspended"` and add `pending?: PendingToolCall`.
- Add `"draft"` to `allowedClasses`, so the Gmail tool from Task 13 is reachable from chat:

```ts
    // draft joins the chat allowlist for J3: every draft-class call is gated
    // by the director's approval in executeTool, so allowing the class does
    // not allow the action.
    allowedClasses: new Set(["read", "write_internal", "enrich", "draft"]),
```

- Return `pending` through, and skip the citation post-check when `status === "suspended"` (there is no answer to check).

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/harness-suspend.test.ts tests/harness.test.ts tests/memory-answer.test.ts tests/answer.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/harness.ts src/lib/memory/answer.ts tests/harness-suspend.test.ts
git commit -m "feat(J3): thread suspension and resume through the harness"
```

---

### Task 8: Stream route persists the pending question and streams it

**Files:**
- Create: `src/lib/approvals/view.ts`
- Modify: `src/lib/ui-message-stream.ts` (add `writer.question`)
- Modify: `src/app/api/agent/stream/route.ts`
- Test: `tests/agent-stream-suspend.test.ts`

**Interfaces:**
- Consumes: `createPendingQuestion`, `getOpenPendingQuestion`, `cancelPendingQuestion` (Task 3); `MemoryAnswer.pending` (Task 7).
- Produces:
  - `src/lib/approvals/view.ts`: `interface PendingQuestionView { id: number; kind: "question" | "approval"; question: string; header: string | null; options: string[]; allowText: boolean; multi: boolean; target: string | null; toolName: string }` and `toPendingView(p: PendingQuestion): PendingQuestionView`. Shared by the SSE payload (this task), the page loader (Task 16) and the client (Task 14) so the wire shape has exactly one definition.
  - `AgentStreamWriter.question: (data: PendingQuestionView) => void`, emitting `{ type: "data-question", id: "question", data }`.

**Two behaviours this task must get right:**
1. **Persist before streaming.** The `pending_questions` row is written *before* `writer.question(...)`, so a client that reloads immediately still finds the question.
2. **A new question auto-cancels an open one.** If the director types instead of answering, the stale pending row is cancelled and a synthetic `tool_result` message is appended first, so the transcript never carries a dangling `tool_use` into the next turn. Without this the session wedges permanently: every later turn re-feeds an assistant message with an unanswered `tool_use` and both providers reject it.

- [ ] **Step 1: Write the failing test**

Create `tests/agent-stream-suspend.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { createSession, appendMessages, loadSessionMessages } from "@/lib/chat/sessions";
import { createPendingQuestion, getOpenPendingQuestion } from "@/lib/approvals/pending";
import { cancelStalePending } from "@/lib/approvals/resume";
import { toPendingView } from "@/lib/approvals/view";
import type { MemoryActor } from "@/lib/memory/actor";
import type { LlmMessage } from "@/lib/llm/types";

const ACTOR: MemoryActor = { actorType: "user", actorId: "stream-suspend-test" };

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await runMigrations(db);
}

function pendingInput(sessionId: number) {
  return {
    sessionId,
    runId: null,
    actor: ACTOR,
    kind: "question" as const,
    toolUseId: "tu_1",
    toolName: "ask_user",
    toolArgs: { question: "Which Sarah?" },
    question: "Which Sarah?",
    header: null,
    options: [],
    allowText: true,
    multi: false,
    target: null,
    insertIndex: 0,
    partialResults: [],
  };
}

describe("pending question view", () => {
  it("exposes only wire-safe fields — never partialResults or toolArgs", async () => {
    await reset();
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const pending = await createPendingQuestion(db, pendingInput(session.id));
    const view = toPendingView(pending);
    expect(view).toEqual({
      id: pending.id,
      kind: "question",
      question: "Which Sarah?",
      header: null,
      options: [],
      allowText: true,
      multi: false,
      target: null,
      toolName: "ask_user",
    });
  });
});

describe("stale pending cancellation", () => {
  beforeEach(async () => {
    await reset();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("is a no-op when nothing is open", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    await expect(cancelStalePending(db, session.id)).resolves.toBe(false);
  });

  it("cancels the row and closes the dangling tool_use with a tool_result", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const assistant: LlmMessage = {
      role: "assistant",
      content: [{ type: "tool_use", id: "tu_1", name: "ask_user", input: { question: "Which Sarah?" } }],
    };
    await appendMessages(db, session.id, [{ role: "user", content: "go" }, assistant], 1);
    await createPendingQuestion(db, pendingInput(session.id));

    expect(await cancelStalePending(db, session.id)).toBe(true);
    expect(await getOpenPendingQuestion(db, session.id)).toBeNull();

    const messages = await loadSessionMessages(db, session.id);
    const last = messages.at(-1)!;
    expect(last.role).toBe("tool_result");
    expect(last.content).toHaveLength(1);
    expect(last.content[0].toolUseId).toBe("tu_1");
    expect(last.content[0].isError).toBe(true);
    expect(last.content[0].content).toContain("cancelled");
  });

  it("answers every tool_use id in the assistant message, not only the suspended one", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const assistant: LlmMessage = {
      role: "assistant",
      content: [
        { type: "tool_use", id: "tu_0", name: "search_memory", input: {} },
        { type: "tool_use", id: "tu_1", name: "ask_user", input: { question: "Which Sarah?" } },
      ],
    };
    await appendMessages(db, session.id, [{ role: "user", content: "go" }, assistant], 1);
    await createPendingQuestion(db, {
      ...pendingInput(session.id),
      insertIndex: 1,
      partialResults: [
        { toolUseId: "tu_0", toolName: "search_memory", content: "Success: {}", isError: false },
      ],
    });

    await cancelStalePending(db, session.id);
    const last = (await loadSessionMessages(db, session.id)).at(-1)!;
    expect(last.content.map((b: { toolUseId: string }) => b.toolUseId)).toEqual(["tu_0", "tu_1"]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/agent-stream-suspend.test.ts`
Expected: FAIL — cannot resolve `@/lib/approvals/view`.

- [ ] **Step 3a: Write the view type**

Create `src/lib/approvals/view.ts`:

```ts
import type { PendingQuestion } from "./pending";

// The wire shape of a pending question: what the SSE part, the page loader and
// the client all agree on. Deliberately omits partialResults, toolArgs and the
// session/run ids — the browser never needs them, and toolArgs can carry a
// draft body the card has no reason to echo back.
export interface PendingQuestionView {
  id: number;
  kind: "question" | "approval";
  question: string;
  header: string | null;
  options: string[];
  allowText: boolean;
  multi: boolean;
  target: string | null;
  toolName: string;
}

export function toPendingView(pending: PendingQuestion): PendingQuestionView {
  return {
    id: pending.id,
    kind: pending.kind,
    question: pending.question,
    header: pending.header,
    options: pending.options,
    allowText: pending.allowText,
    multi: pending.multi,
    target: pending.target,
    toolName: pending.toolName,
  };
}
```

- [ ] **Step 3b: Write `cancelStalePending`**

Create `src/lib/approvals/resume.ts` (Task 9 adds `buildResumeMessage` to this same file):

```ts
import { appendMessages } from "../chat/sessions";
import type { LlmToolResultBlock } from "../llm/types";
import type { Sql } from "../tools/types";
import { cancelPendingQuestion, getOpenPendingQuestion } from "./pending";

// A director who types a new question instead of answering abandons the open
// one. The suspended tool_use is still unanswered in chat_messages, and every
// later turn re-feeds that transcript — providers reject an assistant message
// whose tool_use has no paired tool_result, so the session would be wedged
// permanently. Closing it with a synthetic cancelled result keeps the
// transcript valid and is honest about what happened.
export async function cancelStalePending(db: Sql, sessionId: number): Promise<boolean> {
  const pending = await getOpenPendingQuestion(db, sessionId);
  if (!pending) return false;

  const cancelled: LlmToolResultBlock = {
    toolUseId: pending.toolUseId,
    toolName: pending.toolName,
    content: "Error (cancelled): the director asked something else instead of answering. This action did not run.",
    isError: true,
  };
  const blocks = [...pending.partialResults];
  blocks.splice(pending.insertIndex, 0, cancelled);

  await appendMessages(db, sessionId, [{ role: "tool_result", content: blocks }], pending.runId ?? undefined);
  await cancelPendingQuestion(db, pending.id);
  return true;
}
```

- [ ] **Step 3c: Add the writer part**

In `src/lib/ui-message-stream.ts`, add to `AgentStreamWriter`:

```ts
  // A run suspended for the director. Terminal for this stream — nothing
  // follows it but `meta`.
  question: (data: PendingQuestionView) => void;
```

and to the object passed to `run(...)`:

```ts
        question: (data) => writer.write({ type: "data-question", id: "question", data }),
```

Import the type: `import type { PendingQuestionView } from "./approvals/view";`

- [ ] **Step 3d: Wire the route**

In `src/app/api/agent/stream/route.ts`:

After `const session = await getOrCreateLatestSession(db, actor);` and **before** `loadSessionMessages`, cancel any stale pending:

```ts
  // The director typed instead of answering: close the abandoned question so
  // the transcript we are about to load has no unanswered tool_use in it.
  await cancelStalePending(db, session.id);
  const priorMessages = await loadSessionMessages(db, session.id);
```

Pass the grant scope into `answerWithMemory`:

```ts
      grantScope: { actor, scopeKind: "session", scopeId: String(session.id) },
```

After `await appendMessages(db, session.id, producedMessages, result.runId);`, and before the existing answer-flush branch, handle the suspended case:

```ts
    if (result.status === "suspended" && result.pending) {
      // Persisted BEFORE it is streamed: a client that reloads the instant the
      // stream closes must still find the question waiting.
      const pending = await createPendingQuestion(db, {
        sessionId: session.id,
        runId: result.runId,
        actor,
        kind: result.pending.request.kind,
        toolUseId: result.pending.toolUseId,
        toolName: result.pending.toolName,
        toolArgs: result.pending.toolArgs,
        question: result.pending.request.question,
        header: result.pending.request.header,
        options: result.pending.request.options,
        allowText: result.pending.request.allowText,
        multi: result.pending.request.multi,
        target: result.pending.request.target,
        insertIndex: result.pending.insertIndex,
        partialResults: result.pending.partialResults,
      });
      writer.answerEnd();
      writer.question(toPendingView(pending));
      writer.meta({ runId: result.runId, status: result.status, steps: result.steps, invalidCitations: [] });
      return;
    }
```

Note `producedMessages` for a suspended run ends at the assistant message with the unanswered `tool_use` — that is correct and intended; Task 9 appends the completing `tool_result` message.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/agent-stream-suspend.test.ts tests/agent-stream-route.test.ts tests/chat-stream.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/approvals/view.ts src/lib/approvals/resume.ts src/lib/ui-message-stream.ts src/app/api/agent/stream/route.ts tests/agent-stream-suspend.test.ts
git commit -m "feat(J3): persist and stream a suspended run's pending question"
```

---

### Task 9: The answer route — resolve, grant, execute, resume

**Files:**
- Modify: `src/lib/approvals/resume.ts` (add `resolveAndBuildResumeMessage`)
- Create: `src/app/api/agent/answer/route.ts`
- Modify: `src/middleware.ts` (confirm the matcher already covers `/api/agent/answer` — it should, since it is deny-by-default with anchored exclusions; add a test rather than a change)
- Test: `tests/approvals-answer.test.ts`

**Interfaces:**
- Consumes: `getOpenPendingQuestionById`, `resolvePendingQuestion` (Task 3); `grantEntries`, `insertGrant`, `grantParts` (Task 2); `executeTool`, `targetArgFor` (Task 4); `answerWithMemory` with `resumeMessages` (Task 7); `toPendingView` (Task 8).
- Produces:
  - `resolveAndBuildResumeMessage(db, input: ResolveInput): Promise<{ message: LlmToolResultMessage } | { error: string }>` in `src/lib/approvals/resume.ts`, where
    `ResolveInput = { pending: PendingQuestion; actor: MemoryActor; answer: string; approved: boolean; always: boolean; registry: ToolRegistry; grantScope: GrantScope }`
  - `POST /api/agent/answer` accepting `{ pendingId: number; answer?: string; approved?: boolean; always?: boolean }` and returning the same SSE stream shape as `/api/agent/stream`.

**Ordering rule (security-relevant):** `resolvePendingQuestion` is the compare-and-set that runs **before** the tool executes. A double-clicked "Approve" must not create two Gmail drafts; the second request loses the CAS and gets a 409.

**Grant rule:** "always" mints a grant only if `grantEntries` accepts it. It is called with the real registry lookup so the validator and the gate agree on what is grantable; if it returns `[]`, the approval still proceeds as a one-shot yes and the response says so.

- [ ] **Step 1: Write the failing test**

Create `tests/approvals-answer.test.ts`:

```ts
import { z } from "zod";
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { createSession } from "@/lib/chat/sessions";
import { createToolRegistry } from "@/lib/tools/registry";
import { createPendingQuestion, getOpenPendingQuestionById, resolvePendingQuestion } from "@/lib/approvals/pending";
import { resolveAndBuildResumeMessage } from "@/lib/approvals/resume";
import { hasGrant, type GrantScope } from "@/lib/approvals/grants";
import type { MemoryActor } from "@/lib/memory/actor";
import type { Tool } from "@/lib/tools/types";

const ACTOR: MemoryActor = { actorType: "user", actorId: "answer-test" };

let sent: string[] = [];

const draftTool: Tool<{ to: string; body: string }, { draftId: string }> = {
  name: "draft_email",
  description: "Draft an email.",
  permissionClass: "draft",
  targetArg: "to",
  argsSchema: z.object({ to: z.string(), body: z.string() }),
  async execute(args) {
    sent.push(args.to);
    return { draftId: `d_${sent.length}` };
  },
};

const registry = createToolRegistry([draftTool]);

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await runMigrations(db);
  sent = [];
}

async function makePending(kind: "question" | "approval", overrides = {}) {
  const db = getDb();
  const session = await createSession(db, ACTOR);
  const pending = await createPendingQuestion(db, {
    sessionId: session.id,
    runId: null,
    actor: ACTOR,
    kind,
    toolUseId: "tu_1",
    toolName: kind === "approval" ? "draft_email" : "ask_user",
    toolArgs: kind === "approval" ? { to: "sarah@acme.com", body: "hi" } : { question: "Which Sarah?" },
    question: kind === "approval" ? "Allow draft_email for sarah@acme.com?" : "Which Sarah?",
    header: null,
    options: [],
    allowText: true,
    multi: false,
    target: kind === "approval" ? "sarah@acme.com" : null,
    insertIndex: 0,
    partialResults: [],
    ...overrides,
  });
  const scope: GrantScope = { actor: ACTOR, scopeKind: "session", scopeId: String(session.id) };
  return { session, pending, scope };
}

describe("resolving a pending question", () => {
  beforeEach(async () => {
    await reset();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("returns the answer as the suspended call's tool_result", async () => {
    const { pending, scope } = await makePending("question");
    const out = await resolveAndBuildResumeMessage(getDb(), {
      pending,
      actor: ACTOR,
      answer: "Sarah Chen",
      approved: true,
      always: false,
      registry,
      grantScope: scope,
    });
    expect("message" in out).toBe(true);
    const block = (out as { message: { content: { toolUseId: string; content: string }[] } }).message.content[0];
    expect(block.toolUseId).toBe("tu_1");
    expect(block.content).toContain("Sarah Chen");
  });

  it("closes the question before anything else runs, so it cannot be replayed", async () => {
    const { pending, scope } = await makePending("question");
    const args = { pending, actor: ACTOR, answer: "Sarah Chen", approved: true, always: false, registry, grantScope: scope };
    await resolveAndBuildResumeMessage(getDb(), args);
    const second = await resolveAndBuildResumeMessage(getDb(), args);
    expect("error" in second).toBe(true);
    expect(await getOpenPendingQuestionById(getDb(), pending.id)).toBeNull();
  });

  it("an approved approval executes the tool exactly once", async () => {
    const { pending, scope } = await makePending("approval");
    const out = await resolveAndBuildResumeMessage(getDb(), {
      pending, actor: ACTOR, answer: "approve", approved: true, always: false, registry, grantScope: scope,
    });
    expect(sent).toEqual(["sarah@acme.com"]);
    const block = (out as { message: { content: { content: string; isError: boolean }[] } }).message.content[0];
    expect(block.isError).toBe(false);
    expect(block.content).toContain("draftId");
  });

  it("a one-shot approval leaves no grant behind", async () => {
    const { pending, scope } = await makePending("approval");
    await resolveAndBuildResumeMessage(getDb(), {
      pending, actor: ACTOR, answer: "approve", approved: true, always: false, registry, grantScope: scope,
    });
    expect(await hasGrant(getDb(), scope, "draft_email", "sarah@acme.com")).toBe(false);
  });

  it("always: true mints a grant for that exact target", async () => {
    const { pending, scope } = await makePending("approval");
    await resolveAndBuildResumeMessage(getDb(), {
      pending, actor: ACTOR, answer: "approve", approved: true, always: true, registry, grantScope: scope,
    });
    expect(await hasGrant(getDb(), scope, "draft_email", "sarah@acme.com")).toBe(true);
    expect(await hasGrant(getDb(), scope, "draft_email", "mike@othercorp.com")).toBe(false);
  });

  it("always on an ungrantable tool degrades to a one-shot yes, never a broad grant", async () => {
    // ask_user declares no targetArg, so grantEntries rejects it.
    const { pending, scope } = await makePending("question");
    await resolveAndBuildResumeMessage(getDb(), {
      pending, actor: ACTOR, answer: "Sarah Chen", approved: true, always: true, registry, grantScope: scope,
    });
    const rows = await getDb()`SELECT id FROM grants`;
    expect(rows).toHaveLength(0);
  });

  it("a denied approval does not execute the tool", async () => {
    const { pending, scope } = await makePending("approval");
    const out = await resolveAndBuildResumeMessage(getDb(), {
      pending, actor: ACTOR, answer: "deny", approved: false, always: true, registry, grantScope: scope,
    });
    expect(sent).toEqual([]);
    const block = (out as { message: { content: { content: string; isError: boolean }[] } }).message.content[0];
    expect(block.isError).toBe(true);
    expect(block.content).toContain("denied");
    expect(await hasGrant(getDb(), scope, "draft_email", "sarah@acme.com")).toBe(false);
  });

  it("slots the resumed result back at its original index", async () => {
    const { pending, scope } = await makePending("question", {
      insertIndex: 1,
      partialResults: [
        { toolUseId: "tu_0", toolName: "search_memory", content: "Success: {}", isError: false },
      ],
    });
    const out = await resolveAndBuildResumeMessage(getDb(), {
      pending, actor: ACTOR, answer: "Sarah Chen", approved: true, always: false, registry, grantScope: scope,
    });
    const ids = (out as { message: { content: { toolUseId: string }[] } }).message.content.map((b) => b.toolUseId);
    expect(ids).toEqual(["tu_0", "tu_1"]);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/approvals-answer.test.ts`
Expected: FAIL — `resolveAndBuildResumeMessage` is not exported.

- [ ] **Step 3a: Implement the resolver**

Append to `src/lib/approvals/resume.ts`:

```ts
import { finishRunStep, startRun, startRunStep } from "../ledger";
import { executeTool, targetArgFor } from "../tools/orchestrator";
import type { ToolRegistry } from "../tools/registry";
import type { LlmToolResultMessage } from "../llm/types";
import type { MemoryActor } from "../memory/actor";
import { grantEntries, insertGrant, type GrantScope } from "./grants";
import { getOpenPendingQuestionById, resolvePendingQuestion, type PendingQuestion } from "./pending";

export interface ResolveInput {
  pending: PendingQuestion;
  actor: MemoryActor;
  answer: string;
  approved: boolean;
  always: boolean;
  registry: ToolRegistry;
  grantScope: GrantScope;
}

export type ResolveOutcome = { message: LlmToolResultMessage } | { error: string };

// Turns the director's answer into the tool_result message that completes the
// suspended turn. Order matters and is security-relevant:
//   1. close the question (compare-and-set) — a double-clicked Approve must not
//      produce two Gmail drafts; the loser of the CAS gets an error, not a run.
//   2. mint the grant, if asked and if grantEntries allows it.
//   3. execute (approval + approved) or synthesise the result (question/denied).
export async function resolveAndBuildResumeMessage(
  db: Sql,
  input: ResolveInput
): Promise<ResolveOutcome> {
  const { pending } = input;

  const claimed = await resolvePendingQuestion(db, pending.id, input.answer);
  if (!claimed) return { error: "This question has already been answered." };

  if (input.always && input.approved && pending.target) {
    // Validated through the same fail-closed path openworker uses, with the
    // registry as the target-argument authority, so the validator and the gate
    // can never disagree about what is grantable. An empty result is not an
    // error: the approval simply stays one-shot.
    const entries = grantEntries(
      [{ tool: pending.toolName, target: pending.target, access: "write" }],
      (tool) => targetArgFor(input.registry, tool)
    );
    for (const entry of entries) {
      const space = entry.indexOf(" ");
      await insertGrant(db, input.grantScope, entry.slice(0, space), entry.slice(space + 1));
    }
  }

  const content = await buildResultContent(db, input);
  const blocks = [...pending.partialResults];
  blocks.splice(pending.insertIndex, 0, {
    toolUseId: pending.toolUseId,
    toolName: pending.toolName,
    content: content.text,
    isError: content.isError,
  });

  return { message: { role: "tool_result", content: blocks } };
}

async function buildResultContent(
  db: Sql,
  input: ResolveInput
): Promise<{ text: string; isError: boolean }> {
  const { pending } = input;

  if (pending.kind === "question") {
    // Matches ask.py's documented return shape: {"answer": "..."}.
    return { text: `Success: ${JSON.stringify({ answer: input.answer })}`, isError: false };
  }

  if (!input.approved) {
    return {
      text: `Error (denied): the director declined ${pending.toolName} for ${pending.target ?? "this target"}. Do not retry it; ask what they would prefer instead.`,
      isError: true,
    };
  }

  // Approved: run the tool from the args the director actually saw, not from
  // anything the model restates. The run step hangs off the original run so
  // the trace shows the approval and its effect in one place.
  const runId = pending.runId ?? (await startRun(db, {
    runType: "agent_chat",
    title: `approved ${pending.toolName}`,
    input: { pendingId: pending.id },
    actor: input.actor,
  })).id;
  const step = await startRunStep(db, {
    runId,
    parentStepId: null,
    stepKind: "agent",
    name: "approved_tool_call",
    input: { pendingId: pending.id, tool: pending.toolName, target: pending.target },
  });

  const result = await executeTool({
    toolUseId: pending.toolUseId,
    name: pending.toolName,
    input: pending.toolArgs,
    registry: input.registry,
    // The class gate still applies; only the grant lookup is overridden.
    allowed: new Set(["read", "enrich", "reason", "draft", "write_internal", "admin"]),
    db,
    runId,
    parentStepId: step.id,
    actor: input.actor,
    grantScope: input.grantScope,
    grantOverride: true,
  });
  await finishRunStep(db, { runStepId: step.id, output: { isError: result.isError } });

  return { text: result.content, isError: result.isError };
}
```

Add `import type { Sql } from "../tools/types";` if not already present, and re-export `getOpenPendingQuestionById` usage as needed.

- [ ] **Step 3b: Write the route**

Create `src/app/api/agent/answer/route.ts`:

```ts
import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { requireActor } from "@/lib/auth/actor";
import { appendMessages, loadSessionMessages } from "@/lib/chat/sessions";
import { getOpenPendingQuestionById } from "@/lib/approvals/pending";
import { resolveAndBuildResumeMessage } from "@/lib/approvals/resume";
import { toPendingView } from "@/lib/approvals/view";
import { createPendingQuestion } from "@/lib/approvals/pending";
import { memoryRegistry } from "@/lib/memory/answer-config";
import { answerWithMemory, summarizeStep } from "@/lib/memory/answer";
import { streamAgentResponse } from "@/lib/ui-message-stream";

// The other half of a suspended run: the director's answer arrives here, the
// deferred tool_result message is written, and the loop re-enters as a new run
// continuing the same session transcript.
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as {
    pendingId?: unknown;
    answer?: unknown;
    approved?: unknown;
    always?: unknown;
  } | null;

  const pendingId = Number(body?.pendingId);
  if (!Number.isInteger(pendingId) || pendingId <= 0) {
    return NextResponse.json({ error: "pendingId is required" }, { status: 400 });
  }
  const answer = typeof body?.answer === "string" ? body.answer : "";
  const approved = body?.approved !== false; // default yes for plain questions
  const always = body?.always === true;

  const db = getDb();
  const actor = await requireActor();
  const pending = await getOpenPendingQuestionById(db, pendingId);

  // 404 rather than 403 for someone else's question: the existence of a
  // pending id is itself information (same call H1 made for /runs/[id]).
  if (!pending || pending.actor.actorId !== actor.actorId || pending.actor.actorType !== actor.actorType) {
    return NextResponse.json({ error: "No open question with that id." }, { status: 404 });
  }
  if (pending.kind === "question" && !answer.trim()) {
    return NextResponse.json({ error: "answer is required" }, { status: 400 });
  }

  const registry = memoryRegistry();
  const grantScope = { actor, scopeKind: "session" as const, scopeId: String(pending.sessionId) };
  const outcome = await resolveAndBuildResumeMessage(db, {
    pending,
    actor,
    answer: answer || (approved ? "approve" : "deny"),
    approved,
    always,
    registry,
    grantScope,
  });
  if ("error" in outcome) {
    return NextResponse.json({ error: outcome.error }, { status: 409 });
  }

  // Persist the completing tool_result BEFORE the resumed run, so the
  // transcript is provider-valid again even if the run below fails.
  await appendMessages(db, pending.sessionId, [outcome.message], pending.runId ?? undefined);
  const priorMessages = await loadSessionMessages(db, pending.sessionId);

  return streamAgentResponse(async (writer) => {
    let searchCalledSoFar = false;
    let streamedLive = false;

    const result = await answerWithMemory(db, {
      // Title only — resumeMessages means no new user message is appended.
      question: pending.question,
      actor,
      grantScope,
      // priorMessages already ends with the completing tool_result message, so
      // resumeMessages is empty: the transcript is whole and the model simply
      // continues from it.
      resumeMessages: [],
      priorMessages,
      signal: request.signal,
      onStep: (event) => {
        writer.step(`step-${event.index}`, summarizeStep(event));
        if (event.tool === "search_memory") searchCalledSoFar = true;
      },
      onAgentLoopEvent: (event) => {
        if (event.type === "tool_start") {
          writer.toolPending(event.name);
          if (event.name === "search_memory") searchCalledSoFar = true;
        } else if (event.type === "llm" && event.event.type === "text_delta" && !searchCalledSoFar) {
          writer.answerDelta(event.event.delta);
          streamedLive = true;
        }
      },
    });

    const producedMessages = result.messages.slice(priorMessages.length + 1); // + system
    await appendMessages(db, pending.sessionId, producedMessages, result.runId);

    if (result.status === "suspended" && result.pending) {
      const next = await createPendingQuestion(db, {
        sessionId: pending.sessionId,
        runId: result.runId,
        actor,
        kind: result.pending.request.kind,
        toolUseId: result.pending.toolUseId,
        toolName: result.pending.toolName,
        toolArgs: result.pending.toolArgs,
        question: result.pending.request.question,
        header: result.pending.request.header,
        options: result.pending.request.options,
        allowText: result.pending.request.allowText,
        multi: result.pending.request.multi,
        target: result.pending.request.target,
        insertIndex: result.pending.insertIndex,
        partialResults: result.pending.partialResults,
      });
      writer.answerEnd();
      writer.question(toPendingView(next));
    } else if (streamedLive && !searchCalledSoFar) {
      writer.answerEnd();
    } else if (result.answer) {
      writer.answerFlush(result.answer);
    } else {
      writer.answerEnd();
    }

    writer.meta({
      runId: result.runId,
      status: result.status,
      steps: result.steps,
      invalidCitations: result.invalidCitations,
    });
  });
}
```

**Note on `resumeMessages: []`:** an empty array is still "present", which is exactly what selects the no-new-user-message branch in Task 7's harness change. Keep it explicit rather than clever — a comment in the harness should say so.

- [ ] **Step 3c: Cover the middleware**

Add to `tests/auth-middleware-matcher.test.ts` a case asserting `/api/agent/answer` **is** matched (protected). No middleware change should be needed; if the test fails, the matcher is wrong and that is the bug.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/approvals-answer.test.ts tests/auth-middleware-matcher.test.ts`
Expected: PASS (8 + existing).

- [ ] **Step 5: Commit**

```bash
git add src/lib/approvals/resume.ts src/app/api/agent/answer/route.ts tests/approvals-answer.test.ts tests/auth-middleware-matcher.test.ts
git commit -m "feat(J3): resume a suspended run from the director's answer"
```

---

### Task 10: OAuth token storage, encrypted at rest

**Files:**
- Create: `src/migrations/009_oauth_tokens.sql`
- Create: `src/lib/auth/crypto.ts`
- Create: `src/lib/auth/tokens.ts`
- Modify: `.env.example`
- Test: `tests/auth-token-crypto.test.ts`, `tests/auth-tokens.test.ts`

**Interfaces:**
- Consumes: `users(id)` from `007_users_auth.sql`.
- Produces:
  - `encryptSecret(plaintext: string): string` / `decryptSecret(ciphertext: string): string` in `crypto.ts` — AES-256-GCM, format `v1.<iv>.<tag>.<ct>` (base64url parts).
  - `saveGoogleTokens(db, input: { userId: number; accessToken: string; refreshToken?: string | null; scope: string; expiresAt: Date | null }): Promise<void>`
  - `getStoredGoogleTokens(db, userId: number): Promise<StoredTokens | null>`
  - `interface StoredTokens { accessToken: string; refreshToken: string | null; scope: string; expiresAt: Date | null }`

  Task 11 calls `saveGoogleTokens`; Task 12 calls `getStoredGoogleTokens`.

**Secret handling:** `TOKEN_ENCRYPTION_KEY` is a 32-byte key, base64. Generate with `openssl rand -base64 32`. **Never print `.env.local`, and never echo the key** — verify with `[ -n "$TOKEN_ENCRYPTION_KEY" ] && echo set`. `encryptSecret` throws when the key is absent or the wrong length: a missing key must fail loudly at write time, not silently store plaintext.

- [ ] **Step 1: Write the failing tests**

Create `tests/auth-token-crypto.test.ts`:

```ts
import { decryptSecret, encryptSecret } from "@/lib/auth/crypto";

const KEY = "8fZ1kq9pXn2vT7yR4wB6sL0mJ3hG5dC1aE8uQ2iO7xY=" ; // 32 bytes, base64

describe("secret encryption", () => {
  const original = process.env.TOKEN_ENCRYPTION_KEY;
  beforeEach(() => {
    process.env.TOKEN_ENCRYPTION_KEY = KEY;
  });
  afterAll(() => {
    process.env.TOKEN_ENCRYPTION_KEY = original;
  });

  it("round-trips a value", () => {
    expect(decryptSecret(encryptSecret("ya29.a0-secret"))).toBe("ya29.a0-secret");
  });

  it("never emits the plaintext", () => {
    expect(encryptSecret("ya29.a0-secret")).not.toContain("ya29");
  });

  it("is non-deterministic — a fresh IV per call", () => {
    expect(encryptSecret("same")).not.toBe(encryptSecret("same"));
  });

  it("is tagged with its format version", () => {
    expect(encryptSecret("x").startsWith("v1.")).toBe(true);
  });

  it("rejects a tampered ciphertext rather than returning garbage", () => {
    const enc = encryptSecret("ya29.a0-secret");
    const parts = enc.split(".");
    parts[3] = Buffer.from("tampered").toString("base64url");
    expect(() => decryptSecret(parts.join("."))).toThrow();
  });

  it("throws when no key is configured", () => {
    delete process.env.TOKEN_ENCRYPTION_KEY;
    expect(() => encryptSecret("x")).toThrow(/TOKEN_ENCRYPTION_KEY/);
  });

  it("throws on a key of the wrong length", () => {
    process.env.TOKEN_ENCRYPTION_KEY = Buffer.from("too-short").toString("base64");
    expect(() => encryptSecret("x")).toThrow(/32 bytes/);
  });
});
```

Create `tests/auth-tokens.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { upsertUser } from "@/lib/auth/users";
import { getStoredGoogleTokens, saveGoogleTokens } from "@/lib/auth/tokens";

const KEY = "8fZ1kq9pXn2vT7yR4wB6sL0mJ3hG5dC1aE8uQ2iO7xY=";

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS oauth_tokens CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '009_oauth_tokens.sql'`;
  await runMigrations(db);
}

describe("google token storage", () => {
  const original = process.env.TOKEN_ENCRYPTION_KEY;
  beforeEach(async () => {
    process.env.TOKEN_ENCRYPTION_KEY = KEY;
    await reset();
  });
  afterAll(async () => {
    process.env.TOKEN_ENCRYPTION_KEY = original;
    await closeDb();
  });

  it("round-trips tokens for a user", async () => {
    const db = getDb();
    const user = await upsertUser(db, { googleSub: "sub-1", email: "a@codeology.org", name: null, imageUrl: null });
    const expiresAt = new Date(Date.now() + 3600_000);
    await saveGoogleTokens(db, {
      userId: user.id,
      accessToken: "ya29.access",
      refreshToken: "1//refresh",
      scope: "openid email https://www.googleapis.com/auth/gmail.compose",
      expiresAt,
    });
    const stored = await getStoredGoogleTokens(db, user.id);
    expect(stored!.accessToken).toBe("ya29.access");
    expect(stored!.refreshToken).toBe("1//refresh");
    expect(stored!.scope).toContain("gmail.compose");
    expect(stored!.expiresAt!.getTime()).toBeCloseTo(expiresAt.getTime(), -3);
  });

  it("stores nothing readable in the clear", async () => {
    const db = getDb();
    const user = await upsertUser(db, { googleSub: "sub-2", email: "b@codeology.org", name: null, imageUrl: null });
    await saveGoogleTokens(db, {
      userId: user.id, accessToken: "ya29.access", refreshToken: "1//refresh", scope: "", expiresAt: null,
    });
    const [row] = await db<{ access_token_enc: string; refresh_token_enc: string }[]>`
      SELECT access_token_enc, refresh_token_enc FROM oauth_tokens WHERE user_id = ${user.id}
    `;
    expect(row.access_token_enc).not.toContain("ya29");
    expect(row.refresh_token_enc).not.toContain("1//");
  });

  it("a re-consent replaces the row rather than adding one", async () => {
    const db = getDb();
    const user = await upsertUser(db, { googleSub: "sub-3", email: "c@codeology.org", name: null, imageUrl: null });
    await saveGoogleTokens(db, { userId: user.id, accessToken: "first", refreshToken: "r1", scope: "", expiresAt: null });
    await saveGoogleTokens(db, { userId: user.id, accessToken: "second", refreshToken: "r2", scope: "", expiresAt: null });
    const rows = await db`SELECT id FROM oauth_tokens WHERE user_id = ${user.id}`;
    expect(rows).toHaveLength(1);
    expect((await getStoredGoogleTokens(db, user.id))!.accessToken).toBe("second");
  });

  it("keeps the existing refresh token when a refresh response omits one", async () => {
    // Google returns refresh_token only on the first consent; a plain refresh
    // response has none. Overwriting with null would silently end the
    // director's offline access until they re-consent.
    const db = getDb();
    const user = await upsertUser(db, { googleSub: "sub-4", email: "d@codeology.org", name: null, imageUrl: null });
    await saveGoogleTokens(db, { userId: user.id, accessToken: "first", refreshToken: "keep-me", scope: "", expiresAt: null });
    await saveGoogleTokens(db, { userId: user.id, accessToken: "second", refreshToken: null, scope: "", expiresAt: null });
    expect((await getStoredGoogleTokens(db, user.id))!.refreshToken).toBe("keep-me");
  });

  it("returns null for a user with no tokens", async () => {
    expect(await getStoredGoogleTokens(getDb(), 999_999)).toBeNull();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npx vitest run tests/auth-token-crypto.test.ts tests/auth-tokens.test.ts`
Expected: FAIL — cannot resolve `@/lib/auth/crypto`.

- [ ] **Step 3a: Write the migration**

Create `src/migrations/009_oauth_tokens.sql`:

```sql
-- 009_oauth_tokens.sql — J3 Gmail drafts need offline access.
--
-- H1 deliberately shipped JWT sessions with no adapter and no token storage:
-- login-only scopes need nothing more than the id token. Creating a Gmail
-- draft does, so the access/refresh pair has to outlive the request.
--
-- Both tokens are stored encrypted (AES-256-GCM, src/lib/auth/crypto.ts).
-- A database dump is not a Gmail credential.

CREATE TABLE IF NOT EXISTS oauth_tokens (
  id                BIGSERIAL PRIMARY KEY,
  user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider          TEXT NOT NULL DEFAULT 'google',
  access_token_enc  TEXT NOT NULL,
  refresh_token_enc TEXT,
  scope             TEXT NOT NULL DEFAULT '',
  expires_at        TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS oauth_tokens_user_provider
  ON oauth_tokens(user_id, provider);
```

- [ ] **Step 3b: Write the crypto helper**

Create `src/lib/auth/crypto.ts`:

```ts
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

// AES-256-GCM at rest for OAuth tokens. Format: v1.<iv>.<tag>.<ciphertext>,
// each part base64url. GCM (not CBC) so a tampered ciphertext throws on the
// auth tag instead of decrypting to garbage a caller might send to Google.
const VERSION = "v1";

function key(): Buffer {
  const raw = process.env.TOKEN_ENCRYPTION_KEY;
  // Fail loudly rather than degrading to plaintext: a missing key must stop
  // the write, not quietly weaken it.
  if (!raw) throw new Error("TOKEN_ENCRYPTION_KEY is not set; refusing to store a token.");
  const buf = Buffer.from(raw, "base64");
  if (buf.length !== 32) {
    throw new Error("TOKEN_ENCRYPTION_KEY must decode to 32 bytes (openssl rand -base64 32).");
  }
  return buf;
}

export function encryptSecret(plaintext: string): string {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key(), iv);
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  return [VERSION, iv.toString("base64url"), cipher.getAuthTag().toString("base64url"), ct.toString("base64url")].join(".");
}

export function decryptSecret(ciphertext: string): string {
  const [version, iv, tag, ct] = ciphertext.split(".");
  if (version !== VERSION || !iv || !tag || !ct) {
    throw new Error("Unrecognised encrypted-secret format.");
  }
  const decipher = createDecipheriv("aes-256-gcm", key(), Buffer.from(iv, "base64url"));
  decipher.setAuthTag(Buffer.from(tag, "base64url"));
  return Buffer.concat([decipher.update(Buffer.from(ct, "base64url")), decipher.final()]).toString("utf8");
}
```

- [ ] **Step 3c: Write the token store**

Create `src/lib/auth/tokens.ts`:

```ts
import type { Sql } from "../tools/types";
import { decryptSecret, encryptSecret } from "./crypto";

export interface StoredTokens {
  accessToken: string;
  refreshToken: string | null;
  scope: string;
  expiresAt: Date | null;
}

export interface SaveTokensInput {
  userId: number;
  accessToken: string;
  refreshToken?: string | null;
  scope: string;
  expiresAt: Date | null;
}

export async function saveGoogleTokens(db: Sql, input: SaveTokensInput): Promise<void> {
  const refreshEnc = input.refreshToken ? encryptSecret(input.refreshToken) : null;
  await db`
    INSERT INTO oauth_tokens (user_id, provider, access_token_enc, refresh_token_enc, scope, expires_at)
    VALUES (${input.userId}, 'google', ${encryptSecret(input.accessToken)}, ${refreshEnc},
            ${input.scope}, ${input.expiresAt})
    ON CONFLICT (user_id, provider) DO UPDATE SET
      access_token_enc  = EXCLUDED.access_token_enc,
      -- Google returns refresh_token only on first consent; a refresh response
      -- omits it. COALESCE keeps the one we already have instead of wiping
      -- offline access on every refresh.
      refresh_token_enc = COALESCE(EXCLUDED.refresh_token_enc, oauth_tokens.refresh_token_enc),
      scope             = EXCLUDED.scope,
      expires_at        = EXCLUDED.expires_at,
      updated_at        = now()
  `;
}

export async function getStoredGoogleTokens(db: Sql, userId: number): Promise<StoredTokens | null> {
  const [row] = await db<
    { access_token_enc: string; refresh_token_enc: string | null; scope: string; expires_at: Date | null }[]
  >`
    SELECT access_token_enc, refresh_token_enc, scope, expires_at
    FROM oauth_tokens WHERE user_id = ${userId} AND provider = 'google'
  `;
  if (!row) return null;
  return {
    accessToken: decryptSecret(row.access_token_enc),
    refreshToken: row.refresh_token_enc ? decryptSecret(row.refresh_token_enc) : null,
    scope: row.scope,
    expiresAt: row.expires_at,
  };
}
```

- [ ] **Step 3d: Document the env var**

Add to `.env.example`:

```
# 32-byte key for encrypting stored OAuth tokens at rest (J3).
# Generate with: openssl rand -base64 32
TOKEN_ENCRYPTION_KEY=
```

Set a real value in `.env.local` without printing it:

```bash
printf 'TOKEN_ENCRYPTION_KEY=%s\n' "$(openssl rand -base64 32)" >> .env.local
grep -c '^TOKEN_ENCRYPTION_KEY=' .env.local   # expect 1
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/auth-token-crypto.test.ts tests/auth-tokens.test.ts`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add src/migrations/009_oauth_tokens.sql src/lib/auth/crypto.ts src/lib/auth/tokens.ts .env.example tests/auth-token-crypto.test.ts tests/auth-tokens.test.ts
git commit -m "feat(J3): store Google OAuth tokens encrypted at rest"
```

---

### Task 11: Widen the OAuth scope and persist tokens on sign-in

**Files:**
- Modify: `src/auth.config.ts`
- Modify: `src/auth.ts`
- Modify: `.env.example` (document the re-consent)
- Test: `tests/auth-gmail-scope.test.ts`

**Interfaces:**
- Consumes: `saveGoogleTokens` (Task 10), `upsertUser` (H1).
- Produces: the Google provider requests `gmail.compose` with `access_type: "offline"` and `prompt: "consent"`; the `jwt` callback persists `account` tokens on sign-in.

**Operational notes for whoever runs this:**
- The Google Cloud OAuth consent screen must list `https://www.googleapis.com/auth/gmail.compose` before the scope will be granted.
- In **Testing** mode with Fisher as a test user, no verification review is needed — but **refresh tokens expire after 7 days**. A "Gmail stopped working after a week" report later is this, not a bug.
- Every existing session must sign out and back in once; the new scope is not retro-granted.

- [ ] **Step 1: Write the failing test**

Create `tests/auth-gmail-scope.test.ts`:

```ts
import { authConfig } from "@/auth.config";

describe("google provider scopes", () => {
  const provider = authConfig.providers[0] as unknown as {
    authorization?: { params?: Record<string, string> };
  };

  it("requests the gmail.compose scope", () => {
    expect(provider.authorization?.params?.scope).toContain(
      "https://www.googleapis.com/auth/gmail.compose"
    );
  });

  it("keeps the login scopes", () => {
    const scope = provider.authorization?.params?.scope ?? "";
    expect(scope).toContain("openid");
    expect(scope).toContain("email");
    expect(scope).toContain("profile");
  });

  it("asks for offline access so a refresh token is issued", () => {
    expect(provider.authorization?.params?.access_type).toBe("offline");
  });

  it("forces the consent screen, so existing logins actually get the new scope", () => {
    expect(provider.authorization?.params?.prompt).toBe("consent");
  });

  it("does not request send or full-mailbox scopes", () => {
    // Drafts only — approve-to-send is explicitly out of scope for v1.
    const scope = provider.authorization?.params?.scope ?? "";
    expect(scope).not.toContain("gmail.send");
    expect(scope).not.toContain("mail.google.com");
    expect(scope).not.toContain("gmail.modify");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/auth-gmail-scope.test.ts`
Expected: FAIL — the provider has no `authorization` block.

- [ ] **Step 3a: Widen the scope**

In `src/auth.config.ts`, replace the `Google({...})` provider and update the comment above it:

```ts
// Scopes: login (openid/email/profile) plus gmail.compose, which J3's
// draft_email tool needs. Drafts only — gmail.send and gmail.modify are
// deliberately absent, because approve-to-send is out of scope for v1 and a
// scope we do not request cannot be abused by a prompt-injected run.
//
// access_type=offline + prompt=consent are both required: without them Google
// issues no refresh token, and an existing signed-in director would never be
// re-prompted for the newly added scope.
//
// NOTE: gmail.compose is a sensitive scope. In Testing mode with named test
// users this works with no verification review, but refresh tokens expire
// after 7 days. Widening beyond the test-user list needs app verification.
export const authConfig = {
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
      authorization: {
        params: {
          scope: "openid email profile https://www.googleapis.com/auth/gmail.compose",
          access_type: "offline",
          prompt: "consent",
        },
      },
    }),
  ],
  // ...rest unchanged...
```

- [ ] **Step 3b: Persist the tokens**

In `src/auth.ts`, change the `jwt` callback to also take `account`:

```ts
    // Runs only on sign-in (`profile`/`account` are undefined on later token
    // refreshes), so this is one upsert per login, not per request.
    async jwt({ token, profile, account }) {
      if (profile?.sub && profile.email) {
        const user = await upsertUser(getDb(), {
          googleSub: profile.sub,
          email: profile.email,
          name: profile.name ?? null,
          imageUrl: typeof profile.picture === "string" ? profile.picture : null,
        });
        token.userId = String(user.id);

        // Gmail drafts need the access/refresh pair to outlive this request.
        // Stored encrypted; see src/lib/auth/tokens.ts. A sign-in that yields
        // no access_token (shouldn't happen with this provider config) simply
        // stores nothing rather than throwing the login away.
        if (account?.access_token) {
          await saveGoogleTokens(getDb(), {
            userId: user.id,
            accessToken: account.access_token,
            refreshToken: account.refresh_token ?? null,
            scope: account.scope ?? "",
            expiresAt: account.expires_at ? new Date(account.expires_at * 1000) : null,
          });
        }
      }
      return token;
    },
```

Add `import { saveGoogleTokens } from "./lib/auth/tokens";`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/auth-gmail-scope.test.ts tests/auth-allowlist.test.ts tests/auth-actor-isolation.test.ts`
Expected: PASS.

Then re-consent manually: `npm run dev`, sign out, sign in, and confirm the Google screen now lists "Manage drafts and send emails" — accept it. Verify a row exists **without printing it**:

```bash
docker compose -f ../Sourcecado/docker-compose.yml exec -T db \
  psql -U sourcecado -d sourcecado_j3 -c 'SELECT user_id, scope, expires_at FROM oauth_tokens;'
```

- [ ] **Step 5: Commit**

```bash
git add src/auth.config.ts src/auth.ts .env.example tests/auth-gmail-scope.test.ts
git commit -m "feat(J3): request gmail.compose and persist Google tokens on sign-in"
```

---

### Task 12: Gmail client — refresh, MIME, create draft

**Files:**
- Create: `src/lib/gmail/client.ts`
- Test: `tests/gmail-client.test.ts`

**Interfaces:**
- Consumes: `getStoredGoogleTokens`, `saveGoogleTokens` (Task 10).
- Produces:
  - `buildRawMessage(input: { to: string; subject: string; body: string }): string` — RFC-2822, base64url.
  - `getFreshAccessToken(db, userId: number): Promise<string>` — refreshes when within 60s of expiry, persists the new token.
  - `createGmailDraft(db, input: { userId: number; to: string; subject: string; body: string }): Promise<{ draftId: string; messageId: string }>`
  - `class GmailError extends Error { readonly code: string }`

  Task 13 calls `createGmailDraft`.

**No `googleapis` dependency** — plain `fetch` against `https://oauth2.googleapis.com/token` and `https://gmail.googleapis.com/gmail/v1/users/me/drafts`. `tests/package-deps.test.ts` will fail if a dependency is added.

- [ ] **Step 1: Write the failing test**

Create `tests/gmail-client.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { upsertUser } from "@/lib/auth/users";
import { saveGoogleTokens, getStoredGoogleTokens } from "@/lib/auth/tokens";
import { buildRawMessage, createGmailDraft, getFreshAccessToken, GmailError } from "@/lib/gmail/client";

const KEY = "8fZ1kq9pXn2vT7yR4wB6sL0mJ3hG5dC1aE8uQ2iO7xY=";
const originalFetch = globalThis.fetch;

function decodeRaw(raw: string): string {
  return Buffer.from(raw, "base64url").toString("utf8");
}

async function userWithTokens(expiresAt: Date | null, refreshToken: string | null = "1//refresh") {
  const db = getDb();
  const user = await upsertUser(db, {
    googleSub: `sub-${Math.round(expiresAt?.getTime() ?? 0)}-${refreshToken}`,
    email: "d@codeology.org",
    name: null,
    imageUrl: null,
  });
  await saveGoogleTokens(db, {
    userId: user.id, accessToken: "ya29.old", refreshToken, scope: "", expiresAt,
  });
  return user;
}

describe("gmail raw message", () => {
  it("is base64url with no padding characters that Gmail rejects", () => {
    const raw = buildRawMessage({ to: "sarah@acme.com", subject: "Hi", body: "Hello" });
    expect(raw).not.toContain("+");
    expect(raw).not.toContain("/");
    expect(raw).not.toContain("=");
  });

  it("carries the headers and body", () => {
    const decoded = decodeRaw(buildRawMessage({ to: "sarah@acme.com", subject: "Hi", body: "Hello there" }));
    expect(decoded).toContain("To: sarah@acme.com");
    expect(decoded).toContain("Subject: Hi");
    expect(decoded).toContain("Hello there");
  });

  it("strips CR/LF from headers so a body cannot inject one", () => {
    // A prompt-injected subject must not be able to add Bcc:.
    const decoded = decodeRaw(
      buildRawMessage({ to: "sarah@acme.com", subject: "Hi\r\nBcc: evil@example.com", body: "x" })
    );
    expect(decoded).not.toContain("Bcc:");
  });
});

describe("access token refresh", () => {
  const originalKey = process.env.TOKEN_ENCRYPTION_KEY;
  beforeEach(async () => {
    process.env.TOKEN_ENCRYPTION_KEY = KEY;
    process.env.GOOGLE_CLIENT_ID = "cid";
    process.env.GOOGLE_CLIENT_SECRET = "csecret";
    const db = getDb();
    await db`DROP TABLE IF EXISTS oauth_tokens CASCADE`;
    await db`DELETE FROM schema_migrations WHERE name = '009_oauth_tokens.sql'`;
    await runMigrations(db);
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });
  afterAll(async () => {
    process.env.TOKEN_ENCRYPTION_KEY = originalKey;
    await closeDb();
  });

  it("uses the stored token when it is still valid", async () => {
    const user = await userWithTokens(new Date(Date.now() + 3600_000));
    globalThis.fetch = (async () => {
      throw new Error("should not refresh");
    }) as typeof fetch;
    expect(await getFreshAccessToken(getDb(), user.id)).toBe("ya29.old");
  });

  it("refreshes an expired token and persists the new one", async () => {
    const user = await userWithTokens(new Date(Date.now() - 1000));
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ access_token: "ya29.new", expires_in: 3600 }), { status: 200 })) as typeof fetch;
    expect(await getFreshAccessToken(getDb(), user.id)).toBe("ya29.new");
    expect((await getStoredGoogleTokens(getDb(), user.id))!.accessToken).toBe("ya29.new");
  });

  it("fails with a reconnect-shaped error when there is no refresh token", async () => {
    const user = await userWithTokens(new Date(Date.now() - 1000), null);
    await expect(getFreshAccessToken(getDb(), user.id)).rejects.toMatchObject({ code: "reconnect_required" });
  });

  it("fails with reconnect_required when Google rejects the refresh (7-day test-mode expiry)", async () => {
    const user = await userWithTokens(new Date(Date.now() - 1000));
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: "invalid_grant" }), { status: 400 })) as typeof fetch;
    await expect(getFreshAccessToken(getDb(), user.id)).rejects.toMatchObject({ code: "reconnect_required" });
  });

  it("fails with not_connected when the user has no tokens at all", async () => {
    await expect(getFreshAccessToken(getDb(), 999_999)).rejects.toMatchObject({ code: "not_connected" });
  });
});

describe("createGmailDraft", () => {
  const originalKey = process.env.TOKEN_ENCRYPTION_KEY;
  beforeEach(async () => {
    process.env.TOKEN_ENCRYPTION_KEY = KEY;
    const db = getDb();
    await db`DROP TABLE IF EXISTS oauth_tokens CASCADE`;
    await db`DELETE FROM schema_migrations WHERE name = '009_oauth_tokens.sql'`;
    await runMigrations(db);
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });
  afterAll(async () => {
    process.env.TOKEN_ENCRYPTION_KEY = originalKey;
    await closeDb();
  });

  it("posts to the drafts endpoint with the bearer token and returns ids", async () => {
    const user = await userWithTokens(new Date(Date.now() + 3600_000));
    let seenUrl = "";
    let seenAuth = "";
    let seenBody: { message?: { raw?: string } } = {};
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      seenUrl = String(url);
      seenAuth = String((init.headers as Record<string, string>).Authorization);
      seenBody = JSON.parse(String(init.body));
      return new Response(JSON.stringify({ id: "draft-1", message: { id: "msg-1" } }), { status: 200 });
    }) as unknown as typeof fetch;

    const out = await createGmailDraft(getDb(), {
      userId: user.id, to: "sarah@acme.com", subject: "Hi", body: "Hello",
    });
    expect(seenUrl).toBe("https://gmail.googleapis.com/gmail/v1/users/me/drafts");
    expect(seenAuth).toBe("Bearer ya29.old");
    expect(decodeRaw(seenBody.message!.raw!)).toContain("To: sarah@acme.com");
    expect(out).toEqual({ draftId: "draft-1", messageId: "msg-1" });
  });

  it("surfaces a Gmail API failure as a GmailError, not a silent success", async () => {
    const user = await userWithTokens(new Date(Date.now() + 3600_000));
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: { message: "Insufficient Permission" } }), { status: 403 })) as typeof fetch;
    await expect(
      createGmailDraft(getDb(), { userId: user.id, to: "sarah@acme.com", subject: "Hi", body: "Hello" })
    ).rejects.toBeInstanceOf(GmailError);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/gmail-client.test.ts`
Expected: FAIL — cannot resolve `@/lib/gmail/client`.

- [ ] **Step 3: Write the client**

Create `src/lib/gmail/client.ts`:

```ts
import { getStoredGoogleTokens, saveGoogleTokens } from "../auth/tokens";
import type { Sql } from "../tools/types";

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts";
// Refresh a little early: a token that expires mid-flight would fail the draft
// for no reason the director could act on.
const EXPIRY_SKEW_MS = 60_000;

export class GmailError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "GmailError";
    this.code = code;
  }
}

// Header values are single-line by definition. Stripping CR/LF is what stops a
// model-authored (or prompt-injected) subject from smuggling in `Bcc:` — the
// classic header-injection hole, and the reason the body is never interpolated
// into a header.
function headerValue(value: string): string {
  return value.replace(/[\r\n]+/g, " ").trim();
}

export function buildRawMessage(input: { to: string; subject: string; body: string }): string {
  const message = [
    `To: ${headerValue(input.to)}`,
    `Subject: ${headerValue(input.subject)}`,
    "Content-Type: text/plain; charset=UTF-8",
    "MIME-Version: 1.0",
    "",
    input.body,
  ].join("\r\n");
  // base64url, unpadded — what the Gmail API expects for `message.raw`.
  return Buffer.from(message, "utf8").toString("base64url");
}

export async function getFreshAccessToken(db: Sql, userId: number): Promise<string> {
  const stored = await getStoredGoogleTokens(db, userId);
  if (!stored) {
    throw new GmailError("not_connected", "This director has not connected Google. Sign out and back in to grant Gmail access.");
  }

  const stillValid = !stored.expiresAt || stored.expiresAt.getTime() - EXPIRY_SKEW_MS > Date.now();
  if (stillValid) return stored.accessToken;

  if (!stored.refreshToken) {
    throw new GmailError("reconnect_required", "The Google access token expired and no refresh token is stored. Sign out and back in.");
  }

  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: process.env.GOOGLE_CLIENT_ID ?? "",
      client_secret: process.env.GOOGLE_CLIENT_SECRET ?? "",
      refresh_token: stored.refreshToken,
      grant_type: "refresh_token",
    }),
  });

  if (!res.ok) {
    // invalid_grant is what a revoked consent — or a Testing-mode refresh
    // token past its 7-day life — looks like. Both need the same human action.
    throw new GmailError("reconnect_required", "Google refused to refresh the token. Sign out and back in to reconnect Gmail.");
  }

  const payload = (await res.json()) as { access_token?: string; expires_in?: number; scope?: string };
  if (!payload.access_token) {
    throw new GmailError("reconnect_required", "Google's refresh response contained no access token.");
  }

  const expiresAt = payload.expires_in ? new Date(Date.now() + payload.expires_in * 1000) : null;
  await saveGoogleTokens(db, {
    userId,
    accessToken: payload.access_token,
    // Refresh responses carry no refresh_token; the store COALESCEs so the
    // existing one survives.
    refreshToken: null,
    scope: payload.scope ?? stored.scope,
    expiresAt,
  });
  return payload.access_token;
}

export async function createGmailDraft(
  db: Sql,
  input: { userId: number; to: string; subject: string; body: string }
): Promise<{ draftId: string; messageId: string }> {
  const accessToken = await getFreshAccessToken(db, input.userId);
  const res = await fetch(DRAFTS_URL, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "content-type": "application/json" },
    body: JSON.stringify({
      message: { raw: buildRawMessage({ to: input.to, subject: input.subject, body: input.body }) },
    }),
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    // Truncated: the response can echo request content, and this string ends
    // up in a tool_result the model reads back.
    throw new GmailError("gmail_api_error", `Gmail refused the draft (${res.status}): ${detail.slice(0, 200)}`);
  }

  const payload = (await res.json()) as { id?: string; message?: { id?: string } };
  if (!payload.id) throw new GmailError("gmail_api_error", "Gmail accepted the request but returned no draft id.");
  return { draftId: payload.id, messageId: payload.message?.id ?? "" };
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/gmail-client.test.ts tests/package-deps.test.ts`
Expected: PASS (11 + deps suite still green, proving no dependency was added).

- [ ] **Step 5: Commit**

```bash
git add src/lib/gmail/client.ts tests/gmail-client.test.ts
git commit -m "feat(J3): Gmail draft client with token refresh, no new dependency"
```

---

### Task 13: The `draft_email` tool

**Files:**
- Create: `src/lib/tools/draft-email.ts`
- Modify: `src/lib/memory/answer-config.ts` (register it)
- Test: `tests/draft-email-tool.test.ts`

**Interfaces:**
- Consumes: `createGmailDraft` (Task 12); `Tool.targetArg` (Task 4).
- Produces: `draftEmailTool: Tool<{ to: string; subject: string; body: string }, { draftId: string; messageId: string }>` with `name: "draft_email"`, `permissionClass: "draft"`, `targetArg: "to"`. Registered in `memoryRegistry()`.

**This is the first `draft`-class tool in the registry** — from here on, the grant gate has a production caller, and any future `draft`/`admin` tool is gated by construction rather than by someone remembering to wire it.

- [ ] **Step 1: Write the failing test**

Create `tests/draft-email-tool.test.ts`:

```ts
import { draftEmailTool } from "@/lib/tools/draft-email";
import { memoryRegistry } from "@/lib/memory/answer-config";
import type { MemoryActor } from "@/lib/memory/actor";
import type { Sql } from "@/lib/tools/types";

const ACTOR: MemoryActor = { actorType: "user", actorId: "42" };
const ctx = { db: {} as Sql, runId: 1, parentStepId: 1, actor: ACTOR };

describe("draft_email tool", () => {
  it("is a draft-class tool that declares `to` as its target", () => {
    expect(draftEmailTool.name).toBe("draft_email");
    expect(draftEmailTool.permissionClass).toBe("draft");
    expect(draftEmailTool.targetArg).toBe("to");
  });

  it("requires a plausible recipient address", () => {
    expect(() => draftEmailTool.argsSchema.parse({ to: "not-an-email", subject: "s", body: "b" })).toThrow();
    expect(() => draftEmailTool.argsSchema.parse({ to: "sarah@acme.com", subject: "s", body: "b" })).not.toThrow();
  });

  it("rejects an empty subject or body", () => {
    expect(() => draftEmailTool.argsSchema.parse({ to: "a@b.com", subject: "", body: "b" })).toThrow();
    expect(() => draftEmailTool.argsSchema.parse({ to: "a@b.com", subject: "s", body: "" })).toThrow();
  });

  it("refuses a non-numeric actor id rather than drafting as the wrong user", async () => {
    await expect(
      draftEmailTool.execute(
        { to: "a@b.com", subject: "s", body: "b" },
        { ...ctx, actor: { actorType: "test_client", actorId: "default" } }
      )
    ).rejects.toThrow(/signed-in director/);
  });

  it("is registered in the memory registry as the only draft-class tool", () => {
    const registry = memoryRegistry();
    expect(registry.get("draft_email")).toBeDefined();
    const draftTools = registry.list(new Set(["draft"])).map((t) => t.name);
    expect(draftTools).toEqual(["draft_email"]);
  });

  it("every draft-class tool in the registry declares a targetArg", () => {
    // The gate denies a targetless draft tool at runtime; this catches it at
    // registration time instead, where the fix is obvious.
    for (const tool of memoryRegistry().list(new Set(["draft", "admin"]))) {
      expect(tool.targetArg, `${tool.name} must declare targetArg`).toBeTruthy();
    }
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/draft-email-tool.test.ts`
Expected: FAIL — cannot resolve `@/lib/tools/draft-email`.

- [ ] **Step 3a: Write the tool**

Create `src/lib/tools/draft-email.ts`:

```ts
import { z } from "zod";
import { createGmailDraft } from "../gmail/client";
import type { Tool } from "./types";

const argsSchema = z.object({
  to: z.string().email(),
  subject: z.string().min(1).max(200),
  body: z.string().min(1).max(10_000),
});

export type DraftEmailArgs = z.infer<typeof argsSchema>;

export const draftEmailTool: Tool<DraftEmailArgs, { draftId: string; messageId: string }> = {
  name: "draft_email",
  description:
    "Create a Gmail draft addressed to one recipient. The draft is saved to the director's Gmail " +
    "drafts folder — it is never sent. Requires the director's approval for each recipient the " +
    "first time; they may choose to approve that recipient standing.",
  permissionClass: "draft",
  // Grants bind to the recipient: approving sarah@acme.com never approves
  // mike@othercorp.com.
  targetArg: "to",
  argsSchema,
  async execute(args, ctx) {
    // actorId is users.id for a signed-in director. A sentinel actor (CLI,
    // tests) has no Google tokens, and drafting into some other mailbox would
    // be worse than failing.
    const userId = Number(ctx.actor.actorId);
    if (ctx.actor.actorType !== "user" || !Number.isInteger(userId)) {
      throw new Error("draft_email requires a signed-in director with a connected Google account.");
    }
    return createGmailDraft(ctx.db, {
      userId,
      to: args.to,
      subject: args.subject,
      body: args.body,
    });
  },
};
```

- [ ] **Step 3b: Register it**

Add `draftEmailTool` to the registry list in `src/lib/memory/answer-config.ts`.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/draft-email-tool.test.ts tests/approvals-gate.test.ts tests/memory-answer.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lib/tools/draft-email.ts src/lib/memory/answer-config.ts tests/draft-email-tool.test.ts
git commit -m "feat(J3): add the draft_email tool behind the approval gate"
```

---

### Task 14: Client stream reader handles `data-question`

**Files:**
- Modify: `src/app/chat/stream.ts`
- Test: `tests/chat-stream-question.test.ts`

**Interfaces:**
- Consumes: `PendingQuestionView` (Task 8); the `/api/agent/answer` route (Task 9).
- Produces:
  - `AssistantTurn.pending?: PendingQuestionView`
  - `applyChunk` handles `"data-question"`.
  - `answerPending(input: { pendingId: number; answer?: string; approved?: boolean; always?: boolean }, onUpdate, signal?): Promise<AssistantTurn>` — same reader as `runChat`, different endpoint.
  - Internal `readStream(res, onUpdate, seed?)` shared by both, so there is one SSE reader, not two.

- [ ] **Step 1: Write the failing test**

Create `tests/chat-stream-question.test.ts`:

```ts
import { applyChunk, answerPending, type AssistantTurn } from "@/app/chat/stream";

const EMPTY: AssistantTurn = { steps: [], answer: "" };

const view = {
  id: 7,
  kind: "approval" as const,
  question: "Allow draft_email for sarah@acme.com?",
  header: "Approval",
  options: [],
  allowText: false,
  multi: false,
  target: "sarah@acme.com",
  toolName: "draft_email",
};

function sseResponse(chunks: unknown[]): Response {
  const body = chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("");
  return new Response(new TextEncoder().encode(body), { status: 200 });
}

describe("data-question chunk", () => {
  it("lands on the turn as `pending`", () => {
    const turn = applyChunk(EMPTY, { type: "data-question", data: view });
    expect(turn.pending).toEqual(view);
  });

  it("clears any live pending-tool row", () => {
    const withTool = applyChunk(EMPTY, { type: "data-tool-pending", data: { tool: "draft_email" } });
    expect(applyChunk(withTool, { type: "data-question", data: view }).pendingTool).toBeUndefined();
  });

  it("does not disturb text already streamed", () => {
    const withText = applyChunk(EMPTY, { type: "text-delta", delta: "Working on it. " });
    expect(applyChunk(withText, { type: "data-question", data: view }).answer).toBe("Working on it. ");
  });
});

describe("answerPending", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("posts the answer and folds the resumed stream into the turn", async () => {
    let seenBody: Record<string, unknown> = {};
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      seenBody = JSON.parse(String(init.body));
      return sseResponse([
        { type: "text-delta", delta: "Draft created." },
        { type: "data-meta", data: { runId: 9, status: "succeeded", steps: 2, invalidCitations: [] } },
      ]);
    }) as unknown as typeof fetch;

    const updates: AssistantTurn[] = [];
    const turn = await answerPending({ pendingId: 7, approved: true, always: true }, (t) => updates.push(t));
    expect(seenBody).toEqual({ pendingId: 7, approved: true, always: true });
    expect(turn.answer).toBe("Draft created.");
    expect(turn.meta?.runId).toBe(9);
    expect(updates.length).toBeGreaterThan(0);
  });

  it("clears the pending card when the resumed run does not suspend again", async () => {
    globalThis.fetch = (async () =>
      sseResponse([{ type: "data-meta", data: { runId: 9, status: "succeeded", steps: 1, invalidCitations: [] } }])) as unknown as typeof fetch;
    const turn = await answerPending({ pendingId: 7, approved: true }, () => {}, undefined, { ...EMPTY, pending: view });
    expect(turn.pending).toBeUndefined();
  });

  it("keeps a new card when the resumed run suspends again", async () => {
    const second = { ...view, id: 8, question: "Allow draft_email for mike@othercorp.com?" };
    globalThis.fetch = (async () => sseResponse([{ type: "data-question", data: second }])) as unknown as typeof fetch;
    const turn = await answerPending({ pendingId: 7, approved: true }, () => {}, undefined, { ...EMPTY, pending: view });
    expect(turn.pending?.id).toBe(8);
  });

  it("throws on a non-OK response so the UI can surface it", async () => {
    globalThis.fetch = (async () => new Response("{}", { status: 409 })) as unknown as typeof fetch;
    await expect(answerPending({ pendingId: 7, approved: true }, () => {})).rejects.toThrow(/409/);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/chat-stream-question.test.ts`
Expected: FAIL — `answerPending` is not exported.

- [ ] **Step 3: Modify the stream reader**

In `src/app/chat/stream.ts`:

Add the import and the field:

```ts
import type { PendingQuestionView } from "@/lib/approvals/view";

export interface AssistantTurn {
  steps: ChatStep[];
  answer: string;
  meta?: ChatMeta;
  pendingTool?: string;
  // The run suspended and is waiting on the director. While set, the composer
  // is disabled and the AskCard is shown.
  pending?: PendingQuestionView;
}
```

Add the case to `applyChunk`:

```ts
    case "data-question":
      return { ...turn, pending: chunk.data as PendingQuestionView, pendingTool: undefined };
```

Extract the reader shared by both calls, replacing the body of `runChat` after the `fetch`:

```ts
// One SSE reader for both the ask path and the answer path. `seed` lets the
// answer path start from the turn already on screen, so the card is replaced
// rather than the whole exchange being rebuilt.
async function readStream(
  res: Response,
  onUpdate: (turn: AssistantTurn) => void,
  seed: AssistantTurn
): Promise<AssistantTurn> {
  if (!res.ok) throw new Error(`Stream failed (${res.status})`);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let turn = seed;

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const { chunks, rest } = drainSse(buffer);
    buffer = rest;
    if (chunks.length) {
      for (const chunk of chunks) turn = applyChunk(turn, chunk);
      onUpdate(turn);
    }
  }
  return turn;
}

export async function runChat(
  question: string,
  history: ConversationTurn[],
  onUpdate: (turn: AssistantTurn) => void,
  signal?: AbortSignal
): Promise<AssistantTurn> {
  const res = await fetch("/api/agent/stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, history }),
    signal,
  });
  return readStream(res, onUpdate, { steps: [], answer: "" });
}

export interface AnswerPendingInput {
  pendingId: number;
  answer?: string;
  approved?: boolean;
  always?: boolean;
}

// Answer an open question and resume the run. The seed turn starts with
// `pending` cleared: if the resumed run suspends again, a fresh data-question
// puts a new card up; if it doesn't, the card simply goes away.
export async function answerPending(
  input: AnswerPendingInput,
  onUpdate: (turn: AssistantTurn) => void,
  signal?: AbortSignal,
  seed: AssistantTurn = { steps: [], answer: "" }
): Promise<AssistantTurn> {
  const res = await fetch("/api/agent/answer", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
  return readStream(res, onUpdate, { ...seed, pending: undefined });
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/chat-stream-question.test.ts tests/chat-stream.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/chat/stream.ts tests/chat-stream-question.test.ts
git commit -m "feat(J3): read pending questions from the chat stream"
```

---

### Task 15: The inline `AskCard` and ChatClient wiring

**Files:**
- Create: `src/app/chat/AskCard.tsx`
- Modify: `src/app/chat/ChatClient.tsx`
- Test: `tests/components/ask-card.test.tsx`

**Interfaces:**
- Consumes: `PendingQuestionView` (Task 8); `answerPending` (Task 14); `Button`, `Input` from `@/components/ui`.
- Produces: `AskCard({ pending, busy, onAnswer }: { pending: PendingQuestionView; busy: boolean; onAnswer: (input: { answer?: string; approved?: boolean; always?: boolean }) => void })`.

**Design (DESIGN.md, Warm Operator):** a bordered card, `rounded-[8px] border border-border bg-surface p-4`, header chip in `text-[11px] uppercase tracking-wide text-muted`, question at `text-[13px] text-text`. Approval kind renders the target in `font-mono`. Primary action uses `Button variant="primary"` (avocado `bg-accent`); secondary/deny uses `variant="ghost"`. No new colors, no new radii.

Buttons by kind:
- `question`: one button per option (ghost), plus a text input + "Send" (primary) when `allowText`. `multi` renders options as toggles with one "Send" (primary) submitting the joined selection.
- `approval`: "Approve once" (ghost), "Always allow for `{target}`" (primary), "Deny" (ghost).

Accessibility: the card is `role="group"` with `aria-label` naming the question; the first actionable control takes focus on mount; `aria-busy` while the answer is in flight.

- [ ] **Step 1: Write the failing test**

Create `tests/components/ask-card.test.tsx`:

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { AskCard } from "@/app/chat/AskCard";
import type { PendingQuestionView } from "@/lib/approvals/view";

const question: PendingQuestionView = {
  id: 1, kind: "question", question: "Which Sarah do you mean?", header: "Contact",
  options: ["Sarah Chen", "Sarah Patel"], allowText: true, multi: false, target: null, toolName: "ask_user",
};

const approval: PendingQuestionView = {
  id: 2, kind: "approval", question: "Allow draft_email for sarah@acme.com?", header: "Approval",
  options: [], allowText: false, multi: false, target: "sarah@acme.com", toolName: "draft_email",
};

describe("AskCard — open question", () => {
  it("shows the question and its header chip", () => {
    render(<AskCard pending={question} busy={false} onAnswer={() => {}} />);
    expect(screen.getByText("Which Sarah do you mean?")).toBeInTheDocument();
    expect(screen.getByText("Contact")).toBeInTheDocument();
  });

  it("sends the option text when an option is clicked", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={question} busy={false} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "Sarah Chen" }));
    expect(onAnswer).toHaveBeenCalledWith({ answer: "Sarah Chen", approved: true });
  });

  it("sends typed text", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={question} busy={false} onAnswer={onAnswer} />);
    fireEvent.change(screen.getByLabelText(/your answer/i), { target: { value: "Neither" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onAnswer).toHaveBeenCalledWith({ answer: "Neither", approved: true });
  });

  it("does not send empty text", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={question} busy={false} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onAnswer).not.toHaveBeenCalled();
  });

  it("hides the text input when allowText is false", () => {
    render(<AskCard pending={{ ...question, allowText: false }} busy={false} onAnswer={() => {}} />);
    expect(screen.queryByLabelText(/your answer/i)).not.toBeInTheDocument();
  });

  it("joins multi-select answers", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={{ ...question, multi: true, allowText: false }} busy={false} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: "Sarah Chen" }));
    fireEvent.click(screen.getByRole("button", { name: "Sarah Patel" }));
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(onAnswer).toHaveBeenCalledWith({ answer: "Sarah Chen, Sarah Patel", approved: true });
  });

  it("disables every control while busy", () => {
    render(<AskCard pending={question} busy onAnswer={() => {}} />);
    for (const button of screen.getAllByRole("button")) expect(button).toBeDisabled();
  });
});

describe("AskCard — approval", () => {
  it("names the exact target, so the scope of a standing yes is visible", () => {
    render(<AskCard pending={approval} busy={false} onAnswer={() => {}} />);
    expect(screen.getByRole("button", { name: /always allow for sarah@acme\.com/i })).toBeInTheDocument();
  });

  it("approve once does not request a grant", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={approval} busy={false} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: /approve once/i }));
    expect(onAnswer).toHaveBeenCalledWith({ approved: true, always: false });
  });

  it("always allow requests a grant", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={approval} busy={false} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: /always allow/i }));
    expect(onAnswer).toHaveBeenCalledWith({ approved: true, always: true });
  });

  it("deny sends approved: false and never always", () => {
    const onAnswer = vi.fn();
    render(<AskCard pending={approval} busy={false} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole("button", { name: /deny/i }));
    expect(onAnswer).toHaveBeenCalledWith({ approved: false, always: false });
  });

  it("shows no free-text input on an approval", () => {
    render(<AskCard pending={approval} busy={false} onAnswer={() => {}} />);
    expect(screen.queryByLabelText(/your answer/i)).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/components/ask-card.test.tsx`
Expected: FAIL — cannot resolve `@/app/chat/AskCard`.

- [ ] **Step 3a: Write the card**

Create `src/app/chat/AskCard.tsx`:

```tsx
"use client";

import { useState } from "react";
import { Button, Input } from "@/components/ui";
import type { PendingQuestionView } from "@/lib/approvals/view";

export interface AskAnswer {
  answer?: string;
  approved?: boolean;
  always?: boolean;
}

// The inline human-in-the-loop card. Two shapes behind one component: an open
// question (options and/or free text) and an approval (approve once / always
// for this target / deny). Both render in the chat thread — there is no
// separate inbox, so the question lives where the conversation does.
export function AskCard({
  pending,
  busy,
  onAnswer,
}: {
  pending: PendingQuestionView;
  busy: boolean;
  onAnswer: (input: AskAnswer) => void;
}) {
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string[]>([]);

  const isApproval = pending.kind === "approval";

  function toggle(option: string) {
    if (!pending.multi) {
      onAnswer({ answer: option, approved: true });
      return;
    }
    setSelected((prev) => (prev.includes(option) ? prev.filter((o) => o !== option) : [...prev, option]));
  }

  function sendText() {
    const value = pending.multi ? selected.join(", ") : text.trim();
    if (!value) return;
    onAnswer({ answer: value, approved: true });
  }

  return (
    <div
      role="group"
      aria-label={pending.question}
      aria-busy={busy}
      className="rounded-[8px] border border-border bg-surface p-4"
    >
      {pending.header ? (
        <div className="text-[11px] uppercase tracking-wide text-muted">{pending.header}</div>
      ) : null}
      <p className="mt-1 text-[13px] text-text">{pending.question}</p>

      {isApproval ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="primary" disabled={busy} onClick={() => onAnswer({ approved: true, always: true })}>
            Always allow for {pending.target}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => onAnswer({ approved: true, always: false })}>
            Approve once
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => onAnswer({ approved: false, always: false })}>
            Deny
          </Button>
        </div>
      ) : (
        <>
          {pending.options.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {pending.options.map((option) => (
                <Button
                  key={option}
                  variant="ghost"
                  disabled={busy}
                  aria-pressed={pending.multi ? selected.includes(option) : undefined}
                  className={pending.multi && selected.includes(option) ? "border-accent text-accent-deep" : ""}
                  onClick={() => toggle(option)}
                >
                  {option}
                </Button>
              ))}
            </div>
          ) : null}

          {pending.allowText || pending.multi ? (
            <div className="mt-3 flex items-center gap-2">
              {pending.allowText ? (
                <Input
                  aria-label="Your answer"
                  value={text}
                  disabled={busy}
                  placeholder="Type your answer"
                  onChange={(event) => setText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") sendText();
                  }}
                />
              ) : null}
              <Button variant="primary" disabled={busy} onClick={sendText}>
                Send
              </Button>
            </div>
          ) : null}
        </>
      )}

      {isApproval ? (
        <p className="mt-3 text-[11px] text-muted">
          A standing approval covers <span className="font-mono">{pending.target}</span> only — every other
          recipient still asks.
        </p>
      ) : null}
    </div>
  );
}
```

If `Input`'s props do not already forward `aria-label`/`onKeyDown`, widen them there rather than reaching around the primitive.

- [ ] **Step 3b: Wire it into ChatClient**

In `src/app/chat/ChatClient.tsx`:

- Import `AskCard`, `answerPending`, and `type AskAnswer`.
- Render the card inside the exchange body, after the answer bubble:

```tsx
                  {e.turn.pending ? (
                    <AskCard
                      pending={e.turn.pending}
                      busy={busy}
                      onAnswer={(input) => answer(e.id, e.turn.pending!.id, input)}
                    />
                  ) : null}
```

- Add the handler beside `submit`:

```tsx
  function answer(exchangeId: number, pendingId: number, input: AskAnswer) {
    if (busy) return;
    setBusy(true);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), RUN_TIMEOUT_MS);
    const seed = exchanges.find((e) => e.id === exchangeId)?.turn ?? { steps: [], answer: "" };

    patch(exchangeId, (e) => ({ ...e, done: false, open: true }));
    answerPending({ pendingId, ...input }, (turn) => patch(exchangeId, (e) => ({ ...e, turn })), controller.signal, seed)
      .then((finalTurn) =>
        patch(exchangeId, (e) => ({ ...e, turn: finalTurn, done: true, open: Boolean(finalTurn.pending) }))
      )
      .catch((err) => {
        const message = controller.signal.aborted
          ? "The run timed out before completing. Try again."
          : err instanceof Error
            ? err.message
            : "The answer could not be delivered.";
        patch(exchangeId, (e) => ({ ...e, errored: true, done: true, open: false, turn: { ...e.turn, answer: message } }));
      })
      .finally(() => {
        clearTimeout(timeout);
        setBusy(false);
      });
  }
```

- Disable the composer while a question is open, so the director answers rather than accidentally cancelling:

```tsx
  const awaitingAnswer = exchanges.some((e) => e.turn.pending);
  // ...
        <Composer value={input} onChange={setInput} onSubmit={submit} disabled={busy || awaitingAnswer} />
```

Note the deliberate asymmetry: the composer is disabled, but `cancelStalePending` (Task 8) still exists as the server-side safety net for a client that posts anyway — a reload, a second tab, or a stale page.

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/components/ask-card.test.tsx tests/chat-page.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/chat/AskCard.tsx src/app/chat/ChatClient.tsx tests/components/ask-card.test.tsx
git commit -m "feat(J3): inline ask/approval card in the chat thread"
```

---

### Task 16: An open question survives a reload

**Files:**
- Modify: `src/app/chat/resume.ts`
- Modify: `src/app/chat/page.tsx`
- Modify: `src/app/chat/ChatClient.tsx`
- Test: `tests/chat-resume-pending.test.ts`

**Interfaces:**
- Consumes: `getOpenPendingQuestion` (Task 3), `toPendingView` (Task 8), `mapMessagesToResumedExchanges` (R6).
- Produces: `ChatClient` accepts `initialPending?: PendingQuestionView`, attached to the **last** resumed exchange so the card reappears under the conversation it belongs to.

**Why this matters:** the whole point of suspending across requests is that the director can close the tab. If the card only exists in the live stream, the feature is live-only in practice.

- [ ] **Step 1: Write the failing test**

Create `tests/chat-resume-pending.test.ts`:

```ts
import { attachPendingToLastExchange } from "@/app/chat/resume";
import type { PendingQuestionView } from "@/lib/approvals/view";

const view: PendingQuestionView = {
  id: 3, kind: "approval", question: "Allow draft_email for sarah@acme.com?", header: "Approval",
  options: [], allowText: false, multi: false, target: "sarah@acme.com", toolName: "draft_email",
};

describe("attaching a pending question to resumed exchanges", () => {
  it("attaches to the last exchange", () => {
    const out = attachPendingToLastExchange(
      [{ question: "first", answer: "a" }, { question: "second", answer: "b" }],
      view
    );
    expect(out[0].pending).toBeUndefined();
    expect(out[1].pending).toEqual(view);
  });

  it("is a no-op when there is no pending question", () => {
    const exchanges = [{ question: "first", answer: "a" }];
    expect(attachPendingToLastExchange(exchanges, null)).toEqual(exchanges);
  });

  it("creates a placeholder exchange when the transcript has none", () => {
    // A suspend on the very first turn: the question must still be answerable.
    const out = attachPendingToLastExchange([], view);
    expect(out).toHaveLength(1);
    expect(out[0].pending).toEqual(view);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/chat-resume-pending.test.ts`
Expected: FAIL — `attachPendingToLastExchange` is not exported.

- [ ] **Step 3a: Extend the resume mapper**

In `src/app/chat/resume.ts`:

```ts
import type { PendingQuestionView } from "@/lib/approvals/view";

export interface ResumedExchange {
  question: string;
  answer: string;
  // Set on the last exchange only, when that session has an unanswered
  // question. This is what makes a suspended run survive a page reload.
  pending?: PendingQuestionView;
}

export function attachPendingToLastExchange(
  exchanges: ResumedExchange[],
  pending: PendingQuestionView | null
): ResumedExchange[] {
  if (!pending) return exchanges;
  if (exchanges.length === 0) return [{ question: "", answer: "", pending }];
  return exchanges.map((exchange, index) =>
    index === exchanges.length - 1 ? { ...exchange, pending } : exchange
  );
}
```

- [ ] **Step 3b: Load it in the page**

In `src/app/chat/page.tsx`, after `mapMessagesToResumedExchanges`:

```ts
  const pending = await getOpenPendingQuestion(db, session.id);
  const initialExchanges = attachPendingToLastExchange(
    mapMessagesToResumedExchanges(messages),
    pending ? toPendingView(pending) : null
  );
```

- [ ] **Step 3c: Seed it in ChatClient**

In `ChatClient.tsx`, carry `resumed.pending` into the seeded `Exchange`:

```tsx
      turn: { steps: [], answer: resumed.answer, pending: resumed.pending },
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/chat-resume-pending.test.ts tests/chat-resume.test.ts tests/chat-page.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app/chat/resume.ts src/app/chat/page.tsx src/app/chat/ChatClient.tsx tests/chat-resume-pending.test.ts
git commit -m "feat(J3): restore an open question after a reload"
```

---

### Task 17: Grant listing and revocation API

**Files:**
- Create: `src/app/api/grants/route.ts` (GET)
- Create: `src/app/api/grants/[id]/route.ts` (DELETE)
- Test: `tests/grants-routes.test.ts`

**Interfaces:**
- Consumes: `listGrants`, `revokeGrant` (Task 2); `requireActor` (H1).
- Produces: `GET /api/grants` → `{ grants: Grant[] }`; `DELETE /api/grants/:id` → `204` on success, `404` when the grant is absent **or belongs to someone else**.

**No UI page** — Fisher's explicit call. This is the off-switch a standing write permission needs; a settings page can come with E1, when grants have a second scope kind to show.

- [ ] **Step 1: Write the failing test**

Create `tests/grants-routes.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import { insertGrant, listGrants, type GrantScope } from "@/lib/approvals/grants";
import type { MemoryActor } from "@/lib/memory/actor";

const ACTOR: MemoryActor = { actorType: "user", actorId: "grant-route-1" };
const OTHER: MemoryActor = { actorType: "user", actorId: "grant-route-2" };
const SCOPE: GrantScope = { actor: ACTOR, scopeKind: "session", scopeId: "1" };

vi.mock("@/lib/auth/actor", () => ({
  requireActor: vi.fn(async () => ACTOR),
}));

async function reset(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS grants CASCADE`;
  await db`DROP TABLE IF EXISTS pending_questions CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '008_approvals.sql'`;
  await runMigrations(db);
}

describe("grants routes", () => {
  beforeEach(async () => {
    await reset();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("GET lists only the signed-in director's grants", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    await insertGrant(db, { ...SCOPE, actor: OTHER }, "draft_email", "mike@othercorp.com");

    const { GET } = await import("@/app/api/grants/route");
    const res = await GET();
    const body = (await res.json()) as { grants: { target: string }[] };
    expect(body.grants.map((g) => g.target)).toEqual(["sarah@acme.com"]);
  });

  it("DELETE revokes the director's own grant", async () => {
    const db = getDb();
    await insertGrant(db, SCOPE, "draft_email", "sarah@acme.com");
    const [grant] = await listGrants(db, ACTOR);

    const { DELETE } = await import("@/app/api/grants/[id]/route");
    const res = await DELETE(new Request("http://localhost/api/grants/1", { method: "DELETE" }), {
      params: Promise.resolve({ id: String(grant.id) }),
    });
    expect(res.status).toBe(204);
    expect(await listGrants(db, ACTOR)).toHaveLength(0);
  });

  it("DELETE 404s on another director's grant, and leaves it intact", async () => {
    const db = getDb();
    await insertGrant(db, { ...SCOPE, actor: OTHER }, "draft_email", "mike@othercorp.com");
    const [theirs] = await listGrants(db, OTHER);

    const { DELETE } = await import("@/app/api/grants/[id]/route");
    const res = await DELETE(new Request("http://localhost/api/grants/1", { method: "DELETE" }), {
      params: Promise.resolve({ id: String(theirs.id) }),
    });
    expect(res.status).toBe(404);
    expect(await listGrants(db, OTHER)).toHaveLength(1);
  });

  it("DELETE 400s on a non-numeric id", async () => {
    const { DELETE } = await import("@/app/api/grants/[id]/route");
    const res = await DELETE(new Request("http://localhost/api/grants/abc", { method: "DELETE" }), {
      params: Promise.resolve({ id: "abc" }),
    });
    expect(res.status).toBe(400);
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run tests/grants-routes.test.ts`
Expected: FAIL — cannot resolve `@/app/api/grants/route`.

- [ ] **Step 3a: Write the list route**

Create `src/app/api/grants/route.ts`:

```ts
import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { requireActor } from "@/lib/auth/actor";
import { listGrants } from "@/lib/approvals/grants";

// The off-switch for standing approvals. No page ships in J3 (Fisher's call —
// a Settings surface waits for E1, when routine-scoped grants exist to show
// alongside these); the route is here so a standing write permission is never
// something only a psql session can revoke.
export async function GET() {
  const actor = await requireActor();
  const grants = await listGrants(getDb(), actor);
  return NextResponse.json({ grants });
}
```

- [ ] **Step 3b: Write the revoke route**

Create `src/app/api/grants/[id]/route.ts`:

```ts
import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { requireActor } from "@/lib/auth/actor";
import { revokeGrant } from "@/lib/approvals/grants";

export async function DELETE(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const grantId = Number(id);
  if (!Number.isInteger(grantId) || grantId <= 0) {
    return NextResponse.json({ error: "Invalid grant id." }, { status: 400 });
  }

  const actor = await requireActor();
  // revokeGrant scopes by actor in the WHERE clause, so a miss is
  // indistinguishable from someone else's grant — 404 either way, and the
  // existence of an id leaks nothing.
  const revoked = await revokeGrant(getDb(), actor, grantId);
  if (!revoked) return NextResponse.json({ error: "No such grant." }, { status: 404 });
  return new NextResponse(null, { status: 204 });
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run tests/grants-routes.test.ts tests/auth-middleware-matcher.test.ts`
Expected: PASS. (`/api/grants` is protected by the deny-by-default matcher; add a near-miss case to the matcher test if one is not already there.)

- [ ] **Step 5: Commit**

```bash
git add src/app/api/grants tests/grants-routes.test.ts
git commit -m "feat(J3): list and revoke standing grants over the API"
```

---

### Task 18: Docs — roadmap, env, and the Gmail operational notes

**Files:**
- Modify: `docs/superpowers/plans/2026-08-02-completion-roadmap.md`
- Modify: `.env.example`
- Create: `docs/GMAIL-SETUP.md`

**Interfaces:** none — documentation only. Do it in the same branch so the estimate and the code land together.

- [ ] **Step 1: Correct the roadmap**

In the Phase 1 table, change the J3 row and add a note under it:

```markdown
| J3 | `ask_user` + scoped standing grants + **real Gmail drafts** | 3.5 | Port `ask.py` + `grant_entries()`. Gmail draft output pulled forward from Phase 2 (Fisher, 2026-08-04) |
```

In the Phase 2 list, replace `Gmail draft output (new ticket, needs H1 scopes + J3 approval)` with:

```markdown
Gmail draft output — **done in J3** (pulled forward 2026-08-04; approve-to-send
remains deferred)
```

Update the capacity section: Phase 1 goes ~9.5 → ~11.5 sessions, total ~33 → ~35 (~39 → ~41 with the 20% overhead). State plainly that V1 lands ~2 sessions later than the previous estimate.

- [ ] **Step 2: Write the Gmail setup note**

Create `docs/GMAIL-SETUP.md` covering:
- The exact scope string, and why `gmail.send`/`gmail.modify` are deliberately absent.
- Adding `https://www.googleapis.com/auth/gmail.compose` to the OAuth consent screen.
- **Testing mode**: works with named test users, no verification review, but **refresh tokens expire after 7 days** — a "Gmail stopped working after a week" report is this, not a bug. Symptom: `reconnect_required`.
- Going beyond the test-user list requires Google app verification for a sensitive scope; budget weeks, not days.
- `TOKEN_ENCRYPTION_KEY`: generate with `openssl rand -base64 32`; rotating it invalidates every stored token and forces a re-consent (there is no re-encryption path in J3 — a deliberate omission).
- Every existing director must sign out and back in once after this ships.

- [ ] **Step 3: Full suite and quality gates**

```bash
npm test 2>&1 | tail -5
npx tsc --noEmit
npm run lint
npm run build
```

Expected: all green; test count is the Task 0 baseline plus roughly 90 new tests.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-08-02-completion-roadmap.md docs/GMAIL-SETUP.md .env.example
git commit -m "docs(J3): correct the roadmap estimate and document Gmail setup"
```

---

## Manual end-to-end verification

Automated tests never touch real Gmail. Run this by hand before calling J3 done — it is the success criterion from the confirmed intent, and every step must be observed, not assumed.

- [ ] `npm run dev`, sign out, sign in, accept the Gmail consent screen.
- [ ] Ask the agent something that makes it use `ask_user` (e.g. "draft an intro email to Sarah" with two Sarahs in memory). **Observe:** the card appears, the composer is disabled.
- [ ] **Reload the page before answering.** The card is still there. This is the whole point of suspend-and-resume; if it is not, stop.
- [ ] Answer it. The run resumes and continues in the same thread.
- [ ] When the approval card appears, click **Always allow for `<address>`**. Check the address in Gmail's Drafts folder — the draft exists, and is **not** sent.
- [ ] Ask for a second draft to the **same** recipient. **Observe:** no approval card.
- [ ] Ask for a draft to a **different** recipient. **Observe:** the approval card returns.
- [ ] `curl -s localhost:3000/api/grants` (with the session cookie) lists exactly one grant. `DELETE` it, then ask for that recipient again — the card returns.
- [ ] Deny an approval. **Observe:** no draft is created, and the agent says so rather than retrying.
- [ ] With a card open, reload and type a **new question** instead of answering. **Observe:** the run continues normally — no provider error. (This is `cancelStalePending`; a failure here wedges the session permanently.)

---

## Judgment calls, recorded

1. **A suspended run is `failed` + `errorType: "awaiting_user"`.** `runs.status` has a shipped `CHECK` constraint with no `suspended` value; altering it was not worth a migration for a status only the trace UI reads. **Consequence:** K2's regression scoreboard must exclude `error_type = 'awaiting_user'` from failure rates, or every human-in-the-loop run counts as a failure.
2. **The resumed run is a new `runs` row.** Simpler than reopening a closed run, and honest — the two halves happened at different times. They are joined by the shared `chat_sessions` transcript, not by a FK. If a single trace view matters later, add `runs.resumed_from_run_id`.
3. **Tool calls after a suspend do not execute.** They get a `not_executed` result. The alternative — running them while the director is deciding — does work they never agreed to.
4. **Grants are scoped to `(actor, scope_kind, scope_id)` with `scope_kind='session'`.** Fisher chose option A over the leaner `session_id` column so E1's routine scope needs no migration. One extra column, no abstraction.
5. **`ask_user` is `read` class.** It writes nothing and must never be gated, since it is the gate's own escape hatch.
6. **`enrich` is not an approval class.** It spends credits but writes nothing outward; the enrich spend ceiling ticket governs it.
7. **No `googleapis` dependency.** Two `fetch` calls against documented REST endpoints, keeping `tests/package-deps.test.ts` green.
8. **No key-rotation path for `TOKEN_ENCRYPTION_KEY`.** Rotating it invalidates stored tokens and forces a re-consent. With one director that is a minute of inconvenience; a re-encryption migration is speculative work today.
9. **No Settings→Approvals page.** Fisher's explicit call. The revoke API exists so a standing permission is never psql-only.

## Known gaps to carry forward

- A run suspended by an unattended routine has nowhere to surface until someone opens that session. Fine now; **E1 owns it** when routines exist. This is the concrete cost of choosing an inline card over an Inbox.
- No timeout on an unanswered question — a suspended run waits forever. Deliberate; revisit when routines can suspend.
- Gmail draft bodies are model-authored plain text. No template, no signature, no HTML.
- Approve-to-send remains out of scope; `gmail.send` is deliberately not requested.

## Self-review

**Spec coverage** — every line of the confirmed intent maps to a task: suspend/resume across requests (6, 7, 8, 9); one mechanism, two kinds (4); grants table with `scope_kind` (1, 2); fail-closed `grantEntries` at `executeTool` (2, 4); `gmail.compose` + token storage (10, 11); `draft_email` with `targetArg: "to"` (13); inline card, both shapes (15); reload survival (16); revoke as API + tests, no page (17). Out-of-scope items (Inbox, settings page, routine grants, approve-to-send, timeouts) appear only in "Known gaps".

**Placeholder scan** — no TBDs; every code step carries the actual code. Task 3b/5b/13b ("register it in `answer-config.ts`") and Task 18's doc bullets are the only prose-only steps, and each names the exact file and the exact change.

**Type consistency** — `PendingQuestionView` is defined once (Task 8) and consumed by 14, 15, 16. `SuspendRequest` is defined once (Task 4) and consumed by 5, 6, 8. `insertIndex` + `partialResults` are written in 6, stored in 1/3, and read in 8 (`cancelStalePending`) and 9 (`resolveAndBuildResumeMessage`) — all four use `blocks.splice(insertIndex, 0, block)`. `grantScope` threads `route → answerWithMemory → runAgent → runAgentLoop → executeTool` with the same name at every hop.
