import { describe, it, expect } from "vitest";
import { conversationTurnsToMessages } from "@/lib/harness";
import { MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";

describe("conversationTurnsToMessages — multi-turn history", () => {
  it("maps user/assistant turns to LlmMessage in order", () => {
    const messages = conversationTurnsToMessages([
      { role: "user", content: "Who is Acme?" },
      { role: "assistant", content: "Acme is a fintech startup." },
    ]);
    expect(messages).toEqual([
      { role: "user", content: "Who is Acme?" },
      { role: "assistant", content: [{ type: "text", text: "Acme is a fintech startup." }] },
    ]);
  });

  it("returns an empty array with no history (back-compat: nothing to thread)", () => {
    expect(conversationTurnsToMessages([])).toEqual([]);
    expect(conversationTurnsToMessages()).toEqual([]);
  });

  it("caps history to the most recent turns server-side, dropping the oldest", () => {
    const many = Array.from({ length: 50 }, (_, i) => ({
      role: "user" as const,
      content: `HISTMARK_${i}`,
    }));
    const messages = conversationTurnsToMessages(many);
    const userContents = messages.filter((m) => m.role === "user").map((m) => (m.role === "user" ? m.content : ""));
    expect(userContents).toContain("HISTMARK_49");
    expect(userContents).not.toContain("HISTMARK_0");
  });

  it("caps a single very long turn so a message cannot grow unbounded", () => {
    const huge = "x".repeat(20000);
    const messages = conversationTurnsToMessages([{ role: "user", content: huge }]);
    const message = messages[0];
    expect(message.role).toBe("user");
    if (message.role === "user") {
      expect(message.content.length).toBeLessThan(huge.length);
    }
  });
});

describe("MEMORY_INSTRUCTIONS — multi-turn citation safety (N3)", () => {
  it("requires calling search_memory every turn so prior-turn citations are not scrubbed", () => {
    expect(MEMORY_INSTRUCTIONS).toMatch(/every turn|each turn|follow-up/i);
  });
});
