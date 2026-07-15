import { getDb } from "./db";
import { failRun, failRunStep, finishRun, finishRunStep, startRun, startRunStep } from "./ledger";
import { ModelGatewayError } from "./model-gateway";
import { runAgentLoop, type AgentLoopEvent } from "./agent-loop";
import type { LlmAdapter, LlmMessage } from "./llm/types";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql } from "./tools/types";

export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

// Emitted after each executed tool step (never for the final answer). Carries the
// model's rationale, the tool, and the observation so a streaming UI can render the
// agent's live reasoning trace.
export interface AgentStepEvent {
  index: number;
  tool: string;
  thought?: string;
  observation: string;
  ok: boolean;
}

export interface RunAgentInput {
  question: string;
  registry: ToolRegistry;
  allowedClasses?: Set<PermissionClass>;
  maxSteps?: number;
  db?: Sql;
  instructions?: string;
  // Prior conversation turns for multi-turn chat. Threaded into messages[] ahead
  // of the current question, capped server-side in conversationTurnsToMessages.
  history?: ConversationTurn[];
  // Full-fidelity prior messages for a resumed chat session (R6). Threaded
  // into messages[] immediately before the new user message — unlike
  // `history`, these are already LlmMessage-shaped, so no string-only
  // downgrade happens and tool_use/tool_result blocks survive intact.
  priorMessages?: LlmMessage[];
  // Invoked after each executed tool step. Awaited so a streaming consumer can
  // flush the step to the client before the next turn runs.
  onStep?: (event: AgentStepEvent) => void | Promise<void>;
  // Raw agent-loop events (llm text/thinking deltas, tool_start, tool_end),
  // forwarded 1:1 before the existing tool_end→onStep collapse. Optional and
  // additive — omitting it reproduces today's behavior exactly. Consumed by
  // the streaming route (R5) for true token streaming; the JSON /api/agent
  // route and existing tests never set it.
  onAgentLoopEvent?: (event: AgentLoopEvent) => void | Promise<void>;
  providerName?: string;
  // Test seam: injected LlmAdapter, forwarded to streamAgentTurn. Mirrors the old
  // `provider` seam's purpose for the new native tool-calling loop.
  adapter?: LlmAdapter;
  signal?: AbortSignal;
}

export interface RunAgentResult {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
  // Additive: the full transcript produced by this run (AgentLoopResult.messages),
  // for R6's chat-session persistence. Not consumed by /api/agent or /api/agent/stream.
  messages: LlmMessage[];
}

const DEFAULT_ALLOWED: PermissionClass[] = ["read", "reason"];
const DEFAULT_MAX_STEPS = 8;
// Fallback system message when no `instructions` is supplied. R4's context
// assembly passes its full sectioned prompt through `instructions` instead of
// this repo needing another call site change.
const DEFAULT_IDENTITY = "You are a sourcing agent. Use the available tools to answer accurately.";
const MAX_HISTORY_TURNS = 12;
const MAX_TURN_CHARS = 4000;

export function conversationTurnsToMessages(history: ConversationTurn[] = []): LlmMessage[] {
  return history.slice(-MAX_HISTORY_TURNS).map((turn) =>
    turn.role === "user"
      ? { role: "user", content: turn.content.slice(0, MAX_TURN_CHARS) }
      : { role: "assistant", content: [{ type: "text", text: turn.content.slice(0, MAX_TURN_CHARS) }] }
  );
}

export async function runAgent(input: RunAgentInput): Promise<RunAgentResult> {
  const db = input.db ?? getDb();
  const allowed = input.allowedClasses ?? new Set(DEFAULT_ALLOWED);
  const maxSteps = input.maxSteps ?? DEFAULT_MAX_STEPS;

  let run: Awaited<ReturnType<typeof startRun>> | null = null;
  let agentStep: Awaited<ReturnType<typeof startRunStep>> | null = null;

  try {
    run = await startRun(db, {
      runType: "agent_chat",
      title: input.question.slice(0, 80),
      input: { question: input.question },
    });
    agentStep = await startRunStep(db, {
      runId: run.id,
      stepKind: "agent",
      name: "agent_loop",
      input: { question: input.question },
    });

    const messages: LlmMessage[] = [
      { role: "system", content: input.instructions ?? DEFAULT_IDENTITY },
      ...conversationTurnsToMessages(input.history),
      ...(input.priorMessages ?? []),
      { role: "user", content: input.question },
    ];

    let stepCounter = 0;
    let thoughtBuffer = "";
    const onEvent = input.onStep || input.onAgentLoopEvent
      ? async (event: AgentLoopEvent): Promise<void> => {
          await input.onAgentLoopEvent?.(event);
          if (!input.onStep) return;
          if (event.type === "llm" && (event.event.type === "text_delta" || event.event.type === "thinking_delta")) {
            thoughtBuffer += event.event.delta;
            return;
          }
          if (event.type === "tool_end") {
            stepCounter += 1;
            const thought = thoughtBuffer.trim() || undefined;
            thoughtBuffer = "";
            await input.onStep?.({
              index: stepCounter,
              tool: event.name,
              thought,
              observation: event.result.content,
              ok: !event.result.isError,
            });
          }
        }
      : undefined;

    const result = await runAgentLoop({
      messages,
      registry: input.registry,
      allowed,
      maxSteps,
      db,
      runId: run.id,
      parentStepId: agentStep.id,
      provider: input.providerName,
      adapter: input.adapter,
      signal: input.signal,
      onEvent,
    });

    if (result.status === "succeeded") {
      await finishRunStep(db, {
        runStepId: agentStep.id,
        output: { answer: result.finalText, steps: result.steps },
      });
      await finishRun(db, { runId: run.id, output: { answer: result.finalText, steps: result.steps } });
      return { runId: run.id, status: "succeeded", answer: result.finalText, steps: result.steps, messages: result.messages };
    }

    const { errorType, errorMessage } = describeLoopFailure(result.stopReason, maxSteps);
    await failRunStep(db, { runStepId: agentStep.id, errorType, errorMessage });
    await failRun(db, { runId: run.id, errorType, errorMessage });
    return { runId: run.id, status: "failed", steps: result.steps, messages: result.messages };
  } catch (error) {
    const code = error instanceof ModelGatewayError ? error.code : "harness_error";
    const message = error instanceof Error ? error.message : String(error);
    if (agentStep) {
      await failRunStep(db, { runStepId: agentStep.id, errorType: code, errorMessage: message });
    }
    if (run) {
      await failRun(db, { runId: run.id, errorType: code, errorMessage: message });
    }
    // Loop never ran (or threw before returning AgentLoopResult) — nothing produced.
    return { runId: run?.id ?? 0, status: "failed", steps: 0, messages: [] };
  }
}

function describeLoopFailure(
  stopReason: string,
  maxSteps: number
): { errorType: string; errorMessage: string } {
  if (stopReason === "aborted") {
    return { errorType: "aborted", errorMessage: "Agent run was aborted." };
  }
  if (stopReason === "tool_use") {
    return { errorType: "max_steps_exceeded", errorMessage: `Agent did not finish within ${maxSteps} steps.` };
  }
  if (stopReason === "max_tokens") {
    return { errorType: "max_tokens_exceeded", errorMessage: "Agent loop stopped: model hit its max token limit." };
  }
  return { errorType: "model_error", errorMessage: "Agent loop stopped due to a model error." };
}
