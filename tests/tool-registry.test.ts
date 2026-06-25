import { z } from "zod";
import { createToolRegistry } from "@/lib/tools/registry";
import type { Tool } from "@/lib/tools/types";

function fakeTool(name: string, permissionClass: Tool["permissionClass"]): Tool {
  return {
    name,
    description: `${name} tool`,
    permissionClass,
    argsSchema: z.object({}),
    execute: async () => ({ ok: true }),
  };
}

describe("createToolRegistry", () => {
  it("registers and retrieves tools by name", () => {
    const registry = createToolRegistry([fakeTool("a", "read")]);
    expect(registry.get("a")?.name).toBe("a");
    expect(registry.get("missing")).toBeUndefined();
  });

  it("throws on duplicate tool name", () => {
    const registry = createToolRegistry([fakeTool("a", "read")]);
    expect(() => registry.register(fakeTool("a", "read"))).toThrow(/already registered/);
  });

  it("lists only tools whose class is in the allowed set", () => {
    const registry = createToolRegistry([
      fakeTool("r", "read"),
      fakeTool("d", "draft"),
      fakeTool("a", "admin"),
    ]);
    const names = registry
      .list(new Set(["read", "draft"]))
      .map((t) => t.name)
      .sort();
    expect(names).toEqual(["d", "r"]);
  });
});
