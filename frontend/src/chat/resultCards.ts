import type { ToolCallMessagePart } from "@assistant-ui/react";

import { isWaitingForApproval } from "./processState";
import { toolHasResultCard } from "./toolRegistry";

/** The DOM id of the result card for one tool call. */
export function cardAnchorId(toolCallId: string): string {
  return `sourcecado-card-${toolCallId}`;
}

/** True when the answer area shows a card for this step. */
export function stepHasResultCard(tool: ToolCallMessagePart): boolean {
  return toolHasResultCard(tool.toolName) && !isWaitingForApproval(tool);
}

/**
 * Scroll to the card for a step and put focus on its heading. The step row
 * calls this from a button. An href would change the hash, and the app routes
 * on the hash.
 */
export function showResultCard(toolCallId: string): boolean {
  const card = [...document.querySelectorAll<HTMLElement>("[data-card-tools]")].find(
    (element) => element.dataset.cardTools?.split(" ").includes(toolCallId),
  );
  if (!card) return false;
  card.scrollIntoView({ block: "nearest" });
  card.querySelector<HTMLElement>("h3")?.focus({ preventScroll: true });
  return true;
}
