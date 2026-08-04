import { createToolRegistry } from "../tools/registry";
import type { ToolRegistry } from "../tools/registry";
import type { Tool } from "../tools/types";
import { loadPersona, type Persona } from "../persona";
import { searchMemoryTool } from "../tools/search-memory";
import { addMemoryNoteTool } from "../tools/add-memory-note";
import { webSearchTool } from "../tools/web-search";
import { webFetchTool } from "../tools/web-fetch";
import { apolloSearchPeopleTool, apolloEnrichContactTool } from "../tools/apollo";

// Every tool the chat path can hand a persona. A persona's frontmatter picks
// from this set; it cannot introduce a tool that isn't implemented here.
const AVAILABLE_TOOLS: Tool[] = [
  searchMemoryTool,
  addMemoryNoteTool,
  webSearchTool,
  webFetchTool,
  apolloSearchPeopleTool,
  apolloEnrichContactTool,
];

// The persona declares its own capability envelope, so §6 ("describe your
// capabilities from your tool list") stays true after a persona edit. An unknown
// tool name throws rather than being skipped — silently dropping it would shrink
// the agent with no signal, which is the failure mode this validation exists for.
export function memoryRegistry(persona: Persona = loadPersona()): ToolRegistry {
  const byName = new Map(AVAILABLE_TOOLS.map((tool) => [tool.name, tool]));

  const tools = persona.tools.map((name) => {
    const tool = byName.get(name);
    if (!tool) {
      throw new Error(
        `Persona "${persona.id}" declares unknown tool "${name}". Available: ${[...byName.keys()].join(", ")}`
      );
    }
    return tool;
  });

  return createToolRegistry(tools);
}
