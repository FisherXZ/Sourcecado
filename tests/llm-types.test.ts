import type {
  LlmAssistantMessage, LlmMessage, LlmStreamEvent, LlmToolResultMessage,
} from "@/lib/llm/types";

describe("llm/types", () => {
  it("LlmMessage union covers all four roles with correct content shapes", () => {
    const messages: LlmMessage[] = [
      { role: "system", content: "sys" },
      { role: "user", content: "hi" },
      {
        role: "assistant",
        content: [
          { type: "text", text: "thinking out loud" },
          { type: "tool_use", id: "t1", name: "search_memory", input: { q: "x" } },
        ],
      },
      {
        role: "tool_result",
        content: [{ toolUseId: "t1", toolName: "search_memory", content: "[]", isError: false }],
      },
    ];
    expect(messages).toHaveLength(4);
    const assistant = messages[2] as LlmAssistantMessage;
    expect(assistant.content).toHaveLength(2);
    const toolResult = messages[3] as LlmToolResultMessage;
    expect(toolResult.content[0]?.isError).toBe(false);
  });

  it("LlmStreamEvent union covers all six event types", () => {
    const events: LlmStreamEvent[] = [
      { type: "text_delta", delta: "hi" },
      { type: "thinking_delta", delta: "hmm" },
      { type: "tool_call_start", id: "t1", name: "search_memory" },
      { type: "tool_call_delta", id: "t1", delta: '{"q":' },
      { type: "tool_call_end", id: "t1", name: "search_memory", input: { q: "x" } },
      { type: "turn_end", stopReason: "tool_use", usage: { inputTokens: 1, outputTokens: 2, totalTokens: 3 } },
    ];
    expect(events).toHaveLength(6);
  });
});
