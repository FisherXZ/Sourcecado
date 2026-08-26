import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { type ConversationTurn } from "@/lib/harness";
import { answerWithMemory } from "@/lib/memory/answer";
import { requireActor } from "@/lib/auth/actor";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }
  const history = parseHistory((body as { history?: unknown } | null)?.history);

  try {
    const db = getDb();
    const actor = await requireActor();
    const result = await answerWithMemory(db, { question, actor, history });
    // A truncated run is a result, not a server error: it carries a real partial
    // answer, and 500 would put that work out of reach of any HTTP client.
    return NextResponse.json(result, {
      status: result.status === "failed" ? 500 : 200,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "agent run failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}

// Accept only well-formed {role, content} turns; ignore anything malformed so a
// bad client payload degrades to a single-turn run rather than a 500.
function parseHistory(raw: unknown): ConversationTurn[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const turns = raw.filter(
    (turn): turn is ConversationTurn =>
      typeof turn === "object" &&
      turn !== null &&
      ((turn as ConversationTurn).role === "user" || (turn as ConversationTurn).role === "assistant") &&
      typeof (turn as ConversationTurn).content === "string"
  );
  return turns.length ? turns : undefined;
}
