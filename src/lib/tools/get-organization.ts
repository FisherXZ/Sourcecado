import { z } from "zod";
import { resolveOrganization, type OrganizationResolution } from "../contacts/resolve";
import type { Tool } from "./types";

export const getOrganizationTool: Tool<{ name: string }, OrganizationResolution> = {
  name: "get_organization",
  description:
    "Look up an Organization by name. Returns { status: 'found', organization, contacts } with " +
    "every known Contact at that org on an unambiguous match, { status: 'ambiguous', candidates } " +
    "when the name matches more than one Organization, or { status: 'not_found' }.",
  permissionClass: "read",
  argsSchema: z.object({ name: z.string().min(1) }),
  async execute(args, ctx) {
    return resolveOrganization(ctx.db, args.name);
  },
};
