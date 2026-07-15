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
    expect(Number(rows[0].id)).toBe(session.id);
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
    const [run] = await db<{ id: number }[]>`
      INSERT INTO runs (run_type, status) VALUES ('agent_chat_stream', 'succeeded') RETURNING id
    `;
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
    await appendMessages(db, session.id, [assistantMsg, toolResultMsg], run.id);

    const loaded = await loadSessionMessages(db, session.id);
    expect(loaded).toEqual([userMsg, assistantMsg, toolResultMsg]);
  });

  it("forces run_id to NULL for user/system rows even if a runId is passed", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const [run] = await db<{ id: number }[]>`
      INSERT INTO runs (run_type, status) VALUES ('agent_chat_stream', 'succeeded') RETURNING id
    `;
    await appendMessages(db, session.id, [{ role: "user", content: "hi" } as LlmUserMessage], run.id);
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

  it("appendMessages is atomic: a mid-batch failure rolls back the whole call, persisting no rows and leaving updated_at untouched", async () => {
    const db = getDb();
    const session = await createSession(db, ACTOR);
    const [before] = await db<{ updated_at: Date }[]>`SELECT updated_at FROM chat_sessions WHERE id = ${session.id}`;

    // Second message has a role the CHECK constraint rejects, so its INSERT
    // throws mid-batch. The db.begin wrapper must roll back the first (valid)
    // INSERT too — otherwise a dropped write would leave an unpaired row that
    // poisons every future turn (the invariant the plan's eng review demanded).
    const good = { role: "user", content: "kept?" } as LlmUserMessage;
    const bad = { role: "bogus", content: "boom" } as unknown as LlmUserMessage;

    await expect(appendMessages(db, session.id, [good, bad])).rejects.toThrow();

    const rows = await db`SELECT 1 FROM chat_messages WHERE session_id = ${session.id}`;
    expect(rows).toHaveLength(0);
    const [after] = await db<{ updated_at: Date }[]>`SELECT updated_at FROM chat_sessions WHERE id = ${session.id}`;
    expect(after.updated_at.getTime()).toBe(before.updated_at.getTime());
  });
});
