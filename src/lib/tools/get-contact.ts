import { z } from "zod";
import { resolveContact, type ContactResolution } from "../contacts/resolve";
import type { Tool } from "./types";

export const getContactTool: Tool<{ name: string }, ContactResolution> = {
  name: "get_contact",
  description:
    "Look up a Contact by name. Returns { status: 'found', contact } on an unambiguous match, " +
    "{ status: 'ambiguous', candidates } when the name matches more than one Contact (ask which " +
    "one before continuing), or { status: 'not_found' }. Never guesses between candidates.",
  permissionClass: "read",
  argsSchema: z.object({ name: z.string().min(1) }),
  async execute(args, ctx) {
    return resolveContact(ctx.db, args.name);
  },
};
