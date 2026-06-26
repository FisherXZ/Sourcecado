import { runAgent, type AgentStepEvent, type ConversationTurn } from "@/lib/harness";
import { getRunTrace } from "@/lib/ledger";
import { verifyAnswerCitations } from "@/lib/memory/citations";
import { memoryRegistry, MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";
import type { Sql } from "@/lib/tools/types";

export interface MemoryAnswer {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
  invalidCitations: string[];
}

export interface AnswerWithMemoryInput {
  question: string;
  history?: ConversationTurn[];
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
}

// One agent run over team memory: the ReAct harness plus the citation post-check
// that scrubs invented citations from the final answer. Shared by the JSON
// /api/agent route and the streaming /api/agent/stream route (the latter passes
// onStep). The post-check runs here, before any answer is returned/streamed, so a
// bad citation never reaches the client.
export async function answerWithMemory(db: Sql, input: AnswerWithMemoryInput): Promise<MemoryAnswer> {
  const registry = memoryRegistry();
  const result = await runAgent({
    question: input.question,
    history: input.history,
    registry,
    allowedClasses: new Set(["read"]),
    instructions: MEMORY_INSTRUCTIONS,
    db,
    onStep: input.onStep,
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
