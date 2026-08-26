import { summarizeStep } from "@/lib/memory/answer";

describe("summarizeStep", () => {
  it("counts facts and chunks for a successful search_memory step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "search_memory",
      ok: true,
      observation: 'Success: {"acceptedFacts":[1,2,3],"gapFacts":[1],"chunks":[1,2]}',
    });
    expect(part).toMatchObject({ index: 1, tool: "search_memory", ok: true });
    expect(part.detail).toBe("4 facts, 2 chunks");
  });

  it("singularizes one fact / one chunk", () => {
    const part = summarizeStep({
      index: 2,
      tool: "search_memory",
      ok: true,
      observation: 'Success: {"acceptedFacts":[1],"gapFacts":[],"chunks":[1]}',
    });
    expect(part.detail).toBe("1 fact, 1 chunk");
  });

  it("surfaces the error message (without the Error(...) prefix) for a failed step", () => {
    const part = summarizeStep({
      index: 1,
      tool: "search_memory",
      ok: false,
      observation: "Error (permission_denied): source not allowed",
    });
    expect(part.ok).toBe(false);
    expect(part.detail).toContain("source not allowed");
    expect(part.detail).not.toContain("Error (");
  });
});
