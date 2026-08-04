import { runAgent, type AgentStepEvent, type ConversationTurn } from "@/lib/harness";
import type { AgentLoopEvent } from "@/lib/agent-loop";
import { buildMemoryAnswerInstructions } from "@/lib/context";
import { getRunTrace } from "@/lib/ledger";
import type { LlmAdapter, LlmMessage } from "@/lib/llm/types";
import { verifyAnswerCitations } from "@/lib/memory/citations";
import { memoryRegistry } from "@/lib/memory/answer-config";
import { loadPersona } from "@/lib/persona";
import type { Sql } from "@/lib/tools/types";
import type { MemoryActor } from "@/lib/memory/actor";

export interface MemoryAnswer {
  runId: number;
  // "truncated" carries a usable partial answer — see AgentLoopResult.status.
  status: "succeeded" | "truncated" | "failed";
  answer?: string;
  steps: number;
  invalidCitations: string[];
  // The full transcript produced by this run (RunAgentResult.messages) —
  // consumed by R6's chat-session persistence via the streaming route.
  messages: LlmMessage[];
}

export interface AnswerWithMemoryInput {
  question: string;
  // The signed-in director this run answers for (H1). Scopes every
  // search_memory hit and stamps the ledger run.
  actor: MemoryActor;
  history?: ConversationTurn[];
  // Full-fidelity prior session messages (R6), forwarded straight through to
  // runAgent's priorMessages — see RunAgentInput.priorMessages.
  priorMessages?: LlmMessage[];
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
  // Test seam: forwarded to runAgent.maxSteps. Production callers leave it unset
  // and get DEFAULT_MAX_STEPS; tests use it to reach the truncated path without
  // driving 50 real turns.
  maxSteps?: number;
}

// One agent run over team memory: the ReAct harness plus the citation post-check
// that scrubs invented citations from the final answer. Shared by the JSON
// /api/agent route and the streaming /api/agent/stream route (the latter passes
// onStep). The post-check runs here, before any answer is returned/streamed, so a
// bad citation never reaches the client.
export async function answerWithMemory(db: Sql, input: AnswerWithMemoryInput): Promise<MemoryAnswer> {
  // One load per run: the prompt sections and the toolset come from the same
  // persona file, so a persona edit can never leave §6's capability claim
  // describing a different tool list than the one actually registered.
  const persona = loadPersona();
  const registry = memoryRegistry(persona);
  // Scoped to the actor: the system prompt embeds a memory index listing source
  // titles, so an unscoped build would leak another director's source names in
  // the prompt even though search_memory itself is filtered.
  const instructions = await buildMemoryAnswerInstructions(db, input.actor, persona);
  const result = await runAgent({
    question: input.question,
    actor: input.actor,
    history: input.history,
    priorMessages: input.priorMessages,
    registry,
    // Chat runs execute read + record-as-note (write_internal) + external
    // enrichment (enrich: web_search / web_fetch / apollo_*). enrich is allowed
    // freely per Fisher's 2026-07-15 call; per-run cost control (credit caps,
    // per-tool budgets) is an URGENT post-R9 follow-up — see progress.md.
    allowedClasses: new Set(["read", "write_internal", "enrich"]),
    instructions,
    db,
    onStep: input.onStep,
    onAgentLoopEvent: input.onAgentLoopEvent,
    signal: input.signal,
    adapter: input.adapter,
    maxSteps: input.maxSteps,
  });

  let answer = result.answer;
  let invalidCitations: string[] = [];
  // Every status that carries text must be checked. Gating on "succeeded" alone
  // would ship a truncated run's partial answer to the user with its citations
  // unverified — the same text, none of the scrubbing.
  if ((result.status === "succeeded" || result.status === "truncated") && answer !== undefined) {
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
