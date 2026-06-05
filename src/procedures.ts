import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { ProcedureDoc } from "./types.js";

export const DEFAULT_PROCEDURES_DIR = "procedures";

export function loadProcedures(proceduresDir = DEFAULT_PROCEDURES_DIR): ProcedureDoc[] {
  const resolvedDir = resolve(proceduresDir);
  if (!existsSync(resolvedDir)) {
    return [];
  }

  return readdirSync(resolvedDir, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b))
    .map((name) => {
      const path = join(resolvedDir, name);
      return {
        name,
        path,
        content: readFileSync(path, "utf8")
      };
    });
}
