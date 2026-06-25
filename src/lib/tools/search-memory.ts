import { z } from "zod";
import { searchMemory, type MemoryBundle } from "../memory/retrieve";
import type { Tool } from "./types";

export const searchMemoryTool: Tool<{ query: string; limit?: number }, MemoryBundle> = {
  name: "search_memory",
  description:
    "Search sourcing memory. Returns { intent, acceptedFacts, gapFacts, chunks } with citations. Use this to ground every answer.",
  permissionClass: "read",
  argsSchema: z.object({
    query: z.string().min(1),
    limit: z.number().int().positive().optional(),
  }),
  async execute(args, ctx) {
    return searchMemory(ctx.db, { query: args.query, limit: args.limit });
  },
};
