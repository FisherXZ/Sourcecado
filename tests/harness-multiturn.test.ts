import { describe, it, expect } from "vitest";
import { buildUserPrompt } from "@/lib/harness";
import { MEMORY_INSTRUCTIONS } from "@/lib/memory/answer-config";

describe("buildUserPrompt — multi-turn history", () => {
  it("threads prior conversation turns into the prompt, before the current question", () => {
    const prompt = buildUserPrompt("And their latest funding?", [], [
      { role: "user", content: "Who is Acme?" },
      { role: "assistant", content: "Acme is a fintech startup." },
    ]);
    expect(prompt).toContain("Who is Acme?");
    expect(prompt).toContain("Acme is a fintech startup.");
    expect(prompt).toContain("And their latest funding?");
    expect(prompt.indexOf("Who is Acme?")).toBeLessThan(prompt.indexOf("And their latest funding?"));
  });

  it("works with no history (back-compat: a plain single-turn prompt)", () => {
    const prompt = buildUserPrompt("Just this.", []);
    expect(prompt).toContain("Just this.");
    expect(prompt).not.toMatch(/Conversation so far/i);
  });

  it("caps history to the most recent turns server-side, dropping the oldest", () => {
    const many = Array.from({ length: 50 }, (_, i) => ({
      role: "user" as const,
      content: `HISTMARK_${i}`,
    }));
    const prompt = buildUserPrompt("now", [], many);
    expect(prompt).toContain("HISTMARK_49");
    expect(prompt).not.toContain("HISTMARK_0");
  });

  it("caps a single very long turn so the prompt cannot grow unbounded", () => {
    const huge = "x".repeat(20000);
    const prompt = buildUserPrompt("now", [], [{ role: "user", content: huge }]);
    expect(prompt.length).toBeLessThan(huge.length);
  });

  it("still includes intra-run observations alongside conversation history", () => {
    const prompt = buildUserPrompt("q", ["Step 1: called search_memory -> ok"], [
      { role: "user", content: "earlier turn" },
    ]);
    expect(prompt).toContain("earlier turn");
    expect(prompt).toContain("Observations so far");
    expect(prompt).toContain("Step 1: called search_memory");
  });
});

describe("MEMORY_INSTRUCTIONS — multi-turn citation safety (N3)", () => {
  it("requires calling search_memory every turn so prior-turn citations are not scrubbed", () => {
    expect(MEMORY_INSTRUCTIONS).toMatch(/every turn|each turn|follow-up/i);
  });
});
