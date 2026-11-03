import { readFileSync } from "node:fs";

const permissions = readFileSync("../backend/coworker/permissions.py", "utf8");
const crm = readFileSync("../backend/coworker/crm_tools.py", "utf8");

/** Read the simple permission declarations, including their CRM registry unions.
 * This does not execute Python or require backend dependencies in the GUI job.
 */
export function backendPermissionNames(variable: string): Set<string> {
  const block = permissions.match(new RegExp(`^${variable} = frozenset\\(\\s*\\{([^}]*)\\}`, "m"))?.[1];
  if (block === undefined) throw new Error(`Missing permission set: ${variable}`);
  const values = new Set(Array.from(block.matchAll(/"([a-z_]+)"/g), match => match[1]));
  for (const [, registry] of block.matchAll(/\*([A-Z_]+)/g)) {
    const entries = registry === "CRM_TOOL_NAMES"
      ? Array.from(crm.matchAll(/_schema\("([a-z_]+)"/g), match => match[1])
      : registry === "CRM_READ_TOOLS"
        ? Array.from((crm.match(/CRM_READ_TOOLS = frozenset\(\{([^}]*)\}/)?.[1] ?? "").matchAll(/"([a-z_]+)"/g), match => match[1])
        : [];
    if (!entries.length) throw new Error(`Unresolved permission registry: ${registry}`);
    for (const entry of entries) values.add(entry);
  }
  return values;
}
