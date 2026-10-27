import type { ToolCallMessagePart } from "@assistant-ui/react";

import { failureOf, stepState } from "./processState";

/**
 * Why Retry is off for an answer, or null when it may run.
 *
 * Retry sends the operator's earlier message again as a new turn. The model
 * starts over, so it may call the same tools again. Reading data is safe to
 * repeat. An action that already reached outside Sourcecado is not.
 *
 * Every send, enrichment and calendar write waits for an approval, so a tool
 * step that carries an approval is the sign that this turn may have acted.
 * A step with no recorded result may have run too. A failed step is judged by
 * the backend's own `retry_safe` flag, the same flag that gates retrying one
 * step. A new turn still asks for approval again, but the operator should
 * decide that on purpose, with a new message, not by one click on Retry.
 */
export function retryBlock(
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
  if (open) {
    return "A step has no recorded result. Check it before you retry.";
  }
  if (tools.some((part) => failureOf(part)?.retry_safe === false)) {
    return "A failed step is not safe to repeat.";
  }
  return null;
}
