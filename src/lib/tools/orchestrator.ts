import { z } from "zod";
import {
  failRunStep,
  failToolCall,
  finishRunStep,
  finishToolCall,
  startRunStep,
  startToolCall,
} from "../ledger";
import type { LlmToolDefinition } from "../llm/types";
import type { ToolRegistry } from "./registry";
import type { PermissionClass, Sql, Tool } from "./types";

export const TOOL_RESULT_MAX_CHARS = 16_000;

export interface ToolExecutionResult {
  content: string; // final, already-truncated text for a tool_result block
  isError: boolean;
}

export interface ExecuteToolInput {
  // Accepted for shape-parity with the contracts brief §4; not read here —
  // the caller uses the tool_use block's id directly to build the
  // LlmToolResultBlock.
  toolUseId: string;
  name: string;
  input: unknown; // native object from LlmToolUseBlock.input — never a JSON string
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  db: Sql;
  runId: number;
  parentStepId: number;
}

// The choke point every tool call passes through: validate → permission gate
// → execute → ledger log → truncate. Denials and failures return an is_error
// result; nothing throws.
export async function executeTool(opts: ExecuteToolInput): Promise<ToolExecutionResult> {
  const { name, input, registry, allowed, db, runId, parentStepId } = opts;
  const tool = registry.get(name);

  // Ledger rows open unconditionally before any validation so every branch
  // (including unknown_tool/permission_denied) is ledger-visible.
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

  if (!tool) {
    return failTool(db, toolStep.id, toolCall.id, "unknown_tool", `Unknown tool: ${name}.`);
  }
  if (!allowed.has(tool.permissionClass)) {
    return failTool(
      db,
      toolStep.id,
      toolCall.id,
      "permission_denied",
      `Tool ${name} (class ${tool.permissionClass}) is not permitted for this run.`
    );
  }
  const parsed = tool.argsSchema.safeParse(input);
  if (!parsed.success) {
    return failTool(
      db,
      toolStep.id,
      toolCall.id,
      "invalid_args",
      `Invalid arguments for ${name}: ${parsed.error.message}`
    );
  }

  try {
    const result = await tool.execute(parsed.data, { db, runId, parentStepId: toolStep.id });
    await finishToolCall(db, { toolCallId: toolCall.id, result });
    await finishRunStep(db, { runStepId: toolStep.id, output: result });
    return { content: truncate(`Success: ${JSON.stringify(result)}`), isError: false };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return failTool(db, toolStep.id, toolCall.id, "tool_error", `Tool ${name} failed: ${message}`);
  }
}

// Tool → LlmToolDefinition, used to build the API `tools:` param. Lives here
// (not registry.ts) since JSON-Schema conversion is a call-boundary concern.
export function toLlmToolDefinition(tool: Tool): LlmToolDefinition {
  let inputSchema: unknown = {};
  try {
    inputSchema = z.toJSONSchema(tool.argsSchema);
  } catch {
    inputSchema = {};
  }
  return { name: tool.name, description: tool.description, inputSchema };
}

function truncate(content: string): string {
  if (content.length <= TOOL_RESULT_MAX_CHARS) return content;
  const overflow = content.length - TOOL_RESULT_MAX_CHARS;
  return `${content.slice(0, TOOL_RESULT_MAX_CHARS)}\n\n[truncated ${overflow} chars]`;
}

async function failTool(
  db: Sql,
  runStepId: number,
  toolCallId: number,
  errorType: string,
  message: string
): Promise<ToolExecutionResult> {
  await failToolCall(db, { toolCallId, errorType, errorMessage: message });
  await failRunStep(db, { runStepId, errorType, errorMessage: message });
  return { content: truncate(`Error (${errorType}): ${message}`), isError: true };
}
