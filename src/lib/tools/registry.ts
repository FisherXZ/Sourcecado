import type { PermissionClass, Tool } from "./types";

export interface ToolRegistry {
  register(tool: Tool): void;
  get(name: string): Tool | undefined;
  list(allowed: Set<PermissionClass>): Tool[];
}

export function createToolRegistry(tools: Tool[] = []): ToolRegistry {
  const byName = new Map<string, Tool>();

  const registry: ToolRegistry = {
    register(tool) {
      if (byName.has(tool.name)) {
        throw new Error(`Tool "${tool.name}" is already registered.`);
      }
      byName.set(tool.name, tool);
    },
    get(name) {
      return byName.get(name);
    },
    list(allowed) {
      return [...byName.values()].filter((tool) => allowed.has(tool.permissionClass));
    },
  };

  for (const tool of tools) {
    registry.register(tool);
  }
  return registry;
}
