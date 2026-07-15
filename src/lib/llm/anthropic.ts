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

// Inverse of model-gateway.ts's resolveAnthropicBaseUrl: the raw
// @anthropic-ai/sdk appends /v1/... to its baseURL itself, so a versioned
// base (the @ai-sdk/anthropic convention) posts to /v1/v1/messages and 404s.
// Strip a trailing version segment; return undefined when unset so the SDK
// uses its own default host.
function resolveAnthropicBaseUrl(raw?: string): string | undefined {
  const configured = raw?.trim();
  if (!configured) return undefined;
  const trimmed = configured.replace(/\/+$/, "");
  return trimmed.replace(/\/v\d+$/, "");
}

// When max_tokens truncates a turn mid-tool-input, the block still closes but
// the accumulated JSON is incomplete. A throw here would misclassify a normal
// truncation outcome as provider_error; fall back to {} so the turn completes
// with its real stop reason (a stopReason of "tool_use" then degrades to an
// invalid_args tool_result the model can recover from).
function parseToolInput(json: string): unknown {
  if (!json) return {};
  try {
    return JSON.parse(json);
  } catch {
    return {};
  }
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
  // Anthropic delivers the authoritative input_tokens in message_start and the
  // cumulative output_tokens in message_delta; the message_delta usage payload
  // normally omits input_tokens. Track each side independently so neither the
  // input count nor the total is silently dropped.
  let inputTokens: number | null = null;
  let outputTokens: number | null = null;
  let currentToolId: string | null = null;
  let currentToolName: string | null = null;
  let currentToolJson = "";

  for await (const event of stream) {
    if (event.type === "message_start") {
      inputTokens = event.message.usage.input_tokens ?? inputTokens;
      outputTokens = event.message.usage.output_tokens ?? outputTokens;
    } else if (event.type === "content_block_start") {
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
          input: parseToolInput(currentToolJson),
        };
        currentToolId = null;
        currentToolName = null;
        currentToolJson = "";
      }
    } else if (event.type === "message_delta") {
      stopReason = mapStopReason(event.delta.stop_reason);
      // Only overwrite when the delta actually carries a value: output_tokens is
      // cumulative here, input_tokens usually arrives only in message_start.
      if (event.usage.input_tokens != null) inputTokens = event.usage.input_tokens;
      if (event.usage.output_tokens != null) outputTokens = event.usage.output_tokens;
    }
  }

  const usage: LlmUsage = {
    inputTokens,
    outputTokens,
    totalTokens:
      inputTokens !== null || outputTokens !== null
        ? (inputTokens ?? 0) + (outputTokens ?? 0)
        : null,
  };
  yield { type: "turn_end", stopReason, usage };
};
