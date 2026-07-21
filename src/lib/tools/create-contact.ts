import { z } from "zod";
import { createContact, type CreateContactResult } from "../contacts/create";
import type { Tool } from "./types";

export const createContactTool: Tool<
  { name: string; role?: string; organizationName?: string },
  CreateContactResult
> = {
  name: "create_contact",
  description:
    "Record a new Contact (a new connection). Only name is required; role and organizationName " +
    "should be gathered from the Director before calling this when possible, since a missing field " +
    "becomes a visible gap on the Contact Profile Card rather than blocking the write. Returns " +
    "{ status: 'created', contact } or { status: 'ambiguous_organization', candidates } if " +
    "organizationName matches more than one existing Organization, in which case ask which one is " +
    "meant and call again rather than guessing.",
  permissionClass: "write_internal",
  argsSchema: z.object({
    name: z.string().min(1),
    role: z.string().min(1).optional(),
    organizationName: z.string().min(1).optional(),
  }),
  async execute(args, ctx) {
    return createContact(ctx.db, args);
  },
};
