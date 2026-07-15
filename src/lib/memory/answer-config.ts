import { createToolRegistry } from "../tools/registry";
import type { ToolRegistry } from "../tools/registry";
import { searchMemoryTool } from "../tools/search-memory";

export function memoryRegistry(): ToolRegistry {
  return createToolRegistry([searchMemoryTool]);
}
