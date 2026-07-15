import { streamAgentTurn, type LlmTurnOutcome } from "./model-gateway";
import type {
  LlmAdapter,
  LlmMessage,
  LlmStreamEvent,
  LlmToolResultBlock,
  StopReason,
} from "./llm/types";
import { executeTool, toLlmToolDefinition, type ToolExecutionResult } from "./tools/orchestrator";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql } from "./tools/types";

export type { ToolExecutionResult } from "./tools/orchestrator";

const DEFAULT_MAX_STEPS = 8;

export interface AgentLoopInput {
  messages: LlmMessage[];
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  maxSteps?: number;
  db: Sql;
  runId: number;
  parentStepId: number;
  provider?: string;
  adapter?: LlmAdapter;
  signal?: AbortSignal;
  onEvent?: (event: AgentLoopEvent) => void | Promise<void>;
}

export type AgentLoopEvent =
  | { type: "llm"; event: LlmStreamEvent }
  | { type: "tool_start"; id: string; name: string; input: unknown }
  | { type: "tool_end"; id: string; name: string; result: ToolExecutionResult };

export interface AgentLoopResult {
  status: "succeeded" | "failed";
  messages: LlmMessage[];
  finalText?: string;
  stopReason: StopReason;
  steps: number;
}

export async function runAgentLoop(input: AgentLoopInput): Promise<AgentLoopResult> {
  const maxSteps = input.maxSteps ?? DEFAULT_MAX_STEPS;
  const messages = [...input.messages];
  const tools = input.registry.list(input.allowed).map(toLlmToolDefinition);
  let lastStopReason: StopReason = "tool_use";

  for (let step = 1; step <= maxSteps; step++) {
    if (input.signal?.aborted) {
      messages.push(syntheticAssistantMessage("[aborted]"));
      return { status: "failed", messages, stopReason: "aborted", steps: step };
    }

    let outcome: LlmTurnOutcome;
    let gen: AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void> | undefined;
    try {
      gen = streamAgentTurn(input.db, {
        taskName: "agent_loop_turn",
        promptVersion: "1",
        providerName: input.provider,
        messages,
        tools,
        trace: { runId: input.runId, parentStepId: input.parentStepId },
        adapter: input.adapter,
        signal: input.signal,
      });
      outcome = await drain(gen, input.onEvent);
    } catch (error) {
      // If the throw came from onEvent (not the generator itself), the
      // generator is still suspended at a yield — resume it with return() so
      // streamAgentTurn's finally can mark its ledger rows 'abandoned' instead
      // of leaving them 'running' forever. No-op if the generator already ran
      // to completion or threw.
      try {
        await gen?.return(undefined as never);
      } catch {
        // Cleanup is best-effort; the original error still decides the outcome.
      }
      const aborted = input.signal?.aborted === true;
      const message = error instanceof Error ? error.message : String(error);
      messages.push(syntheticAssistantMessage(aborted ? "[aborted]" : `[model error: ${message}]`));
      return { status: "failed", messages, stopReason: aborted ? "aborted" : "error", steps: step };
    }

    messages.push(outcome.message);
    lastStopReason = outcome.stopReason;

    if (outcome.stopReason === "end") {
      const finalText = outcome.message.content
        .filter((block): block is Extract<(typeof outcome.message.content)[number], { type: "text" }> => block.type === "text")
        .map((block) => block.text)
        .join("");
      return { status: "succeeded", messages, finalText, stopReason: "end", steps: step };
    }

    if (outcome.stopReason !== "tool_use") {
      // "max_tokens" or "error" surfaced as a normal turn outcome (not a throw).
      // Keep messages[] a provider-valid transcript for later reuse (R6 persists
      // and resubmits it): a truncated turn can carry tool_use blocks that will
      // never execute — providers reject a transcript where a tool_use has no
      // paired tool_result — and an empty assistant message is rejected outright.
      if (outcome.message.content.length === 0) {
        messages[messages.length - 1] = syntheticAssistantMessage(
          `[model produced no content: ${outcome.stopReason}]`
        );
      }
      const dangling = outcome.message.content.filter(
        (block): block is Extract<(typeof outcome.message.content)[number], { type: "tool_use" }> =>
          block.type === "tool_use"
      );
      if (dangling.length > 0) {
        messages.push({
          role: "tool_result",
          content: dangling.map((block) => ({
            toolUseId: block.id,
            toolName: block.name,
            content: `Error (not_executed): run ended (${outcome.stopReason}) before this tool call could execute.`,
            isError: true,
          })),
        });
      }
      return { status: "failed", messages, stopReason: outcome.stopReason, steps: step };
    }

    const toolUseBlocks = outcome.message.content.filter(
      (block): block is Extract<(typeof outcome.message.content)[number], { type: "tool_use" }> =>
        block.type === "tool_use"
    );
    const resultBlocks: LlmToolResultBlock[] = [];
    for (const block of toolUseBlocks) {
      await input.onEvent?.({ type: "tool_start", id: block.id, name: block.name, input: block.input });
      const result = await executeTool({
        toolUseId: block.id,
        name: block.name,
        input: block.input,
        registry: input.registry,
        allowed: input.allowed,
        db: input.db,
        runId: input.runId,
        parentStepId: input.parentStepId,
      });
      await input.onEvent?.({ type: "tool_end", id: block.id, name: block.name, result });
      resultBlocks.push({
        toolUseId: block.id,
        toolName: block.name,
        content: result.content,
        isError: result.isError,
      });
    }
    messages.push({ role: "tool_result", content: resultBlocks });
  }

  return { status: "failed", messages, stopReason: lastStopReason, steps: maxSteps };
}

async function drain(
  gen: AsyncGenerator<LlmStreamEvent, LlmTurnOutcome, void>,
  onEvent?: (event: AgentLoopEvent) => void | Promise<void>
): Promise<LlmTurnOutcome> {
  let cur = await gen.next();
  while (!cur.done) {
    await onEvent?.({ type: "llm", event: cur.value });
    cur = await gen.next();
  }
  return cur.value;
}

function syntheticAssistantMessage(text: string): LlmMessage {
  return { role: "assistant", content: [{ type: "text", text }] };
}
