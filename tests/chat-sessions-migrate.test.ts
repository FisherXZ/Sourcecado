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
