import type { ToolCallMessagePart } from "@assistant-ui/react";
import type { ReactNode } from "react";

import type { InspectorStep, InspectorTarget } from "./Inspector";
import { failureOf, stepLabel, type StepState } from "./processState";
import { domainRendererFor, isEvidenceToolName, toolPresentation } from "./toolRegistry";
import { EvidenceSetResult } from "../generative/EvidenceSetResult";
import { sanitizeApolloNameMasks } from "../personName";

/** What the Output tab says when a step has no result to show. Same words as the step row. */
const NO_RESULT_NOTE: Readonly<Partial<Record<StepState, string>>> = {
  running: "Still running.",
  waiting: "Waiting for your decision. This step has not run.",
  denied: "Denied. This step did not run.",
  interrupted: "The run stopped before this step finished.",
  unknown: "No result was recorded. The step may have run. Check it before you repeat it.",
};

/** A short key that changes whenever the live data behind this turn's steps changes. */
export function turnRevision(
  tools: readonly ToolCallMessagePart[],
  states: readonly StepState[],
): string {
  return tools
    .map(
      (tool, index) =>
        `${tool.toolCallId}:${states[index]}:${tool.timing?.completedAt ?? ""}:${tool.result === undefined ? 0 : 1}`,
    )
    .join("|");
}

/**
 * The same card the thread shows for this result. It renders only for a
 * finished step: a quiet card must not imply a success the step did not have.
 */
function cardFor(tool: ToolCallMessagePart, state: StepState): ReactNode {
  if (state !== "done" && state !== "partial") return undefined;
  if (isEvidenceToolName(tool.toolName)) return <EvidenceSetResult tools={[tool]} />;
  const Renderer = domainRendererFor(tool.toolName);
  if (!Renderer) return undefined;
  return (
    <Renderer
      toolCallId={tool.toolCallId}
      toolName={tool.toolName}
      args={tool.args}
      result={tool.result}
      status="success"
    />
  );
}

/**
 * The inspector item for one tool step of a turn. Built when the operator
 * opens it, and again while it stays open and the turn changes.
 *
 * Apollo masks surnames with asterisks. The raw views and the tables get the
 * masked text replaced. The card gets the original result, because the card
 * reads the mask to say that a surname is hidden.
 */
export function toolInspectorTarget(
  tools: readonly ToolCallMessagePart[],
  states: readonly StepState[],
  toolCallId: string,
): InspectorTarget | null {
  const index = tools.findIndex((tool) => tool.toolCallId === toolCallId);
  const tool = tools[index];
  const state = states[index];
  if (!tool || !state) return null;
  const presentation = toolPresentation(tool.toolName);
  const apollo = tool.toolName.startsWith("apollo_");
  const failure = failureOf(tool);
  const failed = state === "failed" || state === "blocked";
  const steps: InspectorStep[] = tools.map((step, position) => ({
    id: step.toolCallId,
    label: stepLabel(step, states[position]!),
    icon: toolPresentation(step.toolName).icon,
    state: states[position]!,
    ...(step.timing ? { startedAt: step.timing.startedAt } : {}),
    ...(step.timing?.completedAt !== undefined
      ? { completedAt: step.timing.completedAt }
      : {}),
  }));
  const card = cardFor(tool, state);
  const note = NO_RESULT_NOTE[state];
  return {
    kind: "tool",
    id: tool.toolCallId,
    title: stepLabel(tool, state),
    status: failed ? "error" : state === "running" || state === "waiting" ? "loading" : "success",
    state,
    toolName: tool.toolName,
    icon: presentation.icon,
    steps,
    revision: turnRevision(tools, states),
    args: apollo ? sanitizeApolloNameMasks(tool.args) : tool.args,
    ...(tool.result !== undefined
      ? { result: apollo ? sanitizeApolloNameMasks(tool.result) : tool.result }
      : {}),
    ...(tool.timing ? { timing: tool.timing } : {}),
    ...(card ? { card } : {}),
    ...(note ? { note } : {}),
    ...(failed
      ? { errorSummary: failure?.summary ?? "This step did not finish." }
      : {}),
  };
}
