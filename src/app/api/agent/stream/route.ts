import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { answerWithMemory, summarizeStep } from "@/lib/memory/answer";
import { streamAgentResponse } from "@/lib/ui-message-stream";
import type { ConversationTurn } from "@/lib/harness";

// Streaming sibling of /api/agent: same memory agent run, but tool steps and
// the model's own text stream to the client live. Text streams token-by-token
// only while no search_memory call has happened this run (nothing to check
// yet); once one fires, subsequent text is held back and the authoritative,
// citation-checked answer is flushed once at the end — so no invalid citation
// ever streams. See docs/superpowers/plans/2026-07-14-r5-streaming-rewire-plan.md
// Judgment call #2 for the exact gating rules.
export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const question = (body as { question?: unknown } | null)?.question;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json({ error: "question is required" }, { status: 400 });
  }
  const history = parseHistory((body as { history?: unknown } | null)?.history);
  const db = getDb();

  return streamAgentResponse(async (writer) => {
    // Gate coupling note: this checks the literal tool name "search_memory"
    // because memoryRegistry() registers exactly that one tool today — "any
    // tool call" and "search_memory called" are the same event. If another
    // citation-bearing tool ever joins the registry, gate on any tool_start.
    let searchCalledSoFar = false;
    let streamedLive = false;

    const result = await answerWithMemory(db, {
      question,
      history,
      // Abort the run when the client disconnects (or the client-side 90s
      // timeout fires): Next aborts request.signal on connection close, and the
      // AI SDK stream swallows write-after-cancel, so this signal is the only
      // thing that actually terminates the background loop.
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

    if (streamedLive && !searchCalledSoFar) {
      writer.answerEnd();
    } else if (result.answer) {
      writer.answerFlush(result.answer);
    }

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
