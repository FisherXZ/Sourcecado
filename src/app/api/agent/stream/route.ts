import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { answerWithMemory, summarizeStep } from "@/lib/memory/answer";
import { streamAgentResponse } from "@/lib/ui-message-stream";
import type { ConversationTurn } from "@/lib/harness";

// Streaming sibling of /api/agent: same memory agent run, but each tool step is
// pushed to the client live (AI SDK UI-message-stream, via the ui-message-stream
// boundary module) so the chat can render the reasoning trace as it happens. The
// final answer is written only after answerWithMemory's citation post-check, so no
// invalid citation ever streams.
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }
  const history = parseHistory((body as { history?: unknown } | null)?.history);
  const db = getDb();

  return streamAgentResponse(async (writer) => {
    const result = await answerWithMemory(db, {
      question,
      history,
      onStep: (event) => writer.step(`step-${event.index}`, summarizeStep(event)),
    });
    if (result.answer) writer.answer(result.answer);
    writer.meta({
      runId: result.runId,
      status: result.status,
      steps: result.steps,
      invalidCitations: result.invalidCitations,
    });
  });
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
