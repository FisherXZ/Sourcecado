# R6 — Server-Side Chat Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist chat history to Postgres so `/chat` resumes the actor's latest
session on page load and survives a reload, with a minimal "New chat" control
that starts a fresh session — no session management UI (rename/delete/list)
and no client-held session id.

**Depends on:** R5 (streaming rewire of `/api/agent/stream` + `ChatClient.tsx`)
must be merged first; R5 itself depends on R4 → R2 → R1 → R0. This plan's
Tasks 1–3 (migration, `src/lib/chat/sessions.ts`, `src/app/chat/resume.ts`) do
**not** import anything from `agent-loop.ts`/`context.ts`/the route, and have
no runtime dependency on R1–R5 — they can be built and merged independently.
Task 2 does a **type-only** import of `LlmMessage` from `src/lib/llm/types.ts`
(R1), so that file must exist for the code to compile. Tasks 4–6 edit files
R5 rewrites, so they require R5's merged state to exist before starting.

## Context

Per the contracts brief (`docs/superpowers/plans/2026-07-14-r-contracts-brief.md`,
§6), R6 owns:
- `src/migrations/005_chat_sessions.sql` (new) — schema given verbatim in the
  brief; do not modify it.
- `src/lib/chat/sessions.ts` (new) — `getOrCreateLatestSession`,
  `appendMessages`, `loadSessionMessages` (brief's named functions), plus
  `createSession` (this plan's addition, needed for the "new chat" path —
  see Judgment calls).
- `src/app/api/agent/stream/route.ts` — add session load/save on top of
  R5's rewrite.
- `src/app/chat/page.tsx` — resume-latest + "New chat" button only.
- One additive field each on R2's `RunAgentInput` (`harness.ts`) and
  `AnswerWithMemoryInput` (`memory/answer.ts`) — `priorMessages?:
  LlmMessage[]` — since the existing `history?: ConversationTurn[]` field
  can't carry loaded tool-use/tool-result blocks (Judgment call 8).

Today (pre-R5), `/chat/page.tsx` is a trivial client-only shell
(`<ChatClient />`, no props), `ChatClient.tsx` holds all conversation state in
`useState` seeded empty, and `/api/agent/stream/route.ts` takes
`{question, history}` where `history` is resent by the client every turn.
After R5, `ChatClient.tsx` and the route are rewritten to stream the typed
`LlmStreamEvent` union (contracts brief §5), but the **shape stays
conversationally the same**: no server-side persistence yet, no props into
`ChatClient`. This plan assumes R5 lands with that architecture (route calls
`answerWithMemory` — which wraps R2's `runAgent`/`harness.ts` and does its own
`startRun`/`startRunStep(kind:"agent")` internally, so the route itself never
calls those directly — passing `question`/`history`/`onStep`/
`onAgentLoopEvent`, and reads back a single `MemoryAnswer` result carrying
`runId` and, per R4's additive field, `messages`). **Task 4 states this
assumption again and gives fallback instructions** in case R5's actual merged
code names things differently — the persistence *contract* (load prior
non-system messages before assembling the turn's input, persist the user
message immediately, persist the loop's produced messages after it settles)
is what must hold, not the exact variable names.

Actor resolution follows the existing v1 single-tenant pattern used
throughout the memory layer (`src/lib/memory/actor.ts`'s `DEFAULT_ACTOR`,
also used unmodified by `src/lib/memory/sources.ts` and the memory API
routes) — no auth/session-cookie work in this slice.

## Judgment calls

1. **Session id transport (brief's own listed open item for R6):** the server
   never receives a session id from the client. Every `/api/agent/stream`
   request resolves the session via `getOrCreateLatestSession(db,
   DEFAULT_ACTOR)` — for this single-tenant actor there is exactly one
   "current" session at a time. "New chat" doesn't need to tell the server
   which session to use; it just needs to make a *new* session become the
   latest one (highest `updated_at`), which `createSession` does as a side
   effect of insertion (`updated_at` defaults to `now()`). This keeps the
   request/response JSON contract of `/api/agent/stream` completely
   unchanged from R5 — no `sessionId` field added anywhere.
2. **"New chat" mechanism:** a plain server-rendered link,
   `<a href="/chat?new=1">New chat</a>`, in `page.tsx`. Following it causes
   `page.tsx` (an async Server Component) to call `createSession` before
   rendering, then `redirect("/chat")` (Next's `next/navigation` redirect) so
   the URL doesn't retain `?new=1` and a manual refresh doesn't re-trigger
   session creation. No client-side fetch, no new API route.
3. **`ChatClient.tsx` touch (deviation from the brief's file-ownership
   table):** the table lists `ChatClient.tsx` under R0/R5 only, but the
   brief's own "Open items" section leaves R6's resume-wiring undecided, and
   *some* prop has to carry resumed history from the server component into
   the client tree. This plan adds exactly one optional, additive prop —
   `initialExchanges?: ResumedExchange[]` — where `ResumedExchange` is a
   small `{ question: string; answer: string }` DTO defined in a new file
   (`src/app/chat/resume.ts`), not the internal `Exchange` shape R5 owns.
   This keeps the surface area minimal and decoupled from whatever R5 does
   internally to `Exchange`. Flagging this explicitly per the brief's own
   instruction ("deviation must be called out... don't silently diverge").
4. **Resumed turns don't replay the reasoning trace or run-meta footer.**
   `chat_messages` stores only `LlmMessage`s (question text + assistant text
   + tool blocks), not the `ChatStep`/`ChatMeta` view-model R5's UI renders
   live. Reconstructing a byte-for-byte reasoning trace from raw
   `tool_use`/`tool_result` blocks is out of scope for "minimal resume" — a
   resumed exchange shows the question and the concatenated assistant text,
   nothing else (no "N steps" footer, no "View trace" link). This matches
   the spec's explicit "no management UI" cut and acceptance criterion #7
   ("chat survives a page reload"), which only requires the conversation
   content to survive, not the trace UI.
5. **System messages are never persisted.** The memory index (R4) is
   rebuilt fresh per request from a live SQL query — storing a turn-1 system
   message and reloading it on turn 5 would serve a stale index. The
   migration's `role` CHECK still allows `'system'` for schema
   completeness/future use, but this slice's code path never inserts one;
   `loadSessionMessages` only ever returns `user`/`assistant`/`tool_result`
   rows in practice, and `resume.ts`'s mapper skips a `system` row
   defensively if one is ever found.
6. **`run_id` on persisted rows:** `user` (and `system`, never used) rows
   always get `run_id = NULL` — they're recorded before any run starts.
   `assistant`/`tool_result` rows get the run's id. `appendMessages` enforces
   this itself (ignores a passed-in `runId` for `user`/`system` roles)
   rather than trusting every call site to remember, since the schema
   comment ("nullable since system/user rows precede any run") describes an
   invariant, not a suggestion.
7. **Task 4's "persist the loop's produced messages" depends on additive
   fields R2/R4 add, not a `run` object.** `/api/agent/stream` (post-R5)
   calls `answerWithMemory`, whose return value (`MemoryAnswer`) has no
   `run`/`messages` field on `main` today. This plan requires R2's
   `RunAgentResult.messages` (sourced from `AgentLoopResult.messages`) and
   R4's pass-through of that same field onto `MemoryAnswer` (see those
   plans' respective Judgment calls) to have landed first. Task 4 reads
   `result.messages` and `result.runId` off that one `MemoryAnswer` value —
   there is no separate `run` object anywhere in this call chain.
8. **`priorMessages` can't travel through `history?: ConversationTurn[]`,
   so it gets its own additive field.** `RunAgentInput`/`AnswerWithMemoryInput`
   (`src/lib/harness.ts`, `src/lib/memory/answer.ts` — confirmed live, and
   per the contracts brief §3 this shape "survives byte-for-byte" through
   R2) only accept `history?: ConversationTurn[]`, where `content` is a plain
   `string`. `loadSessionMessages` (Task 2) returns full `LlmMessage[]`,
   which can contain `LlmAssistantMessage`s with `tool_use` blocks and
   `LlmToolResultMessage`s — neither fits a string-only `ConversationTurn`
   (proven by Task 2's own round-trip test). Rather than downgrade
   `priorMessages` into text and lose tool call/result fidelity on every
   resumed turn that used a tool, this plan adds one additive, optional
   field — `priorMessages?: LlmMessage[]` — to both `RunAgentInput` and
   `AnswerWithMemoryInput`, mirroring R5's own precedent for
   `onAgentLoopEvent` (an additive-only edit to an R2-owned file, flagged
   per the brief's escape hatch — brief §7, "don't silently diverge"). Task
   4 threads it straight into `runAgentLoop`'s `messages[]`, immediately
   before the new user message, with no reshaping — full fidelity survives
   a resumed turn. This keeps Edit 3's produced-message slicing index-based
   (see Task 4), just computed from a length the route already knows
   (`priorMessages.length + 2` for the system + user messages) instead of
   an array the route assembles itself.

## Tasks

### Task 1: Migration `005_chat_sessions.sql`

**Build:** Create the migration exactly as specified in the contracts brief
§6 (do not modify the SQL — it's locked).

**Files:**
- Create: `src/migrations/005_chat_sessions.sql`
- Create: `tests/chat-sessions-migrate.test.ts`

**Step 1 — write the migration file:**

```sql
-- 005_chat_sessions.sql — R6 server-side chat session persistence.
-- content_json stores exactly the `content` field of the corresponding
-- LlmMessage variant (a string for system/user; an array of
-- LlmAssistantBlock/LlmToolResultBlock for assistant/tool_result), so
-- round-tripping is a direct { role, content: row.content_json } reassembly.
-- run_id is nullable since system/user rows precede any run.

CREATE TABLE IF NOT EXISTS chat_sessions (
  id            BIGSERIAL PRIMARY KEY,
  actor_type    TEXT NOT NULL,
  actor_id      TEXT NOT NULL,
  title         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            BIGSERIAL PRIMARY KEY,
  session_id    BIGINT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool_result')),
  content_json  JSONB NOT NULL,
  run_id        BIGINT REFERENCES runs(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages(session_id, id);
CREATE INDEX IF NOT EXISTS chat_sessions_actor_idx ON chat_sessions(actor_type, actor_id, updated_at DESC);
```

**Step 2 — write the migration test** (mirrors `tests/memory-migrate.test.ts`):

Create `tests/chat-sessions-migrate.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";

async function resetChatTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS chat_messages CASCADE`;
  await db`DROP TABLE IF EXISTS chat_sessions CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '005_chat_sessions.sql'`;
  await runMigrations(db);
}

describe("005 chat sessions migration", () => {
  beforeEach(async () => {
    await resetChatTables();
  });

  afterAll(async () => {
    await closeDb();
  });

  it("creates chat_sessions and chat_messages", async () => {
    const db = getDb();
    const result = await db<{ table_name: string }[]>`
      SELECT table_name FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name IN ('chat_sessions', 'chat_messages')
      ORDER BY table_name
    `;
    expect(result.map((r) => r.table_name)).toEqual(["chat_messages", "chat_sessions"]);
  });

  it("chat_messages.role rejects a value outside the allowed set", async () => {
    const db = getDb();
    const [session] = await db<{ id: number }[]>`
      INSERT INTO chat_sessions (actor_type, actor_id) VALUES ('test_client', 'x') RETURNING id
    `;
    await expect(
      db`INSERT INTO chat_messages (session_id, role, content_json) VALUES (${session.id}, 'bogus', '"x"')`
    ).rejects.toThrow();
  });

  it("chat_messages_session_idx and chat_sessions_actor_idx exist", async () => {
    const db = getDb();
    const result = await db<{ indexname: string }[]>`
      SELECT indexname FROM pg_indexes
      WHERE schemaname = 'public'
        AND indexname IN ('chat_messages_session_idx', 'chat_sessions_actor_idx')
    `;
    expect(result.map((r) => r.indexname).sort()).toEqual([
      "chat_messages_session_idx",
      "chat_sessions_actor_idx",
    ]);
  });

  it("deleting a session cascades to its messages", async () => {
    const db = getDb();
    const [session] = await db<{ id: number }[]>`
      INSERT INTO chat_sessions (actor_type, actor_id) VALUES ('test_client', 'x') RETURNING id
    `;
    await db`INSERT INTO chat_messages (session_id, role, content_json) VALUES (${session.id}, 'user', '"hi"')`;
    await db`DELETE FROM chat_sessions WHERE id = ${session.id}`;
    const remaining = await db`SELECT 1 FROM chat_messages WHERE session_id = ${session.id}`;
    expect(remaining).toHaveLength(0);
  });

  it("a run being deleted sets chat_messages.run_id to NULL, not blocking the delete", async () => {
    const db = getDb();
    const [run] = await db<{ id: number }[]>`
      INSERT INTO runs (run_type, status) VALUES ('agent_chat_stream', 'succeeded') RETURNING id
    `;
    const [session] = await db<{ id: number }[]>`
      INSERT INTO chat_sessions (actor_type, actor_id) VALUES ('test_client', 'x') RETURNING id
    `;
    const [message] = await db<{ id: number }[]>`
      INSERT INTO chat_messages (session_id, role, content_json, run_id)
      VALUES (${session.id}, 'assistant', '[]', ${run.id}) RETURNING id
    `;
    await db`DELETE FROM runs WHERE id = ${run.id}`;
    const [row] = await db<{ run_id: number | null }[]>`
      SELECT run_id FROM chat_messages WHERE id = ${message.id}
    `;
    expect(row.run_id).toBeNull();
  });
});
```

**Acceptance criteria:**
- `npx vitest run tests/chat-sessions-migrate.test.ts` passes (5 tests).
- Migration file matches the brief's SQL verbatim (no added columns, no
  renamed constraints).

**Verify:** `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-sessions-migrate.test.ts`

---

### Task 2: `src/lib/chat/sessions.ts`

**Build:** the session-lifecycle and message-persistence functions.

**Files:**
- Create: `src/lib/chat/sessions.ts`
- Create: `tests/chat-sessions.test.ts`

**Step 1 — write the failing tests.**

Create `tests/chat-sessions.test.ts`:

```ts
import { closeDb, getDb } from "@/lib/db";
import { runMigrations } from "@/lib/migrate";
import type { MemoryActor } from "@/lib/memory/actor";
import type { LlmAssistantMessage, LlmToolResultMessage, LlmUserMessage } from "@/lib/llm/types";
import {
  appendMessages,
  createSession,
  getOrCreateLatestSession,
  loadSessionMessages,
} from "@/lib/chat/sessions";

async function resetChatTables(): Promise<void> {
  const db = getDb();
  await db`DROP TABLE IF EXISTS chat_messages CASCADE`;
  await db`DROP TABLE IF EXISTS chat_sessions CASCADE`;
  await db`DELETE FROM schema_migrations WHERE name = '005_chat_sessions.sql'`;
  await runMigrations(db);
}

const ACTOR: MemoryActor = { actorType: "test_client", actorId: "chat-sessions-test" };
const OTHER_ACTOR: MemoryActor = { actorType: "test_client", actorId: "other-actor" };

describe("chat session persistence", () => {
  beforeEach(async () => {
    await resetChatTables();
  });
  afterAll(async () => {
    await closeDb();
  });

  it("createSession always inserts a new row", async () => {
    const db = getDb();
    const a = await createSession(db, ACTOR);
    const b = await createSession(db, ACTOR);
    expect(a.id).not.toBe(b.id);
  });

  it("getOrCreateLatestSession creates one when none exists", async () => {
    const db = getDb();
    const session = await getOrCreateLatestSession(db, ACTOR);
    const rows = await db`SELECT id FROM chat_sessions WHERE actor_type = ${ACTOR.actorType} AND actor_id = ${ACTOR.actorId}`;
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe(session.id);
  });

  it("getOrCreateLatestSession returns the most recently updated session, not just most recently created", async () => {
    const db = getDb();
    const older = await createSession(db, ACTOR);
    await new Promise((r) => setTimeout(r, 10));
    const newer = await createSession(db, ACTOR);
    // touch `older` after `newer` was created so it becomes latest by updated_at
    await appendMessages(db, older.id, [{ role: "user", content: "hi" } as LlmUserMessage]);

    const latest = await getOrCreateLatestSession(db, ACTOR);
    expect(latest.id).toBe(older.id);
    expect(latest.id).not.toBe(newer.id);
  });

  it("sessions are isolated per actor", async () => {
    const db = getDb();
    const mine = await createSession(db, ACTOR);
    await createSession(db, OTHER_ACTOR);
    const latest = await getOrCreateLatestSession(db, ACTOR);
    expect(latest.id).toBe(mine.id);
  });

  it("appendMessages + loadSessionMessages round-trip every LlmMessage variant", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const userMsg: LlmUserMessage = { role: "user", content: "tell me about acme" };
    const assistantMsg: LlmAssistantMessage = {
      role: "assistant",
      content: [
        { type: "text", text: "Let me check." },
        { type: "tool_use", id: "call_1", name: "search_memory", input: { query: "acme" } },
      ],
    };
    const toolResultMsg: LlmToolResultMessage = {
      role: "tool_result",
      content: [{ toolUseId: "call_1", toolName: "search_memory", content: "found 2 facts", isError: false }],
    };

    await appendMessages(db, session.id, [userMsg]);
    await appendMessages(db, session.id, [assistantMsg, toolResultMsg], 42);

    const loaded = await loadSessionMessages(db, session.id);
    expect(loaded).toEqual([userMsg, assistantMsg, toolResultMsg]);
  });

  it("forces run_id to NULL for user/system rows even if a runId is passed", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    await appendMessages(db, session.id, [{ role: "user", content: "hi" } as LlmUserMessage], 99);
    const rows = await db<{ run_id: number | null }[]>`SELECT run_id FROM chat_messages WHERE session_id = ${session.id}`;
    expect(rows[0].run_id).toBeNull();
  });

  it("appendMessages bumps chat_sessions.updated_at", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const [before] = await db<{ updated_at: Date }[]>`SELECT updated_at FROM chat_sessions WHERE id = ${session.id}`;
    await new Promise((r) => setTimeout(r, 10));
    await appendMessages(db, session.id, [{ role: "user", content: "hi" } as LlmUserMessage]);
    const [after] = await db<{ updated_at: Date }[]>`SELECT updated_at FROM chat_sessions WHERE id = ${session.id}`;
    expect(after.updated_at.getTime()).toBeGreaterThan(before.updated_at.getTime());
  });

  it("appendMessages is a no-op for an empty array", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    await expect(appendMessages(db, session.id, [])).resolves.toBeUndefined();
    const rows = await db`SELECT 1 FROM chat_messages WHERE session_id = ${session.id}`;
    expect(rows).toHaveLength(0);
  });

  it("loadSessionMessages returns rows in insertion order", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    await appendMessages(db, session.id, [{ role: "user", content: "first" } as LlmUserMessage]);
    await appendMessages(db, session.id, [{ role: "user", content: "second" } as LlmUserMessage]);
    const loaded = await loadSessionMessages(db, session.id);
    expect(loaded.map((m) => m.content)).toEqual(["first", "second"]);
  });
});
```

**Step 2 — run to verify it fails:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-sessions.test.ts`
Expected: FAIL — cannot find module `@/lib/chat/sessions`.

**Step 3 — write `src/lib/chat/sessions.ts`:**

```ts
import type postgres from "postgres";
import type { Sql } from "../tools/types";
import { DEFAULT_ACTOR, type MemoryActor } from "../memory/actor";
import type { LlmMessage } from "../llm/types";

export interface ChatSession {
  id: number;
}

// Always inserts a fresh row — used for the "new chat" path, where the whole
// point is a session that did NOT exist a moment ago.
export async function createSession(db: Sql, actor: MemoryActor = DEFAULT_ACTOR): Promise<ChatSession> {
  const [row] = await db<{ id: number }[]>`
    INSERT INTO chat_sessions (actor_type, actor_id)
    VALUES (${actor.actorType}, ${actor.actorId})
    RETURNING id
  `;
  return { id: Number(row.id) };
}

// Resume-latest-or-create-new: the actor's most recently updated session, or
// a brand new one if they have none yet.
export async function getOrCreateLatestSession(db: Sql, actor: MemoryActor = DEFAULT_ACTOR): Promise<ChatSession> {
  const [existing] = await db<{ id: number }[]>`
    SELECT id FROM chat_sessions
    WHERE actor_type = ${actor.actorType} AND actor_id = ${actor.actorId}
    ORDER BY updated_at DESC
    LIMIT 1
  `;
  if (existing) return { id: Number(existing.id) };
  return createSession(db, actor);
}

// Persists messages in order and bumps chat_sessions.updated_at. `runId`
// tags assistant/tool_result rows; user/system rows always get NULL run_id
// regardless of what's passed, since they're recorded before any run starts.
// Wrapped in one transaction (mirrors the db.begin pattern in migrate.ts /
// memory/notes.ts) so a multi-row call (e.g. an assistant tool_use message
// plus its paired tool_result message) can't persist half-written — a
// dropped INSERT mid-call would otherwise leave an unpaired tool_use row
// that every future turn re-threads into the model, which providers reject.
export async function appendMessages(
  db: Sql,
  sessionId: number,
  messages: LlmMessage[],
  runId?: number
): Promise<void> {
  if (messages.length === 0) return;

  await db.begin(async (tx) => {
    for (const message of messages) {
      const rowRunId = message.role === "user" || message.role === "system" ? null : (runId ?? null);
      await tx`
        INSERT INTO chat_messages (session_id, role, content_json, run_id)
        VALUES (${sessionId}, ${message.role}, ${toJson(tx, message.content)}, ${rowRunId})
      `;
    }
    await tx`UPDATE chat_sessions SET updated_at = now() WHERE id = ${sessionId}`;
  });
}

// SELECT role, content_json FROM chat_messages WHERE session_id = $1 ORDER BY
// id, then rows.map(r => ({ role: r.role, content: r.content_json }) as
// LlmMessage) — direct reassembly, no reshaping (brief §6).
export async function loadSessionMessages(db: Sql, sessionId: number): Promise<LlmMessage[]> {
  const rows = await db<{ role: string; content_json: unknown }[]>`
    SELECT role, content_json FROM chat_messages WHERE session_id = ${sessionId} ORDER BY id
  `;
  return rows.map((r) => ({ role: r.role, content: r.content_json }) as LlmMessage);
}

function toJson(db: Sql, value: unknown) {
  return db.json(value as postgres.JSONValue);
}
```

**Step 4 — run to verify it passes:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-sessions.test.ts`
Expected: PASS (9 tests).

**Acceptance criteria:**
- All 9 tests in `tests/chat-sessions.test.ts` pass.
- `loadSessionMessages` output for a round-tripped message is `toEqual` the
  original `LlmMessage` object (no extra fields, no reshaping).

**Verify:** `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-sessions.test.ts tests/chat-sessions-migrate.test.ts`

---

### Task 3: `src/app/chat/resume.ts` — messages → resumed exchanges

**Build:** a pure function that folds a session's `LlmMessage[]` into
`{question, answer}` pairs for display, without replaying the reasoning
trace (Judgment call 4).

**Files:**
- Create: `src/app/chat/resume.ts`
- Create: `tests/chat-resume.test.ts`

**Step 1 — write the failing tests.**

Create `tests/chat-resume.test.ts`:

```ts
import type { LlmAssistantMessage, LlmMessage, LlmToolResultMessage, LlmUserMessage } from "@/lib/llm/types";
import { mapMessagesToResumedExchanges } from "@/app/chat/resume";

function user(content: string): LlmUserMessage {
  return { role: "user", content };
}
function assistantText(text: string): LlmAssistantMessage {
  return { role: "assistant", content: [{ type: "text", text }] };
}
function assistantToolUse(text: string, id: string, name: string): LlmAssistantMessage {
  return { role: "assistant", content: [{ type: "text", text }, { type: "tool_use", id, name, input: {} }] };
}
function toolResult(id: string, name: string, content: string): LlmToolResultMessage {
  return { role: "tool_result", content: [{ toolUseId: id, toolName: name, content, isError: false }] };
}

describe("mapMessagesToResumedExchanges", () => {
  it("returns an empty list for no messages", () => {
    expect(mapMessagesToResumedExchanges([])).toEqual([]);
  });

  it("pairs a single question with its answer", () => {
    const messages: LlmMessage[] = [user("hi there"), assistantText("hello!")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "hi there", answer: "hello!" }]);
  });

  it("concatenates assistant text across a tool-use turn, skipping tool_result content", () => {
    const messages: LlmMessage[] = [
      user("tell me about acme"),
      assistantToolUse("Let me check.", "call_1", "search_memory"),
      toolResult("call_1", "search_memory", "found 2 facts"),
      assistantText("Acme is a Series B company."),
    ];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([
      { question: "tell me about acme", answer: "Let me check. Acme is a Series B company." },
    ]);
  });

  it("handles multiple turns in one session", () => {
    const messages: LlmMessage[] = [
      user("first question"),
      assistantText("first answer"),
      user("second question"),
      assistantText("second answer"),
    ];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([
      { question: "first question", answer: "first answer" },
      { question: "second question", answer: "second answer" },
    ]);
  });

  it("includes a trailing turn even without a following user message", () => {
    const messages: LlmMessage[] = [user("only question"), assistantText("only answer")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "only question", answer: "only answer" }]);
  });

  it("skips a system message defensively (never persisted in practice)", () => {
    const messages: LlmMessage[] = [{ role: "system", content: "instructions" }, user("q"), assistantText("a")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "q", answer: "a" }]);
  });

  it("ignores an orphaned assistant/tool_result message with no preceding user turn", () => {
    const messages: LlmMessage[] = [assistantText("stray"), user("q"), assistantText("a")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "q", answer: "a" }]);
  });
});
```

**Step 2 — run to verify it fails:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-resume.test.ts`
Expected: FAIL — cannot find module `@/app/chat/resume`.

**Step 3 — write `src/app/chat/resume.ts`:**

```ts
import type { LlmMessage } from "@/lib/llm/types";

export interface ResumedExchange {
  question: string;
  answer: string;
}

// Folds a session's persisted transcript into display-only {question,
// answer} pairs. Deliberately does not reconstruct the reasoning trace
// (ChatStep[]) or run meta (ChatMeta) — chat_messages doesn't store either,
// and R6 is a minimal resume, not a trace replay (see plan Judgment call 4).
export function mapMessagesToResumedExchanges(messages: LlmMessage[]): ResumedExchange[] {
  const exchanges: ResumedExchange[] = [];
  let current: { question: string; answerParts: string[] } | null = null;

  for (const message of messages) {
    if (message.role === "system") continue;

    if (message.role === "user") {
      if (current) exchanges.push({ question: current.question, answer: current.answerParts.join(" ") });
      current = { question: message.content, answerParts: [] };
      continue;
    }

    if (!current) continue; // orphaned assistant/tool_result with no preceding question

    if (message.role === "assistant") {
      for (const block of message.content) {
        if (block.type === "text" && block.text) current.answerParts.push(block.text);
      }
    }
    // tool_result messages contribute nothing to the displayed answer text.
  }

  if (current) exchanges.push({ question: current.question, answer: current.answerParts.join(" ") });
  return exchanges;
}
```

**Step 4 — run to verify it passes:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-resume.test.ts`
Expected: PASS (7 tests).

**Acceptance criteria:**
- All 7 tests in `tests/chat-resume.test.ts` pass.
- `mapMessagesToResumedExchanges` never throws on malformed/orphaned input.

**Verify:** `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-resume.test.ts`

---

### Task 4: Wire session load/save into `/api/agent/stream/route.ts`

**Precondition:** R5 merged. Read the actual merged
`src/app/api/agent/stream/route.ts` before starting this task — the sketch
below is this plan's best-effort concrete guess at R5's shape (per R5's own
plan: the route calls `answerWithMemory`, forwarding `onAgentLoopEvent` for
live streaming, and reads the single `MemoryAnswer` it returns — ledger
`startRun`/`startRunStep(kind:"agent")` happen inside `runAgent`/`harness.ts`,
not in the route). **If the actual file differs in variable/function names,
apply the same three edits at the equivalent points — the contract that must
hold is:**
1. Load this actor's prior non-system messages **before** the turn's input
   messages are assembled.
2. Persist the new user message **immediately** (before invoking the loop),
   so it's durable even if the loop later fails or the request aborts.
3. Persist the loop's newly-produced messages (tagged with the run id)
   **after** the loop settles, regardless of `status` (`"succeeded"` or
   `"failed"` — even a failed run's synthetic error message should show up on
   reload).

Per Judgment call 8, this task also adds one additive field —
`priorMessages?: LlmMessage[]` — to `RunAgentInput` (`src/lib/harness.ts`,
R2-owned) and `AnswerWithMemoryInput` (`src/lib/memory/answer.ts`), since the
existing `history?: ConversationTurn[]` field can't carry `tool_use`/
`tool_result` blocks. `runAgent` threads it into `messages[]` immediately
before the new user message; nothing else about either file's existing
behavior changes.

**Files:**
- Modify: `src/app/api/agent/stream/route.ts`
- Modify: `src/lib/harness.ts` (additive `priorMessages?` field on
  `RunAgentInput`, threaded into `messages[]`)
- Modify: `src/lib/memory/answer.ts` (additive `priorMessages?` field on
  `AnswerWithMemoryInput`, passed through to `runAgent`)
- Modify: the R5-owned route test file (`tests/agent-stream-route.test.ts` or
  whatever R5 renamed it to — check for it before creating a new one)
- Modify: whatever test file covers `runAgent`/`harness.ts` post-R2 (add one
  case proving a passed `priorMessages` array reaches the loop's `messages[]`
  input ahead of the new user message)

**Step 1 — write the failing tests.**

Add these cases to the route's test file (mocking `@/lib/chat/sessions`
alongside whatever R5 already mocks):

```ts
import { vi } from "vitest";

const { getOrCreateLatestSessionMock, loadSessionMessagesMock, appendMessagesMock } = vi.hoisted(() => ({
  getOrCreateLatestSessionMock: vi.fn(),
  loadSessionMessagesMock: vi.fn(),
  appendMessagesMock: vi.fn(),
}));
vi.mock("@/lib/chat/sessions", () => ({
  getOrCreateLatestSession: getOrCreateLatestSessionMock,
  loadSessionMessages: loadSessionMessagesMock,
  appendMessages: appendMessagesMock,
}));

// ... existing R5 mocks for the loop/gateway/ledger stay as-is ...

describe("session persistence", () => {
  beforeEach(() => {
    getOrCreateLatestSessionMock.mockReset().mockResolvedValue({ id: 7 });
    loadSessionMessagesMock.mockReset().mockResolvedValue([]);
    appendMessagesMock.mockReset().mockResolvedValue(undefined);
  });

  it("loads prior session messages before running the turn and threads them in as priorMessages", async () => {
    const prior = [
      { role: "user", content: "earlier question" },
      { role: "assistant", content: [{ type: "text", text: "earlier answer" }] },
    ];
    loadSessionMessagesMock.mockResolvedValue(prior);
    // ... invoke POST with a question, drain the stream (per R5's test helper) ...
    expect(loadSessionMessagesMock).toHaveBeenCalledWith(expect.anything(), 7);
    // answerWithMemoryMock is R5's existing mock for the `@/lib/memory/answer` module
    expect(answerWithMemoryMock).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ priorMessages: prior })
    );
  });

  it("persists the user message immediately and the produced messages after the turn, tagged with the run id", async () => {
    // ... invoke POST, drain the stream ...
    // first appendMessages call: just the new user message, no runId
    expect(appendMessagesMock.mock.calls[0][1]).toBe(7);
    expect(appendMessagesMock.mock.calls[0][2]).toEqual([{ role: "user", content: expect.any(String) }]);
    expect(appendMessagesMock.mock.calls[0][3]).toBeUndefined();
    // second appendMessages call: the loop's produced messages, tagged with the run id
    expect(appendMessagesMock.mock.calls[1][3]).toEqual(expect.any(Number));
  });

  it("still persists the turn's messages when the loop fails", async () => {
    // ... mock the loop/turn result as status "failed" ...
    // ... invoke POST, drain the stream ...
    expect(appendMessagesMock).toHaveBeenCalledTimes(2); // user message + failure message, same as success path
  });
});
```

(The exact request-driving/stream-draining boilerplate should match
whatever helper R5's test file already uses — reuse it rather than
reinventing a second SSE reader.)

**Step 2 — run to verify it fails:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-stream-route.test.ts`
Expected: FAIL — `@/lib/chat/sessions` doesn't exist yet as an import in the
route, so the new assertions have nothing to check against (mocks never
called).

**Step 3 — edit `src/lib/harness.ts`, `src/lib/memory/answer.ts`, and
`src/app/api/agent/stream/route.ts`.**

First, two small additive-field edits (Judgment call 8), then the route's
three edits, in order:

```ts
// --- harness.ts: additive field on RunAgentInput, threaded into messages[] ---
export interface RunAgentInput {
  question: string;
  // ...existing fields unchanged...
  history?: ConversationTurn[];
  // Full-fidelity prior messages for a resumed chat session (R6). Threaded
  // into messages[] immediately before the new user message — unlike
  // `history`, these are already LlmMessage-shaped, so no string-only
  // downgrade happens and tool_use/tool_result blocks survive intact.
  priorMessages?: LlmMessage[];
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
}

// wherever runAgent assembles messages[] (systemMessage + historyAsMessages + userMessage):
const messages: LlmMessage[] = [
  systemMessage,
  ...historyAsMessages,
  ...(input.priorMessages ?? []),
  userMessage,
];

// --- memory/answer.ts: pass-through additive field ---
export interface AnswerWithMemoryInput {
  question: string;
  history?: ConversationTurn[];
  priorMessages?: LlmMessage[];
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
}
// ... runAgent({ question: input.question, history: input.history, priorMessages: input.priorMessages, ... }) ...
```

```ts
// --- Edit 1: near the top of the handler, after `question` and `db` are resolved ---
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { appendMessages, getOrCreateLatestSession, loadSessionMessages } from "@/lib/chat/sessions";
import type { LlmMessage, LlmUserMessage } from "@/lib/llm/types";

// ... existing question/db resolution ...
const session = await getOrCreateLatestSession(db, DEFAULT_ACTOR);
const priorMessages: LlmMessage[] = await loadSessionMessages(db, session.id);
const userMessage: LlmUserMessage = { role: "user", content: question };
await appendMessages(db, session.id, [userMessage]); // durable even if the loop below fails/aborts

// --- Edit 2: pass priorMessages straight through to answerWithMemory —
// the route no longer assembles a raw messages[] array itself; that stays
// internal to runAgent/harness.ts (see the additive-field edit above) ---
const result = await answerWithMemory(db, {
  question,
  priorMessages,
  onStep,
  onAgentLoopEvent,
});

// --- Edit 3: after the loop settles (both success and failure paths), before
// the handler returns/closes the stream — `result` here is the
// `MemoryAnswer` returned by `answerWithMemory`; R4 added its additive
// `messages` field and it carries `runId` directly, so there is no separate
// `run` object. The prefix length is computed from what the route already
// knows (system message + priorMessages + the new user message), not from
// an array the route built itself, since that assembly now happens inside
// runAgent ---
const priorPrefixLength = priorMessages.length + 2; // systemMessage + priorMessages + userMessage
const producedMessages = result.messages.slice(priorPrefixLength);
await appendMessages(db, session.id, producedMessages, result.runId);
```

**Step 4 — run to verify it passes:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-stream-route.test.ts`
Expected: PASS (all prior R5 cases + the new session-persistence cases).

**Acceptance criteria:**
- A second `/api/agent/stream` request (mocked `loadSessionMessages`
  returning turn 1's messages) calls `answerWithMemory` with those messages
  as `priorMessages`, unmodified (no downgrade to `ConversationTurn[]`).
- `appendMessages` is called twice per request: once immediately with just
  the new user message (no `runId`), once after the loop settles with the
  produced messages tagged with the run id — on both success and failure.
- A `runAgent`/`harness.ts` test proves a passed `priorMessages` array lands
  in `messages[]` immediately before the new user message.
- The stream/route's request and response JSON shapes are otherwise
  unchanged from R5 (no `sessionId` field added — see Judgment call 1).

**Verify:** `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/agent-stream-route.test.ts`

---

### Task 5: `src/app/chat/page.tsx` — resume-latest + "New chat"

**Precondition:** R5 merged (`ChatClient.tsx` exists in its R5 form; Task 6
below adds the one new prop it needs).

**Build:** turn `page.tsx` into an async Server Component that resolves the
actor's session, loads and maps its messages, and renders `ChatClient` with
the resumed history. A `?new=1` search param forces a fresh session and
redirects back to the bare `/chat` URL.

**Files:**
- Modify: `src/app/chat/page.tsx`
- Create: `tests/chat-page.test.tsx`

**Step 1 — write the failing tests.**

Create `tests/chat-page.test.tsx`:

```ts
import { vi } from "vitest";

const { createSessionMock, getOrCreateLatestSessionMock, loadSessionMessagesMock, redirectMock } = vi.hoisted(() => ({
  createSessionMock: vi.fn(),
  getOrCreateLatestSessionMock: vi.fn(),
  loadSessionMessagesMock: vi.fn(),
  redirectMock: vi.fn(() => {
    throw new Error("NEXT_REDIRECT");
  }),
}));
vi.mock("@/lib/chat/sessions", () => ({
  createSession: createSessionMock,
  getOrCreateLatestSession: getOrCreateLatestSessionMock,
  loadSessionMessages: loadSessionMessagesMock,
}));
vi.mock("@/lib/db", () => ({ getDb: vi.fn().mockReturnValue({}) }));
vi.mock("next/navigation", () => ({ redirect: redirectMock }));

import ChatPage from "@/app/chat/page";
import { ChatClient } from "@/app/chat/ChatClient";

describe("ChatPage", () => {
  beforeEach(() => {
    createSessionMock.mockReset();
    getOrCreateLatestSessionMock.mockReset();
    loadSessionMessagesMock.mockReset();
    redirectMock.mockClear();
  });

  it("resumes the latest session and passes its mapped history into ChatClient", async () => {
    getOrCreateLatestSessionMock.mockResolvedValue({ id: 5 });
    loadSessionMessagesMock.mockResolvedValue([
      { role: "user", content: "hi" },
      { role: "assistant", content: [{ type: "text", text: "hello" }] },
    ]);

    const element = await ChatPage({ searchParams: Promise.resolve({}) });
    expect(getOrCreateLatestSessionMock).toHaveBeenCalled();
    expect(createSessionMock).not.toHaveBeenCalled();

    const clientElement = findElementOfType(element, ChatClient);
    expect(clientElement?.props.initialExchanges).toEqual([{ question: "hi", answer: "hello" }]);
  });

  it("with ?new=1, creates a fresh session and redirects to /chat", async () => {
    createSessionMock.mockResolvedValue({ id: 9 });

    await expect(ChatPage({ searchParams: Promise.resolve({ new: "1" }) })).rejects.toThrow("NEXT_REDIRECT");
    expect(createSessionMock).toHaveBeenCalled();
    expect(getOrCreateLatestSessionMock).not.toHaveBeenCalled();
    expect(redirectMock).toHaveBeenCalledWith("/chat");
  });
});

// React elements expose their tree via .props.children; walk it to find a
// node whose type matches the given component.
function findElementOfType(node: unknown, type: unknown): { props: Record<string, unknown> } | null {
  if (node == null || typeof node !== "object") return null;
  const el = node as { type?: unknown; props?: { children?: unknown } };
  if (el.type === type) return el as { props: Record<string, unknown> };
  const children = el.props?.children;
  if (Array.isArray(children)) {
    for (const child of children) {
      const found = findElementOfType(child, type);
      if (found) return found;
    }
  } else if (children) {
    return findElementOfType(children, type);
  }
  return null;
}
```

**Step 2 — run to verify it fails:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-page.test.tsx`
Expected: FAIL — `page.tsx` doesn't accept `searchParams` yet / doesn't pass
`initialExchanges`.

**Step 3 — write `src/app/chat/page.tsx`:**

```tsx
import { redirect } from "next/navigation";
import { getDb } from "@/lib/db";
import { createSession, getOrCreateLatestSession, loadSessionMessages } from "@/lib/chat/sessions";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { mapMessagesToResumedExchanges } from "./resume";
import { ChatClient } from "./ChatClient";

export default async function ChatPage({
  searchParams,
}: {
  searchParams: Promise<{ new?: string }>;
}) {
  const { new: forceNew } = await searchParams;
  const db = getDb();

  if (forceNew) {
    await createSession(db, DEFAULT_ACTOR);
    redirect("/chat");
  }

  const session = await getOrCreateLatestSession(db, DEFAULT_ACTOR);
  const messages = await loadSessionMessages(db, session.id);
  const initialExchanges = mapMessagesToResumedExchanges(messages);

  return (
    <div className="mx-auto flex w-full max-w-3xl justify-end px-6 pt-4">
      <a href="/chat?new=1" className="text-[13px] text-accent-deep underline">
        New chat
      </a>
      <ChatClient initialExchanges={initialExchanges} />
    </div>
  );
}
```

Note: the "New chat" link and `<ChatClient>` currently can't both be direct
children of one flex row with `ChatClient`'s own full-height layout (it
renders its own `min-h-screen` wrapper) — nest correctly against whatever
layout `ChatClient` actually renders after R5; the link must render above/
outside `ChatClient`'s own header, not fight its flex layout. Adjust the
wrapper markup during implementation if `ChatClient`'s R5 root element
doesn't compose with a sibling this way (e.g. render the link inside
`ChatClient`'s existing `<header>` instead, by threading it as a prop, if a
sibling `<a>` visually collides).

**Step 4 — run to verify it passes:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-page.test.tsx`
Expected: PASS (2 tests).

**Acceptance criteria:**
- Loading `/chat` with an existing session resumes it (`getOrCreateLatestSession`
  called, `createSession` not called) and passes the mapped history to
  `ChatClient` via `initialExchanges`.
- Loading `/chat?new=1` calls `createSession` (not `getOrCreateLatestSession`)
  and redirects to `/chat`.
- A "New chat" link is present and points at `/chat?new=1`.

**Verify:** `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/chat-page.test.tsx`

---

### Task 6: `ChatClient.tsx` — accept `initialExchanges`

**Precondition:** R5 merged.

**Build:** the single additive prop needed to display resumed history (see
Judgment call 3). No other change to `ChatClient.tsx`'s R5 behavior.

**Files:**
- Modify: `src/app/chat/ChatClient.tsx`
- Modify: the R5-owned `tests/components/ChatClient.test.tsx`

**Step 1 — add a failing test.**

Append to `tests/components/ChatClient.test.tsx` (using whatever render
helper/mocks R5's version of the file already sets up):

```ts
it("seeds resumed exchanges from initialExchanges without calling runChat", () => {
  render(
    <ChatClient
      initialExchanges={[{ question: "earlier question", answer: "earlier answer" }]}
    />
  );
  expect(screen.getByText("earlier question")).toBeInTheDocument();
  expect(screen.getByText("earlier answer")).toBeInTheDocument();
  expect(runChatMock).not.toHaveBeenCalled();
});
```

**Step 2 — run to verify it fails:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/components/ChatClient.test.tsx`
Expected: FAIL — `initialExchanges` prop doesn't exist / isn't rendered.

**Step 3 — edit `src/app/chat/ChatClient.tsx`.**

Add the prop and seed the initial `exchanges` state from it. Locate
wherever R5 left `useState<Exchange[]>([])` (or its post-R5 equivalent) and
change it to seed from `initialExchanges`, mapping each `ResumedExchange`
into that state's shape as a settled (`done: true, open: false`), no-steps,
no-meta exchange:

```tsx
import type { ResumedExchange } from "./resume";

// ... inside the ChatClient function signature ...
export function ChatClient({ initialExchanges = [] }: { initialExchanges?: ResumedExchange[] }) {
  const [exchanges, setExchanges] = useState<Exchange[]>(() =>
    initialExchanges.map((resumed, index) => ({
      id: -(index + 1), // negative ids so they never collide with idRef's live-turn counter
      question: resumed.question,
      turn: { steps: [], answer: resumed.answer },
      open: false,
      done: true,
    }))
  );
  // ... rest of the component unchanged ...
}
```

Adjust field names (`turn`, `steps`, `done`, `open`) to match whatever
`Exchange` actually looks like in R5's merged file if it has changed shape —
the mapping principle (one settled, trace-free exchange per resumed pair) is
what must hold.

**Step 4 — run to verify it passes:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/components/ChatClient.test.tsx`
Expected: PASS (all prior R5 cases + the new resume case).

**Acceptance criteria:**
- Rendering `<ChatClient initialExchanges={[...]} />` shows the resumed
  question/answer pairs immediately, with no reasoning trace and no
  "View trace" meta footer (per Judgment call 4).
- Rendering `<ChatClient />` with no prop (all existing R5 tests) is
  unaffected — `initialExchanges` defaults to `[]`.
- `runChat` is not called just because `initialExchanges` was provided.

**Verify:** `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run tests/components/ChatClient.test.tsx`

---

### Task 7: Full verification

**Files:** none (verification only).

**Step 1 — run the full test suite:**
Run: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npx vitest run`
Expected: PASS — every prior suite plus the 5 new/modified R6 files
(`chat-sessions-migrate`, `chat-sessions`, `chat-resume`, the extended
`agent-stream-route`/R5-route test, the extended `ChatClient` test, and
`chat-page`) green.

**Step 2 — lint:**
Run: `npm run lint`
Expected: `✔ No ESLint warnings or errors`.

**Step 3 — build:**
Run: `npm run build`
Expected: build succeeds; `/chat` still listed as a route (now server-rendered
with a dynamic segment for `searchParams`, so expect it may report as `ƒ`
dynamic rather than static — that's correct, not a regression).

**Step 4 — manual smoke (acceptance criterion #7):**
Run in one terminal: `export DATABASE_URL="postgresql://sourcecado:sourcecado@localhost:5432/sourcecado"; npm run dev`
Then:
1. Open `http://localhost:3000/chat`, ask a question, wait for the answer.
2. Reload the page. Expected: the question/answer pair is still visible
   (loaded from Postgres), no reasoning trace replayed.
3. Click "New chat". Expected: navigates to `/chat` with an empty
   conversation.
4. Ask a new question, reload again. Expected: only the new-session
   question/answer shows, not the earlier session's.
5. Inspect `chat_sessions`/`chat_messages` directly (`psql` or the run
   inspector's DB) to confirm two distinct session rows exist with the
   expected message rows and `run_id` linkage.

**Step 5 — commit:**

```bash
git add src/migrations/005_chat_sessions.sql src/lib/chat/sessions.ts \
  src/app/chat/resume.ts src/app/chat/page.tsx src/app/chat/ChatClient.tsx \
  src/app/api/agent/stream/route.ts src/lib/harness.ts src/lib/memory/answer.ts \
  tests/chat-sessions-migrate.test.ts \
  tests/chat-sessions.test.ts tests/chat-resume.test.ts tests/chat-page.test.tsx \
  tests/agent-stream-route.test.ts tests/components/ChatClient.test.tsx
git commit -m "feat(r6): server-side chat sessions — resume latest + new chat"
```

---

## Tests

| File | New/Modified | Covers |
|---|---|---|
| `tests/chat-sessions-migrate.test.ts` | New | 005 migration: tables, CHECK constraint, indexes, CASCADE/SET NULL FKs |
| `tests/chat-sessions.test.ts` | New | `createSession`, `getOrCreateLatestSession` (latest-by-`updated_at`, per-actor isolation), `appendMessages` (round-trip, `run_id` enforcement, no-op on empty, `updated_at` bump), `loadSessionMessages` (order) |
| `tests/chat-resume.test.ts` | New | `mapMessagesToResumedExchanges`: empty, single turn, tool-use turn, multi-turn, trailing turn, system-skip, orphan-skip |
| `tests/agent-stream-route.test.ts` (or R5's renamed equivalent) | Modified | session load-before-turn, `priorMessages` passed to `answerWithMemory` unmodified, persist-immediately (user msg) + persist-after (produced msgs, tagged with run id), persistence on both success and failure |
| harness.ts/`runAgent` test file (R2's file) | Modified | passed `priorMessages` lands in `messages[]` immediately before the new user message |
| `tests/chat-page.test.tsx` | New | resume-latest path passes mapped history to `ChatClient`; `?new=1` path creates a session and redirects |
| `tests/components/ChatClient.test.tsx` (R5's file) | Modified | `initialExchanges` seeds settled, trace-free exchanges without calling `runChat` |

## Self-Review

**Spec/brief coverage:**
- Migration exactly as specified in brief §6 → Task 1.
- `getOrCreateLatestSession`/`appendMessages`/`loadSessionMessages` (brief's
  named functions) + `createSession` (this plan's addition, justified in
  Judgment calls) → Task 2.
- Stream route loads/saves (spec's R6 line) → Task 4.
- `/chat` resumes latest + "New chat" button, no management UI (spec's R6
  line) → Tasks 5–6.
- Acceptance criterion #7 ("chat survives a page reload") → Task 7 Step 4.
- No session management UI (rename/delete/list) added anywhere — confirmed
  absent from every task.

**Placeholder scan:** no TBD/TODO; every task's code/test steps are complete
except Task 4's route edit, which is explicitly and necessarily an
integration sketch against not-yet-existing R5 code (flagged, with a stated
fallback contract, rather than silently assumed).

**Deviations flagged back to the contracts brief:** `ChatClient.tsx` is
touched (Judgment call 3) despite the file-ownership table listing it under
R0/R5 only — this directly resolves the brief's own listed open item ("R6:
session id transport") the only way that's actually possible, and is called
out rather than silently diverged.

## Eng Review (2026-07-14)

**Verdict: approve (revised)**

Reviewed against the actual repo state (branch `feat/a-chat-streaming`;
`src/lib/harness.ts`, `src/app/api/agent/stream/route.ts`, `src/app/chat/*`
read directly — none of R0–R5 are merged yet, `src/lib/agent-loop.ts` /
`src/lib/llm/` / `src/lib/tools/orchestrator.ts` / `src/lib/context.ts` don't
exist, migrations stop at `003_archived.sql`) and against
`2026-07-14-r-contracts-brief.md`. Tasks 1–3 and 5–7 are solid: TDD-first,
concretely coded (not sketched), correctly scoped as buildable independently
of R1–R5, and consistent with the brief. Task 4 has one real feasibility gap
that blocks it as written, plus one durability gap worth closing before
merge.

### Must-fix

**RESOLVED** — see Judgment call 8 and the revised Task 4 (both items below
addressed inline; verdict updated to approve (revised)).

1. **Task 4's core mechanism doesn't connect to the real `runAgent`/
   `answerWithMemory` contract.** The task's Edit 2 assumes the route builds
   `const messages: LlmMessage[] = [systemMessage, ...priorMessages,
   userMessage]` and hands that to the loop. But the actual (and per R2/R5's
   own plans, "byte-for-byte" preserved) contract is `RunAgentInput.history?:
   ConversationTurn[]` / `AnswerWithMemoryInput.history?: ConversationTurn[]`
   — confirmed live today at `src/lib/harness.ts:38-41,54-64` and
   `src/lib/memory/answer.ts:15-17` — where `ConversationTurn.content` is a
   plain `string`. There is no `messages: LlmMessage[]` field anywhere on
   that path; the route never assembles a raw message array today, and
   nothing in R1/R2/R4/R5's contracts brief sections adds one. `priorMessages`
   as loaded by `loadSessionMessages` can contain `LlmAssistantMessage`s with
   `tool_use` blocks and `LlmToolResultMessage`s (proven by Task 2's own
   round-trip test), neither of which fits into a `{role, content: string}`
   `ConversationTurn`. So Task 4 as written either (a) doesn't compile against
   the real contract, or (b) silently requires downgrading `priorMessages`
   into text-only `ConversationTurn[]` — losing tool call/result content on
   every resumed turn that involved a tool call, which nobody decided or
   tested. This also breaks the `producedMessages = result.messages.slice(
   messages.length)` math (Edit 3): that only works if `result.messages` is
   index-aligned with the exact array the route threaded in, which no longer
   holds once history is round-tripped through a lossy `ConversationTurn`
   mapper inside `harness.ts`.
   **Fix:** add an explicit Judgment call and task step deciding one of:
   - Add an additive `priorMessages?: LlmMessage[]` (or similarly named)
     field to `RunAgentInput`/`AnswerWithMemoryInput`, threaded directly into
     `runAgentLoop`'s `messages[]` ahead of the new user message — mirroring
     R5's own precedent for `onAgentLoopEvent` (additive-only edit to an
     R2-owned file, flagged per the brief's escape hatch), with its own test
     proving the loop receives it; **or**
   - Explicitly accept the lossy downgrade (map `priorMessages` through
     something like `resume.ts`'s question/answer pairing into
     `ConversationTurn[]`), state that tool call/result content does not
     survive into the next turn's model context on resume, and rewrite Edit
     3's produced-message slicing to not depend on index alignment with the
     lossy array (e.g., diff by count of *newly appended* messages the loop
     reports, not `messages.length`).
   Either is fine; the plan currently picks neither and ships a snippet that
   assumes a field that isn't there.

   **Resolved:** picked the first option — Judgment call 8 adds an additive
   `priorMessages?: LlmMessage[]` field to `RunAgentInput`/
   `AnswerWithMemoryInput`, and Task 4's Step 3 now edits `harness.ts` (thread
   it into `messages[]` before the new user message, no downgrade) and
   `memory/answer.ts` (pass-through) before the route edits. Edit 3's slicing
   stays index-based but is recomputed from `priorMessages.length + 2`
   (system + priorMessages + userMessage) since the route no longer builds
   the raw array itself. Task 4's test sketch now asserts `answerWithMemory`
   is called with `priorMessages` set, and a new test-file line item covers
   `runAgent` threading it into `messages[]`.

2. **`appendMessages` isn't transactional across its multi-row calls.** Task 4
   calls it with `[assistantMsg, toolResultMsg]` in one call (matching the
   round-trip test in Task 2). The current implementation loops row-by-row
   with individual `INSERT`s, then a separate `UPDATE ... updated_at`. If the
   second `INSERT` fails (dropped connection, statement timeout) after the
   first succeeds, the persisted transcript ends up with an `assistant`
   message containing a `tool_use` block and no matching `tool_result` row.
   On the next turn, that half-written transcript gets threaded back into the
   model call — Anthropic's API (and most providers) reject a `tool_use` with
   no immediately-following `tool_result`, turning a transient DB write
   failure into a permanently broken session (every future turn 400s until
   someone manually fixes the row). **Fix:** wrap `appendMessages`'s inserts
   and the `updated_at` bump in one `db.begin(...)` transaction, same pattern
   already used in this repo's `migrate.ts`.

   **Resolved:** Task 2's `appendMessages` implementation now wraps every
   `INSERT` and the trailing `updated_at` `UPDATE` in one `db.begin(async
   (tx) => {...})` call (same pattern as `migrate.ts`/`memory/notes.ts`), so a
   mid-call failure rolls back the whole batch instead of persisting an
   unpaired `tool_use` row.

### Notes (non-blocking)

- **Migration filename silently diverges from the brief.** Contracts brief §6
  names the file `src/migrations/004_chat_sessions.sql`; this plan uses
  `005_chat_sessions.sql`. It's numerically fine — R1's plan independently
  claims `004_model_calls_stream_turn.sql` and flags that deviation itself —
  but R6's plan never states this renumbering or acknowledges the brief's
  literal filename, which the brief's own rule requires ("that deviation must
  be called out explicitly ... don't silently diverge"). Add one sentence to
  the Judgment calls list.
- **`getOrCreateLatestSession` has a benign TOCTOU race.** Two concurrent
  first-ever requests for `DEFAULT_ACTOR` (double-tab load, double-submit)
  can both see "no session exists" and both insert one. Low severity for v1
  single-tenant traffic — no data corruption, just an extra empty session row
  — and this repo has an existing precedent for treating this class of race
  as worth a follow-up fix once observed (`cc25782`, source_id TOCTOU). Not
  blocking; worth a one-line ticket if it's ever seen in practice.
- **Task 4's own test sketch leaves its key assertion unresolved**
  ("assert the loop/turn was invoked with the prior messages threaded in —
  exact assertion depends on R5's mock surface"). Once must-fix #1 is
  resolved, this needs a concrete assertion, not a placeholder comment — as
  written it's the one test in the plan that doesn't actually pin behavior.
- Tasks 1, 2, 3, 5, 6 are otherwise well-scoped: fully coded (no
  placeholder/TBD sketches), each has a clear failing-test-first step, and
  the file/complexity footprint (6 production files, 2 new modules) stays
  well under the 8-file/2-new-service smell threshold.
