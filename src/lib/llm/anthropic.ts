import Anthropic from "@anthropic-ai/sdk";
import type {
  LlmAdapter, LlmMessage, LlmStreamEvent, LlmToolDefinition, LlmTurnRequest,
  LlmUsage, StopReason,
} from "./types";

const DEFAULT_MAX_TOKENS = 8192;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value?.trim()) {
    throw new Error(`${name} is required for the Anthropic LLM adapter.`);
  }
  return value;
}

// Mirrors model-gateway.ts's resolveAnthropicBaseUrl. Duplicated (not
// imported) to avoid a circular import — model-gateway.ts imports this
// module to pick the default adapter for streamAgentTurn.
function resolveAnthropicBaseUrl(raw?: string): string {
  const configured = raw?.trim();
  if (!configured) return "https://api.anthropic.com/v1";
  const trimmed = configured.replace(/\/+$/, "");
  return /\/v\d+$/.test(trimmed) ? trimmed : `${trimmed}/v1`;
}

function toAnthropicTools(tools: LlmToolDefinition[]): Anthropic.Tool[] {
  return tools.map((tool) => ({
    name: tool.name,
    description: tool.description,
    input_schema: tool.inputSchema as Anthropic.Tool["input_schema"],
  }));
}

function toAnthropicMessages(messages: LlmMessage[]): {
  system: string;
  wireMessages: Anthropic.MessageParam[];
} {
  const first = messages[0];
  if (!first || first.role !== "system") {
    throw new Error("anthropicAdapter: messages[0] must be the system message.");
  }
  const wireMessages: Anthropic.MessageParam[] = [];

  for (const message of messages.slice(1)) {
    if (message.role === "user") {
      wireMessages.push({ role: "user", content: message.content });
    } else if (message.role === "assistant") {
      wireMessages.push({
        role: "assistant",
        content: message.content.map((block): Anthropic.ContentBlockParam =>
          block.type === "text"
            ? { type: "text", text: block.text }
            : { type: "tool_use", id: block.id, name: block.name, input: block.input },
        ),
      });
    } else if (message.role === "tool_result") {
      wireMessages.push({
        role: "user",
        content: message.content.map((block) => ({
          type: "tool_result" as const,
          tool_use_id: block.toolUseId,
          content: block.content,
          is_error: block.isError,
        })),
      });
    }
  }

  return { system: first.content, wireMessages };
}

function mapStopReason(reason: string | null): StopReason {
  switch (reason) {
    case "end_turn":
    case "stop_sequence":
      return "end";
    case "tool_use":
      return "tool_use";
    case "max_tokens":
      return "max_tokens";
    default:
      return "error";
  }
}

export const anthropicAdapter: LlmAdapter = async function* anthropicAdapter(
  request: LlmTurnRequest,
  signal?: AbortSignal,
): AsyncGenerator<LlmStreamEvent> {
  const apiKey = requireEnv("ANTHROPIC_API_KEY");
  const client = new Anthropic({
    apiKey,
    baseURL: resolveAnthropicBaseUrl(process.env.ANTHROPIC_BASE_URL),
  });
  const { system, wireMessages } = toAnthropicMessages(request.messages);

  const stream = await client.messages.create(
    {
      model: request.model,
      max_tokens: request.maxTokens ?? DEFAULT_MAX_TOKENS,
      system,
      messages: wireMessages,
      tools: toAnthropicTools(request.tools),
      stream: true,
    },
    { signal },
  );

  let stopReason: StopReason = "error";
  let usage: LlmUsage = { inputTokens: null, outputTokens: null, totalTokens: null };
  let currentToolId: string | null = null;
  let currentToolName: string | null = null;
  let currentToolJson = "";

  for await (const event of stream) {
    if (event.type === "content_block_start") {
      if (event.content_block.type === "tool_use") {
        currentToolId = event.content_block.id;
        currentToolName = event.content_block.name;
        currentToolJson = "";
        yield { type: "tool_call_start", id: currentToolId, name: currentToolName };
      }
    } else if (event.type === "content_block_delta") {
      if (event.delta.type === "text_delta") {
        yield { type: "text_delta", delta: event.delta.text };
      } else if (event.delta.type === "thinking_delta") {
        yield { type: "thinking_delta", delta: event.delta.thinking };
      } else if (event.delta.type === "input_json_delta") {
        currentToolJson += event.delta.partial_json;
        if (currentToolId) {
          yield { type: "tool_call_delta", id: currentToolId, delta: event.delta.partial_json };
        }
      }
    } else if (event.type === "content_block_stop") {
      if (currentToolId && currentToolName) {
        yield {
          type: "tool_call_end",
          id: currentToolId,
          name: currentToolName,
          input: currentToolJson ? JSON.parse(currentToolJson) : {},
        };
        currentToolId = null;
        currentToolName = null;
        currentToolJson = "";
      }
    } else if (event.type === "message_delta") {
      stopReason = mapStopReason(event.delta.stop_reason);
      usage = {
        inputTokens: event.usage.input_tokens,
        outputTokens: event.usage.output_tokens,
        totalTokens:
          event.usage.input_tokens !== null || event.usage.output_tokens !== null
            ? (event.usage.input_tokens ?? 0) + (event.usage.output_tokens ?? 0)
            : null,
      };
    }
  }

  yield { type: "turn_end", stopReason, usage };
};
