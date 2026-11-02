import type { ToolCallMessagePart } from "@assistant-ui/react";

import { failureOf, stepState } from "./processState";

/**
 * The tools that only read. Running one again cannot send, enrich, write or
 * run a command. This is a copy of `RETRY_SAFE` in
 * `backend/coworker/permissions.py`, which stays the source of truth. A test
 * reads that file and fails when the two lists differ.
 */
export const READ_ONLY_TOOLS: ReadonlySet<string> = new Set([
  "now",
  "load_skill",
  "gmail_search",
  "gmail_read",
  "drive_search",
  "drive_list_folder",
  "drive_read",
  "drive_index_query",
  "calendar_list",
  "apollo_search_people",
  "board_get",
  "board_query",
]);

/**
 * Why Retry is off for a whole turn, or null when it may run.
 *
 * Retry sends the operator's message again as a new turn. The model starts
 * over and may call the same tools. That is safe only when every step of the
 * turn is a tool that reads. Any other step may already have acted: a send,
 * a paid enrichment, a write, a command, or a tool this list does not know.
 * An unknown tool counts as not safe. A step with no recorded result counts
 * as not safe too, because it may have run.
 */
export function turnRetryBlock(
  tools: readonly ToolCallMessagePart[],
  messageState: unknown,
): string | null {
  if (tools.some((part) => part.approval !== undefined)) {
    return "This turn used an approval. Retry could repeat the action.";
  }
  const open = tools.some((part) => {
    const state = stepState(part, messageState);
    return (
      state === "running" ||
      state === "waiting" ||
      state === "unknown" ||
      state === "interrupted"
    );
  });
  if (open) return "A step has no recorded result. Check it before you retry.";
  if (
    tools.some(
      (part) =>
        !READ_ONLY_TOOLS.has(part.toolName) ||
        failureOf(part)?.retry_safe === false,
    )
  ) {
    return "This turn ran a step that can change data. Retry could repeat it.";
  }
  return null;
}
