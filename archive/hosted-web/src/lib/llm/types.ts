import type postgres from "postgres";
export type Sql = postgres.Sql;

// Messages
export type LlmRole = "system" | "user" | "assistant" | "tool_result";
export interface LlmTextBlock { type: "text"; text: string }
export interface LlmToolUseBlock { type: "tool_use"; id: string; name: string; input: unknown }
export type LlmAssistantBlock = LlmTextBlock | LlmToolUseBlock;
export interface LlmToolResultBlock {
  toolUseId: string;
  toolName: string;
  content: string;
  isError: boolean;
}
export interface LlmSystemMessage { role: "system"; content: string }
export interface LlmUserMessage { role: "user"; content: string }
export interface LlmAssistantMessage { role: "assistant"; content: LlmAssistantBlock[] }
export interface LlmToolResultMessage { role: "tool_result"; content: LlmToolResultBlock[] }
export type LlmMessage =
  | LlmSystemMessage | LlmUserMessage | LlmAssistantMessage | LlmToolResultMessage;

// Streaming
export type StopReason = "end" | "tool_use" | "max_tokens" | "error" | "aborted";
export interface LlmUsage {
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
}
export type LlmStreamEvent =
  | { type: "text_delta"; delta: string }
  | { type: "thinking_delta"; delta: string }
  | { type: "tool_call_start"; id: string; name: string }
  | { type: "tool_call_delta"; id: string; delta: string }
  | { type: "tool_call_end"; id: string; name: string; input: unknown }
  | { type: "turn_end"; stopReason: StopReason; usage: LlmUsage };

// Adapter interface — implemented by anthropic.ts and openai-compat.ts
export interface LlmToolDefinition {
  name: string;
  description: string;
  inputSchema: unknown;
}
export interface LlmTurnRequest {
  model: string;
  messages: LlmMessage[];
  tools: LlmToolDefinition[];
  maxTokens?: number;
}
export type LlmAdapter = (request: LlmTurnRequest, signal?: AbortSignal) => AsyncGenerator<LlmStreamEvent>;
