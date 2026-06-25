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

  it("appends the instructions string after the tool catalog", () => {
    const instructions = "INSTRUCTIONS_MARKER: ground every answer.";
    const prompt = buildAgentSystemPrompt([echoTool], instructions);
    expect(prompt).toContain(instructions);
    // The instructions block comes after the tool catalog entry.
    expect(prompt.indexOf(instructions)).toBeGreaterThan(prompt.indexOf("echo (read):"));
  });
});
