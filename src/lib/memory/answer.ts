import { runAgent, type AgentStepEvent, type ConversationTurn } from "@/lib/harness";
import type { AgentLoopEvent } from "@/lib/agent-loop";
import { buildMemoryAnswerInstructions } from "@/lib/context";
import { getRunTrace } from "@/lib/ledger";
import type { LlmAdapter, LlmMessage } from "@/lib/llm/types";
import { verifyAnswerCitations } from "@/lib/memory/citations";
import { memoryRegistry } from "@/lib/memory/answer-config";
import type { Sql } from "@/lib/tools/types";

export interface MemoryAnswer {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
  invalidCitations: string[];
  // The full transcript produced by this run (RunAgentResult.messages) —
  // consumed by R6's chat-session persistence via the streaming route.
  messages: LlmMessage[];
}

export interface AnswerWithMemoryInput {
  question: string;
  history?: ConversationTurn[];
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
  // Raw agent-loop events, forwarded 1:1 to runAgent — see RunAgentInput.
  onAgentLoopEvent?: (event: AgentLoopEvent) => void | Promise<void>;
  // Client disconnect / timeout signal, forwarded to runAgent so the loop
  // aborts between steps (and its provider fetch is cancelled) instead of
  // running to completion in the background. See RunAgentInput.signal.
  signal?: AbortSignal;
  // Test seam: injected LlmAdapter, forwarded to runAgent. Mirrors
  // RunAgentInput.adapter; production callers never set it.
  adapter?: LlmAdapter;
}

// One agent run over team memory: the ReAct harness plus the citation post-check
// that scrubs invented citations from the final answer. Shared by the JSON
// /api/agent route and the streaming /api/agent/stream route (the latter passes
// onStep). The post-check runs here, before any answer is returned/streamed, so a
// bad citation never reaches the client.
export async function answerWithMemory(db: Sql, input: AnswerWithMemoryInput): Promise<MemoryAnswer> {
  const registry = memoryRegistry();
  const instructions = await buildMemoryAnswerInstructions(db);
  const result = await runAgent({
    question: input.question,
    history: input.history,
    registry,
    // §4's record-as-note doctrine (add_memory_note, class write_internal) is
    // dead wiring unless the chat run permits that class alongside read.
    allowedClasses: new Set(["read", "write_internal"]),
    instructions,
    db,
    onStep: input.onStep,
    onAgentLoopEvent: input.onAgentLoopEvent,
    signal: input.signal,
    adapter: input.adapter,
  });

  let answer = result.answer;
  let invalidCitations: string[] = [];
  if (result.status === "succeeded" && answer !== undefined) {
    const trace = await getRunTrace(db, result.runId);
    const checked = verifyAnswerCitations(trace, answer);
    answer = checked.answer;
    invalidCitations = checked.invalidCitations;
  }

  return {
    runId: result.runId,
    status: result.status,
    answer,
    steps: result.steps,
    invalidCitations,
    messages: result.messages,
  };
}

// A tool step rendered in the chat's reasoning trace. `detail` is a short,
// human-readable summary of the observation (not the raw tool JSON).
export interface ChatStepPart {
  index: number;
  tool: string;
  thought?: string;
  ok: boolean;
  detail: string;
}

export function summarizeStep(event: AgentStepEvent): ChatStepPart {
  return {
    index: event.index,
    tool: event.tool,
    thought: event.thought,
    ok: event.ok,
    detail: describeObservation(event),
  };
}

function describeObservation(event: AgentStepEvent): string {
  if (!event.ok) {
    // Observation is "Error (type): message" — show just the message.
    return event.observation.replace(/^Error \([^)]*\):\s*/, "").trim().slice(0, 160) || "failed";
  }
  const payload = event.observation.replace(/^Success:\s*/, "");
  if (event.tool === "search_memory") {
    try {
      const r = JSON.parse(payload) as {
        acceptedFacts?: unknown[];
        gapFacts?: unknown[];
        chunks?: unknown[];
      };
      const facts = (r.acceptedFacts?.length ?? 0) + (r.gapFacts?.length ?? 0);
      const chunks = r.chunks?.length ?? 0;
      return `${facts} fact${facts === 1 ? "" : "s"}, ${chunks} chunk${chunks === 1 ? "" : "s"}`;
    } catch {
      return "done";
    }
  }
  return "done";
}
