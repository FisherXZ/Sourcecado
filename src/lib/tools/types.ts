import type postgres from "postgres";
import type { z } from "zod";

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
}

export interface Tool<TArgs = unknown, TResult = unknown> {
  name: string;
  description: string;
  permissionClass: PermissionClass;
  argsSchema: z.ZodType<TArgs>;
  execute(args: TArgs, ctx: ToolContext): Promise<TResult>;
}
