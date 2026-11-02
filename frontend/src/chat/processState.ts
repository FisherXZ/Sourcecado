import type { ToolCallMessagePart } from "@assistant-ui/react";

import type { ToolFailure } from "./protocol";
import { toolPresentation } from "./toolRegistry";

/**
 * What a step in the process layer is doing or how it ended.
 *
 * Only `done` gets success styling. `partial`, `interrupted` and `unknown`
 * each keep their own word, because an operator who reads "done" will act on
 * it (send, enrich, stop checking).
 */
export type StepState =
  | "running"
  | "waiting"
  | "done"
  | "partial"
  | "failed"
  | "blocked"
  | "denied"
  | "interrupted"
  | "unknown";

const LIVE_MESSAGE_STATES = new Set(["running", "stopping", "waiting-approval"]);
const STOPPED_MESSAGE_STATES = new Set(["cancelled", "stopped", "interrupted"]);

/** True while the turn can still produce more steps or text. */
export function isLiveMessage(messageState: unknown): boolean {
  return typeof messageState === "string" && LIVE_MESSAGE_STATES.has(messageState);
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function sourcecadoMetadata(part: ToolCallMessagePart): Record<string, unknown> {
  return (part.providerMetadata?.sourcecado as Record<string, unknown>) ?? {};
}

export function failureOf(part: ToolCallMessagePart): ToolFailure | null {
  const failure = sourcecadoMetadata(part).failure;
  return failure && typeof failure === "object" ? (failure as ToolFailure) : null;
}

export function recoveryOf(part: ToolCallMessagePart): Record<string, unknown> | null {
  const recovery = sourcecadoMetadata(part).recovery;
  return recovery && typeof recovery === "object"
    ? (recovery as Record<string, unknown>)
    : null;
}

export function isWaitingForApproval(part: ToolCallMessagePart): boolean {
  return (
    part.approval !== undefined &&
    part.approval.approved !== true &&
    part.result === undefined
  );
}

export function stepState(
  part: ToolCallMessagePart,
  messageState: unknown,
): StepState {
  const approval = part.approval;
  // A denied approval also arrives as a failed result. The denial is the
  // outcome the operator needs, so it wins and the step shows one receipt.
  if (approval?.approved === false) return "denied";
  if (approval?.resolution) return "interrupted";
  const stopped =
    typeof messageState === "string" && STOPPED_MESSAGE_STATES.has(messageState);
  // The operator has not decided yet. This holds whether or not the turn is
  // still live: a retried step in a finished turn waits for approval too.
  const awaitingDecision =
    approval !== undefined &&
    approval.approved !== true &&
    sourcecadoMetadata(part).approvalState !== "resolved-elsewhere";
  if (awaitingDecision && part.result === undefined) {
    return stopped ? "interrupted" : "waiting";
  }
  const failure = failureOf(part);
  const errored = failure !== null || part.isError === true;
  // A denial reaches the browser as a failed result first and as the decision
  // second. Until the decision arrives, show the step as waiting, not failed.
  if (awaitingDecision && errored && isLiveMessage(messageState)) return "waiting";
  if (errored) {
    // A refusal with no approval record. The event does not say who refused:
    // the failure class is matched from the error text. "Not allowed" is the
    // most the record supports.
    return approval === undefined && failure?.class === "permission"
      ? "blocked"
      : "failed";
  }
  if (part.result !== undefined) {
    return record(part.result)?.partial === true ? "partial" : "done";
  }
  if (stopped) return "interrupted";
  // A message with no state at all is treated as still working, so a bare
  // step with no result reads as running rather than as an unknown outcome.
  if (messageState === undefined || isLiveMessage(messageState)) return "running";
  // The turn ended and no result was ever recorded. The step may have run.
  return "unknown";
}

/**
 * One state for the whole layer, for its receipt line. A step whose outcome
 * is unknown or interrupted outranks the rest: a receipt with a success mark
 * would cover it.
 */
export function layerState(states: readonly StepState[]): StepState {
  for (const state of ["running", "waiting", "unknown", "interrupted"] as const) {
    if (states.includes(state)) return state;
  }
  const first = states[0];
  if (first === undefined) return "done";
  if (states.every((state) => state === first)) return first;
  if (states.some((state) => state === "done" || state === "partial")) {
    return "partial";
  }
  return (["failed", "blocked"] as const).find((state) => states.includes(state)) ?? "denied";
}

/**
 * What the receipt line shows. The layer state describes the steps. The
 * receipt also describes the turn: while the turn still works and no answer
 * has started, a finished step gets a working mark instead of a success
 * check. After a stop, it says Interrupted even if every step finished.
 */
export function receiptState(
  state: StepState,
  messageState: unknown,
  answerStarted: boolean,
): StepState {
  if (state !== "done") return state;
  if (typeof messageState === "string" && STOPPED_MESSAGE_STATES.has(messageState)) {
    return "interrupted";
  }
  return isLiveMessage(messageState) && !answerStarted ? "running" : state;
}

const ANSWER_MIN_CHARS = 100;

/**
 * True once the answer is streaming: text that follows the last step and is
 * long enough, or multi-line, to be an answer. A lead-in sentence before the
 * next step is short and one line, so it does not count. Without that rule
 * the layer would close and reopen around every step of a model that writes
 * a sentence before each tool call.
 */
export function answerHasStarted(
  content: readonly { readonly type: string; readonly text?: string }[],
): boolean {
  let lastStep = -1;
  content.forEach((part, index) => {
    if (part.type === "tool-call") lastStep = index;
  });
  if (lastStep < 0) return false;
  const text = content
    .slice(lastStep + 1)
    .filter((part) => part.type === "text")
    .map((part) => part.text ?? "")
    .join("")
    .trim();
  return text.length >= ANSWER_MIN_CHARS || text.includes("\n");
}

/**
 * Milliseconds a step took, or null when it cannot be known. A step that
 * stopped without a recorded end has no honest duration, so it shows none
 * instead of counting up forever.
 */
export function stepElapsedMs(
  tool: ToolCallMessagePart,
  state: StepState,
  now: number,
): number | null {
  const startedAt = tool.timing?.startedAt;
  if (startedAt === undefined) return null;
  const completedAt = tool.timing?.completedAt;
  if (completedAt !== undefined) return Math.max(0, completedAt - startedAt);
  return state === "running" ? Math.max(0, now - startedAt) : null;
}

/**
 * Milliseconds the steps ran: the time covered by their start-to-end spans.
 * Model time between steps is not counted, and steps that overlap are counted
 * once.
 */
export function layerElapsedMs(
  tools: readonly ToolCallMessagePart[],
  states: readonly StepState[],
  now: number,
): number | null {
  const spans: [number, number][] = [];
  tools.forEach((tool, index) => {
    const startedAt = tool.timing?.startedAt;
    if (startedAt === undefined) return;
    const endedAt =
      tool.timing?.completedAt ?? (states[index] === "running" ? now : undefined);
    if (endedAt === undefined) return;
    spans.push([startedAt, Math.max(startedAt, endedAt)]);
  });
  if (spans.length === 0) return null;
  spans.sort((first, second) => first[0] - second[0]);
  let total = 0;
  let [from, to] = spans[0]!;
  for (const [start, end] of spans.slice(1)) {
    if (start > to) {
      total += to - from;
      from = start;
    }
    to = Math.max(to, end);
  }
  return total + (to - from);
}

/**
 * "4s" while a step runs (counts up by the second), "<1s" for anything under
 * a second. It never reads "0s".
 */
export function formatElapsed(ms: number, live = false): string {
  const seconds = live ? Math.floor(ms / 1000) : Math.round(ms / 1000);
  if (seconds < 1) return "<1s";
  if (seconds < 60) return `${seconds}s`;
  const rest = String(seconds % 60).padStart(2, "0");
  return `${Math.floor(seconds / 60)}m ${rest}s`;
}

const WORKSPACE_RECEIPT_LABELS: Readonly<Record<string, string>> = {
  created: "Created workspace file",
  updated: "Updated workspace file",
  moved: "Moved workspace item",
  trashed: "Trashed workspace item",
  shell_approved: "Ran approved workspace command",
  shell_auto_read: "Ran read-only workspace command",
  denied: "Workspace action denied",
  interrupted: "Workspace action interrupted",
  stale: "Workspace write was stale",
  read: "Read workspace data",
  directory_requested: "Requested workspace access",
};

function workspaceReceiptLabel(tool: ToolCallMessagePart): string | null {
  const receiptType = record(tool.result)?.receipt_type;
  return typeof receiptType === "string"
    ? WORKSPACE_RECEIPT_LABELS[receiptType] ?? null
    : null;
}

/**
 * The verb and object for a step row. Past tense only when the step
 * finished: "Searched Drive" with a failure mark would claim a search that
 * never completed.
 */
export function stepLabel(tool: ToolCallMessagePart, state: StepState): string {
  const presentation = toolPresentation(tool.toolName);
  if (state === "done" || state === "partial") {
    return workspaceReceiptLabel(tool) ?? presentation.label;
  }
  return presentation.activeLabel;
}

/** "Used 3 sources", "Ran 2 actions", or both. The receipt's step segment. */
export function stepsSegment(
  tools: readonly ToolCallMessagePart[],
  running: boolean,
): string {
  const sources = tools.filter(
    (tool) => toolPresentation(tool.toolName).category === "source",
  ).length;
  const actions = tools.length - sources;
  const used = running ? "using" : "used";
  const ran = running ? "running" : "ran";
  const sourcePhrase = `${sources} ${sources === 1 ? "source" : "sources"}`;
  const actionPhrase = `${actions} ${actions === 1 ? "action" : "actions"}`;
  const phrase =
    actions === 0
      ? `${used} ${sourcePhrase}`
      : sources === 0
        ? `${ran} ${actionPhrase}`
        : `${used} ${sourcePhrase}, ${ran} ${actionPhrase}`;
  return phrase.charAt(0).toUpperCase() + phrase.slice(1);
}
