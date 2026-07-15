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
