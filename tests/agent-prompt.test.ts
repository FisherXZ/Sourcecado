import { buildAgentSystemPrompt } from "@/lib/harness";
import { echoTool } from "@/lib/tools/echo";

describe("buildAgentSystemPrompt", () => {
  it("lists each tool with its name, class, and args JSON schema", () => {
    const prompt = buildAgentSystemPrompt([echoTool]);
    expect(prompt).toContain("echo (read):");
    // The model needs the tool's argument shape, not just its name/description.
    expect(prompt).toContain("args JSON schema:");
    expect(prompt).toContain('"text"');
  });

  it("handles an empty tool set", () => {
    expect(buildAgentSystemPrompt([])).toContain("(none)");
  });
});
