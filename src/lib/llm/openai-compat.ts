import OpenAI from "openai";
import type {
  LlmAdapter, LlmMessage, LlmStreamEvent, LlmToolDefinition, LlmTurnRequest,
  LlmUsage, StopReason,
} from "./types";

const DEFAULT_MAX_TOKENS = 8192;

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value?.trim()) {
    throw new Error(`${name} is required for the OpenAI-compatible LLM adapter.`);
  }
  return value;
}

function toOpenAiTools(tools: LlmToolDefinition[]): OpenAI.Chat.ChatCompletionTool[] {
  return tools.map((tool) => ({
    type: "function",
    function: { name: tool.name, description: tool.description, parameters: tool.inputSchema as Record<string, unknown> },
  }));
}

function toOpenAiMessages(messages: LlmMessage[]): OpenAI.Chat.ChatCompletionMessageParam[] {
  const wire: OpenAI.Chat.ChatCompletionMessageParam[] = [];
  for (const message of messages) {
    if (message.role === "system") {
      wire.push({ role: "system", content: message.content });
    } else if (message.role === "user") {
      wire.push({ role: "user", content: message.content });
    } else if (message.role === "assistant") {
      const text = message.content
        .filter((b): b is Extract<typeof b, { type: "text" }> => b.type === "text")
        .map((b) => b.text)
        .join("");
      const toolCalls = message.content
        .filter((b): b is Extract<typeof b, { type: "tool_use" }> => b.type === "tool_use")
        .map((b) => ({
          id: b.id,
          type: "function" as const,
          function: { name: b.name, arguments: JSON.stringify(b.input) },
        }));
      wire.push({
        role: "assistant",
        content: text || null,
        ...(toolCalls.length > 0 ? { tool_calls: toolCalls } : {}),
      });
    } else if (message.role === "tool_result") {
      for (const block of message.content) {
        wire.push({ role: "tool", tool_call_id: block.toolUseId, content: block.content });
      }
    }
  }
  return wire;
}

function mapFinishReason(reason: string | null): StopReason {
  switch (reason) {
    case "stop":
      return "end";
    case "tool_calls":
      return "tool_use";
    case "length":
      return "max_tokens";
    default:
      return "error";
  }
}

export function createOpenAiCompatAdapter(providerName: "deepseek" | "openai"): LlmAdapter {
  return async function* openAiCompatAdapter(
    request: LlmTurnRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<LlmStreamEvent> {
    const client =
      providerName === "deepseek"
        ? new OpenAI({ apiKey: requireEnv("DEEPSEEK_API_KEY"), baseURL: "https://api.deepseek.com" })
        : new OpenAI({ apiKey: requireEnv("OPENAI_API_KEY") });

    const stream = await client.chat.completions.create(
      {
        model: request.model,
        max_tokens: request.maxTokens ?? DEFAULT_MAX_TOKENS,
        messages: toOpenAiMessages(request.messages),
        tools: toOpenAiTools(request.tools),
        stream: true,
        stream_options: { include_usage: true },
      },
      { signal },
    );

    const toolCalls = new Map<number, { id: string; name: string; argsJson: string }>();
    let usage: LlmUsage = { inputTokens: null, outputTokens: null, totalTokens: null };
    let stopReason: StopReason | null = null;

    for await (const chunk of stream) {
      if (chunk.usage) {
        usage = {
          inputTokens: chunk.usage.prompt_tokens ?? null,
          outputTokens: chunk.usage.completion_tokens ?? null,
          totalTokens: chunk.usage.total_tokens ?? null,
        };
      }
      const choice = chunk.choices[0];
      if (!choice) continue;

      if (choice.delta.content) {
        yield { type: "text_delta", delta: choice.delta.content };
      }
      for (const entry of choice.delta.tool_calls ?? []) {
        if (entry.id) {
          const name = entry.function?.name ?? "";
          toolCalls.set(entry.index, { id: entry.id, name, argsJson: "" });
          yield { type: "tool_call_start", id: entry.id, name };
        }
        const args = entry.function?.arguments;
        if (args) {
          const state = toolCalls.get(entry.index);
          if (state) {
            state.argsJson += args;
            yield { type: "tool_call_delta", id: state.id, delta: args };
          }
        }
      }
      if (choice.finish_reason) {
        for (const state of toolCalls.values()) {
          yield {
            type: "tool_call_end",
            id: state.id,
            name: state.name,
            input: state.argsJson ? JSON.parse(state.argsJson) : {},
          };
        }
        stopReason = mapFinishReason(choice.finish_reason);
      }
    }

    yield { type: "turn_end", stopReason: stopReason ?? "error", usage };
  };
}
