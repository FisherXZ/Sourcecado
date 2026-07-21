import { createToolRegistry } from "../tools/registry";
import type { ToolRegistry } from "../tools/registry";
import { searchMemoryTool } from "../tools/search-memory";
import { addMemoryNoteTool } from "../tools/add-memory-note";
import { getContactTool } from "../tools/get-contact";
import { getOrganizationTool } from "../tools/get-organization";
import { createContactTool } from "../tools/create-contact";
import { listOutreachHistoryTool } from "../tools/list-outreach-history";

export function memoryRegistry(): ToolRegistry {
  return createToolRegistry([
    searchMemoryTool,
    addMemoryNoteTool,
    getContactTool,
    getOrganizationTool,
    createContactTool,
    listOutreachHistoryTool,
  ]);
}
