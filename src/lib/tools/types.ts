import type postgres from "postgres";
import type { z } from "zod";
import type { MemoryActor } from "../memory/actor";

export type Sql = postgres.Sql;

export type PermissionClass =
  | "read"
  | "enrich"
  | "reason"
  | "draft"
  | "write_internal"
  | "admin";

export const PERMISSION_CLASSES: readonly PermissionClass[] = [
  "read",
  "enrich",
  "reason",
  "draft",
  "write_internal",
  "admin",
];

export interface ToolContext {
  db: Sql;
  runId: number;
  parentStepId: number;
  // Who this run executes as. Required, with no DEFAULT_ACTOR fallback: memory
  // tools filter on it, so a forgotten actor would silently read another
  // director's sources. Required makes that a compile error instead. Tests and
  // CLI scripts pass DEFAULT_ACTOR explicitly.
  actor: MemoryActor;
}

export interface Tool<TArgs = unknown, TResult = unknown> {
  name: string;
  description: string;
  permissionClass: PermissionClass;
  argsSchema: z.ZodType<TArgs>;
  execute(args: TArgs, ctx: ToolContext): Promise<TResult>;
}
