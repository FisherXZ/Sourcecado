import { z } from "zod";
import { listOutreachHistory, type OutreachHistoryEntry } from "../contacts/outreach";
import type { Tool } from "./types";

export const listOutreachHistoryTool: Tool<{ contactId: number }, OutreachHistoryEntry[]> = {
  name: "list_outreach_history",
  description:
    "List a Contact's past interaction timeline (most recent first), given the contactId from a " +
    "prior get_contact call. Returns an empty array when there is no recorded history yet, not an error.",
  permissionClass: "read",
  argsSchema: z.object({ contactId: z.number().int().positive() }),
  async execute(args, ctx) {
    return listOutreachHistory(ctx.db, args.contactId);
  },
};
