import { z } from "zod";
import {
  failRunStep,
  failToolCall,
  finishRunStep,
  finishToolCall,
  startRunStep,
  startToolCall,
} from "./ledger";
import { streamAgentTurn, type LlmTurnOutcome } from "./model-gateway";
import type {
  LlmAdapter,
  LlmMessage,
  LlmStreamEvent,
  LlmToolDefinition,
  LlmToolResultBlock,
  StopReason,
} from "./llm/types";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql, Tool } from "./tools/types";

const DEFAULT_MAX_STEPS = 8;
const TOOL_RESULT_MAX_CHARS = 16_000;

export interface ToolExecutionResult {
  content: string;
  isError: boolean;
}

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
    try {
      const gen = streamAgentTurn(input.db, {
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
      return { status: "failed", messages, stopReason: outcome.stopReason, steps: step };
    }

    const toolUseBlocks = outcome.message.content.filter(
      (block): block is Extract<(typeof outcome.message.content)[number], { type: "tool_use" }> =>
        block.type === "tool_use"
    );
    const resultBlocks: LlmToolResultBlock[] = [];
    for (const block of toolUseBlocks) {
      await input.onEvent?.({ type: "tool_start", id: block.id, name: block.name, input: block.input });
      const result = await executeToolUseBlock({
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

function toLlmToolDefinition(tool: Tool): LlmToolDefinition {
  let inputSchema: unknown = {};
  try {
    inputSchema = z.toJSONSchema(tool.argsSchema);
  } catch {
    inputSchema = {};
  }
  return { name: tool.name, description: tool.description, inputSchema };
}

// --- Internal tool execution -------------------------------------------------
// Temporary home (Judgment call #1): R3 lifts this verbatim into
// src/lib/tools/orchestrator.ts as `executeTool`/`toLlmToolDefinition`, then
// updates the imports above instead of the local definitions.

interface ExecuteToolUseBlockInput {
  name: string;
  input: unknown;
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  db: Sql;
  runId: number;
  parentStepId: number;
}

async function executeToolUseBlock(opts: ExecuteToolUseBlockInput): Promise<ToolExecutionResult> {
  const { name, input, registry, allowed, db, runId, parentStepId } = opts;
  const tool = registry.get(name);

  const toolStep = await startRunStep(db, {
    runId,
    parentStepId,
    stepKind: "tool",
    name,
    input: { args: input },
  });
  const toolCall = await startToolCall(db, {
    runId,
    runStepId: toolStep.id,
    toolName: name,
    arguments: input,
    metadata: { permissionClass: tool?.permissionClass ?? null },
  });

  const fail = async (errorType: string, message: string): Promise<ToolExecutionResult> => {
    await failToolCall(db, { toolCallId: toolCall.id, errorType, errorMessage: message });
    await failRunStep(db, { runStepId: toolStep.id, errorType, errorMessage: message });
    return truncate(`Error (${errorType}): ${message}`, true);
  };

  if (!tool) {
    return fail("unknown_tool", `Unknown tool: ${name}.`);
  }
  if (!allowed.has(tool.permissionClass)) {
    return fail(
      "permission_denied",
      `Tool ${name} (class ${tool.permissionClass}) is not permitted for this run.`
    );
  }
  const parsed = tool.argsSchema.safeParse(input);
  if (!parsed.success) {
    return fail("invalid_args", `Invalid arguments for ${name}: ${parsed.error.message}`);
  }

  try {
    const result = await tool.execute(parsed.data, { db, runId, parentStepId: toolStep.id });
    await finishToolCall(db, { toolCallId: toolCall.id, result });
    await finishRunStep(db, { runStepId: toolStep.id, output: result });
    return truncate(`Success: ${JSON.stringify(result)}`, false);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return fail("tool_error", `Tool ${name} failed: ${message}`);
  }
}

function truncate(content: string, isError: boolean): ToolExecutionResult {
  if (content.length <= TOOL_RESULT_MAX_CHARS) {
    return { content, isError };
  }
  const overflow = content.length - TOOL_RESULT_MAX_CHARS;
  return {
    content: `${content.slice(0, TOOL_RESULT_MAX_CHARS)}\n\n[truncated ${overflow} chars]`,
    isError,
  };
}
