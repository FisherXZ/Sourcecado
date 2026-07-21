import { z } from "zod";
import { addMemoryNote } from "../memory/notes";
import type { Tool } from "./types";

export const addMemoryNoteTool: Tool<{ title: string; text: string }, { sourceId: string }> = {
  name: "add_memory_note",
  description: "Record a sourcing memory note (or a correction). Becomes immediately searchable.",
  permissionClass: "write_internal",
  argsSchema: z.object({ title: z.string().min(1), text: z.string().min(1) }),
  async execute(args, ctx) {
    // Stamp the writing run so a note produced by a (possibly prompt-injected)
    // chat run is traceable back to that run and archivable.
    return addMemoryNote(ctx.db, { title: args.title, text: args.text, runId: ctx.runId });
  },
};
