import { createToolRegistry } from "../tools/registry";
import type { ToolRegistry } from "../tools/registry";
import { searchMemoryTool } from "../tools/search-memory";
import { addMemoryNoteTool } from "../tools/add-memory-note";
import { webSearchTool } from "../tools/web-search";
import { webFetchTool } from "../tools/web-fetch";
import { apolloSearchPeopleTool, apolloEnrichContactTool } from "../tools/apollo";

export function memoryRegistry(): ToolRegistry {
  return createToolRegistry([
    searchMemoryTool,
    addMemoryNoteTool,
    webSearchTool,
    webFetchTool,
    apolloSearchPeopleTool,
    apolloEnrichContactTool,
  ]);
}
