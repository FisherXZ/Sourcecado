import { z } from "zod";
import { getDb } from "./db";
import {
  failRun,
  failRunStep,
  failToolCall,
  finishRun,
  finishRunStep,
  finishToolCall,
  startRun,
  startRunStep,
  startToolCall,
} from "./ledger";
import { callModel, ModelGatewayError, type ModelGatewayProvider } from "./model-gateway";
import type { ToolRegistry } from "./tools/registry";
import type { PermissionClass, Sql } from "./tools/types";

export const agentDecisionSchema = z.discriminatedUnion("action", [
  z.object({
    action: z.literal("tool"),
    tool: z.string(),
    args: z.record(z.string(), z.unknown()),
    thought: z.string().optional(),
  }),
  z.object({
    action: z.literal("final"),
    answer: z.string(),
    thought: z.string().optional(),
  }),
]);
export type AgentDecision = z.infer<typeof agentDecisionSchema>;

export interface RunAgentInput {
  question: string;
  registry: ToolRegistry;
  allowedClasses?: Set<PermissionClass>;
  maxSteps?: number;
  provider?: ModelGatewayProvider;
  db?: Sql;
}

export interface RunAgentResult {
  runId: number;
  status: "succeeded" | "failed";
  answer?: string;
  steps: number;
}

const DEFAULT_ALLOWED: PermissionClass[] = ["read", "reason"];
const DEFAULT_MAX_STEPS = 8;

export async function runAgent(input: RunAgentInput): Promise<RunAgentResult> {
  const db = input.db ?? getDb();
  const allowed = input.allowedClasses ?? new Set(DEFAULT_ALLOWED);
  const maxSteps = input.maxSteps ?? DEFAULT_MAX_STEPS;

  const run = await startRun(db, {
    runType: "agent_chat",
    title: input.question.slice(0, 80),
    input: { question: input.question },
  });
  const agentStep = await startRunStep(db, {
    runId: run.id,
    stepKind: "agent",
    name: "react_loop",
    input: { question: input.question },
  });

  const transcript: string[] = [];
  let step = 0;

  try {
    for (step = 1; step <= maxSteps; step++) {
      const decision = await decide(db, {
        runId: run.id,
        parentStepId: agentStep.id,
        question: input.question,
        registry: input.registry,
        allowed,
        transcript,
        provider: input.provider,
      });

      if (decision.action === "final") {
        await finishRunStep(db, {
          runStepId: agentStep.id,
          output: { answer: decision.answer, steps: step },
        });
        await finishRun(db, {
          runId: run.id,
          output: { answer: decision.answer, steps: step },
        });
        return { runId: run.id, status: "succeeded", answer: decision.answer, steps: step };
      }

      const observation = await executeToolCall(db, {
        decision,
        registry: input.registry,
        allowed,
        runId: run.id,
        parentStepId: agentStep.id,
      });
      transcript.push(`Step ${step}: called ${decision.tool} -> ${observation}`);
    }

    const message = `Agent did not finish within ${maxSteps} steps.`;
    await failRunStep(db, {
      runStepId: agentStep.id,
      errorType: "max_steps_exceeded",
      errorMessage: message,
    });
    await failRun(db, {
      runId: run.id,
      errorType: "max_steps_exceeded",
      errorMessage: message,
    });
    return { runId: run.id, status: "failed", steps: maxSteps };
  } catch (error) {
    const code = error instanceof ModelGatewayError ? error.code : "harness_error";
    const message = error instanceof Error ? error.message : String(error);
    await failRunStep(db, { runStepId: agentStep.id, errorType: code, errorMessage: message });
    await failRun(db, { runId: run.id, errorType: code, errorMessage: message });
    return { runId: run.id, status: "failed", steps: step };
  }
}

interface DecideOptions {
  runId: number;
  parentStepId: number;
  question: string;
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  transcript: string[];
  provider?: ModelGatewayProvider;
}

async function decide(db: Sql, opts: DecideOptions): Promise<AgentDecision> {
  const tools = opts.registry.list(opts.allowed);
  const result = await callModel<AgentDecision>(db, {
    kind: "generate_object",
    taskName: "agent_react_decide",
    promptVersion: "1",
    system: buildSystemPrompt(tools),
    prompt: buildUserPrompt(opts.question, opts.transcript),
    schema: agentDecisionSchema,
    schemaName: "agent_decision",
    trace: { runId: opts.runId, parentStepId: opts.parentStepId },
    provider: opts.provider,
  });
  if (!result.object) {
    throw new ModelGatewayError("invalid_output", "Model did not return a decision object.");
  }
  return result.object;
}

function buildSystemPrompt(
  tools: { name: string; description: string; permissionClass: string }[],
): string {
  const catalog = tools
    .map((t) => `- ${t.name} (${t.permissionClass}): ${t.description}`)
    .join("\n");
  return [
    "You are a sourcing agent. Decide the next action.",
    "Either call one tool, or give a final answer.",
    "Respond with a decision object: {action:'tool', tool, args} or {action:'final', answer}.",
    "Available tools:",
    catalog || "(none)",
  ].join("\n");
}

function buildUserPrompt(question: string, transcript: string[]): string {
  const history = transcript.length
    ? `\n\nObservations so far:\n${transcript.join("\n")}`
    : "";
  return `Question: ${question}${history}`;
}

interface ExecuteToolOptions {
  decision: Extract<AgentDecision, { action: "tool" }>;
  registry: ToolRegistry;
  allowed: Set<PermissionClass>;
  runId: number;
  parentStepId: number;
}

async function executeToolCall(db: Sql, opts: ExecuteToolOptions): Promise<string> {
  const { decision, registry, allowed, runId, parentStepId } = opts;
  const toolName = decision.tool;
  const tool = registry.get(toolName);

  const toolStep = await startRunStep(db, {
    runId,
    parentStepId,
    stepKind: "tool",
    name: toolName,
    input: { args: decision.args },
  });
  const toolCall = await startToolCall(db, {
    runId,
    runStepId: toolStep.id,
    toolName,
    arguments: decision.args,
    metadata: { permissionClass: tool?.permissionClass ?? null },
  });

  if (!tool) {
    return failTool(db, toolStep.id, toolCall.id, "unknown_tool", `Unknown tool: ${toolName}.`);
  }
  if (!allowed.has(tool.permissionClass)) {
    return failTool(
      db,
      toolStep.id,
      toolCall.id,
      "permission_denied",
      `Tool ${toolName} (class ${tool.permissionClass}) is not permitted for this run.`,
    );
  }
  const parsed = tool.argsSchema.safeParse(decision.args);
  if (!parsed.success) {
    return failTool(
      db,
      toolStep.id,
      toolCall.id,
      "invalid_args",
      `Invalid arguments for ${toolName}: ${parsed.error.message}`,
    );
  }

  try {
    const result = await tool.execute(parsed.data, { db, runId, parentStepId: toolStep.id });
    await finishToolCall(db, { toolCallId: toolCall.id, result });
    await finishRunStep(db, { runStepId: toolStep.id, output: result });
    return `Success: ${JSON.stringify(result)}`;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return failTool(db, toolStep.id, toolCall.id, "tool_error", `Tool ${toolName} failed: ${message}`);
  }
}

async function failTool(
  db: Sql,
  runStepId: number,
  toolCallId: number,
  errorType: string,
  message: string,
): Promise<string> {
  await failToolCall(db, { toolCallId, errorType, errorMessage: message });
  await failRunStep(db, { runStepId, errorType, errorMessage: message });
  return `Error (${errorType}): ${message}`;
}
