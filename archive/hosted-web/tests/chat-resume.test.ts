import type { LlmAssistantMessage, LlmMessage, LlmToolResultMessage, LlmUserMessage } from "@/lib/llm/types";
import { mapMessagesToResumedExchanges } from "@/app/chat/resume";

function user(content: string): LlmUserMessage {
  return { role: "user", content };
}
function assistantText(text: string): LlmAssistantMessage {
  return { role: "assistant", content: [{ type: "text", text }] };
}
function assistantToolUse(text: string, id: string, name: string): LlmAssistantMessage {
  return { role: "assistant", content: [{ type: "text", text }, { type: "tool_use", id, name, input: {} }] };
}
function toolResult(id: string, name: string, content: string): LlmToolResultMessage {
  return { role: "tool_result", content: [{ toolUseId: id, toolName: name, content, isError: false }] };
}

describe("mapMessagesToResumedExchanges", () => {
  it("returns an empty list for no messages", () => {
    expect(mapMessagesToResumedExchanges([])).toEqual([]);
  });

  it("pairs a single question with its answer", () => {
    const messages: LlmMessage[] = [user("hi there"), assistantText("hello!")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "hi there", answer: "hello!" }]);
  });

  it("concatenates assistant text across a tool-use turn, skipping tool_result content", () => {
    const messages: LlmMessage[] = [
      user("tell me about acme"),
      assistantToolUse("Let me check.", "call_1", "search_memory"),
      toolResult("call_1", "search_memory", "found 2 facts"),
      assistantText("Acme is a Series B company."),
    ];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([
      { question: "tell me about acme", answer: "Let me check. Acme is a Series B company." },
    ]);
  });

  it("handles multiple turns in one session", () => {
    const messages: LlmMessage[] = [
      user("first question"),
      assistantText("first answer"),
      user("second question"),
      assistantText("second answer"),
    ];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([
      { question: "first question", answer: "first answer" },
      { question: "second question", answer: "second answer" },
    ]);
  });

  it("includes a trailing turn even without a following user message", () => {
    const messages: LlmMessage[] = [user("only question"), assistantText("only answer")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "only question", answer: "only answer" }]);
  });

  it("skips a system message defensively (never persisted in practice)", () => {
    const messages: LlmMessage[] = [{ role: "system", content: "instructions" }, user("q"), assistantText("a")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "q", answer: "a" }]);
  });

  it("ignores an orphaned assistant/tool_result message with no preceding user turn", () => {
    const messages: LlmMessage[] = [assistantText("stray"), user("q"), assistantText("a")];
    expect(mapMessagesToResumedExchanges(messages)).toEqual([{ question: "q", answer: "a" }]);
  });
});
