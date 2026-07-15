import { NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { appendMessages, getOrCreateLatestSession, loadSessionMessages } from "@/lib/chat/sessions";
import { conversationTurnsToMessages, type ConversationTurn } from "@/lib/harness";
import { answerWithMemory, summarizeStep } from "@/lib/memory/answer";
import { DEFAULT_ACTOR } from "@/lib/memory/actor";
import { streamAgentResponse } from "@/lib/ui-message-stream";
import type { LlmUserMessage } from "@/lib/llm/types";

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

  // Chat-session continuity (R6): resume this actor's latest session and load
  // its prior turns (already LlmMessage-shaped, full fidelity) before the
  // turn's input is assembled. The new user message is persisted immediately
  // — durable even if the loop below fails or the request aborts.
  const session = await getOrCreateLatestSession(db, DEFAULT_ACTOR);
  const priorMessages = await loadSessionMessages(db, session.id);
  const userMessage: LlmUserMessage = { role: "user", content: question };
  await appendMessages(db, session.id, [userMessage]);

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
      priorMessages,
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

    // Persist the loop's newly-produced messages after it settles, tagged with
    // the run id — on both success and failure (even a failed run's synthetic
    // error message should show up on reload). `result.messages` is the full
    // transcript runAgent built (system + history + priorMessages + the new
    // user message + whatever the loop produced); slice off exactly that
    // known prefix.
    const priorPrefixLength = 1 + conversationTurnsToMessages(history).length + priorMessages.length + 1;
    const producedMessages = result.messages.slice(priorPrefixLength);
    await appendMessages(db, session.id, producedMessages, result.runId);

    if (streamedLive && !searchCalledSoFar) {
      writer.answerEnd();
    } else if (result.answer) {
      writer.answerFlush(result.answer);
    } else {
      // Run ended with no authoritative answer (e.g. failed mid-stream after a
      // search_memory gated further live streaming): close any answer part that
      // pre-search narration left open, so the SSE text part is well-formed for
      // every consumer. Idempotent — a no-op when nothing streamed live.
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
