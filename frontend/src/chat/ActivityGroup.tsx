import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { useInspector } from "./Inspector";
import { toolInspectorTarget, turnRevision } from "./inspectorTargets";
import { ActionIcon } from "./ProcessIcons";
import {
  formatElapsed,
  isLiveMessage,
  layerElapsedMs,
  layerState,
  receiptState,
  stepState,
  stepsSegment,
} from "./processState";
import { StepRow, StepStatus } from "./StepRow";

/**
 * A row that joins the process layer ahead of the tool steps. The thinking
 * row (#225) is the first user: `summary` becomes the first segment of the
 * receipt ("Thought for 12s") and `node` renders as the first row.
 */
export type ProcessLead = {
  readonly summary: string;
  readonly node: ReactNode;
};

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * The process layer for one assistant turn: the tool step rows, above the
 * answer. One step shows as one row. Several steps group under a receipt
 * line ("Used 3 sources in 4s") that stays open while the turn works and
 * closes when the answer starts or the turn ends. The operator's own open or
 * close choice wins from then on. An open layer has a bounded height and
 * scrolls inside itself, so it never pushes the answer off screen.
 *
 * Domain result cards are not part of it. They render in the answer area
 * (see AnswerResults).
 */
export function ActivityGroup({
  tools,
  messageState,
  answerStarted = false,
  lead,
}: {
  readonly tools: readonly ToolCallMessagePart[];
  readonly messageState: unknown;
  /** True once answer text has streamed in after the last step. */
  readonly answerStarted?: boolean;
  readonly lead?: ProcessLead;
}) {
  const states = tools.map((tool) => stepState(tool, messageState));
  const state = layerState(states);
  const running = state === "running";
  const live = isLiveMessage(messageState) || running;
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = chosen ?? (live && !answerStarted);
  const bodyId = useId();
  const bodyRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [reducedMotion] = useState(prefersReducedMotion);
  const [, tick] = useState(0);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(
      () => tick((value) => value + 1),
      reducedMotion ? 5000 : 1000,
    );
    return () => window.clearInterval(id);
  }, [running, reducedMotion]);

  // Follow the newest step while the turn works, unless the operator has
  // scrolled up to read an earlier one.
  useEffect(() => {
    const body = bodyRef.current;
    if (open && live && body && followRef.current) {
      body.scrollTop = body.scrollHeight;
    }
  }, [open, live, tools.length]);

  // An open inspector item follows its step: when the step or the turn
  // changes, the panel shows the new state, not the state at the click.
  const { selected, sync } = useInspector();
  useEffect(() => {
    if (!selected || !tools.some((tool) => tool.toolCallId === selected.id)) return;
    if (selected.revision === turnRevision(tools, states)) return;
    const next = toolInspectorTarget(tools, states, selected.id);
    if (next) sync(next);
  }, [selected, tools, messageState]);

  const rowCount = tools.length + (lead ? 1 : 0);
  if (rowCount === 0) return null;
  const now = Date.now();
  const rows = (
    <ol className="sourcecado-steps">
      {lead ? (
        <li className="sourcecado-step sourcecado-step-lead">{lead.node}</li>
      ) : null}
      {tools.map((tool, index) => (
        <StepRow
          key={tool.toolCallId}
          tool={tool}
          state={states[index]!}
          now={now}
          turnFailed={messageState === "failed"}
          inspect={() => toolInspectorTarget(tools, states, tool.toolCallId)}
        />
      ))}
    </ol>
  );
  // The receipt also speaks for the turn: a working mark between steps, and
  // Interrupted after a stop even when every step finished.
  const shown = receiptState(state, messageState, answerStarted);
  const className = `sourcecado-activity sourcecado-activity-${shown}`;
  if (rowCount === 1) return <section className={className}>{rows}</section>;

  const elapsedMs = layerElapsedMs(tools, states, now);
  const stepSummary = [
    stepsSegment(tools, shown === "running"),
    elapsedMs !== null ? `in ${formatElapsed(elapsedMs, running)}` : null,
  ]
    .filter(Boolean)
    .join(" ");
  const receipt = [lead?.summary, tools.length > 0 ? stepSummary : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <section className={className}>
      <button
        type="button"
        className="sourcecado-activity-receipt"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setChosen(!open)}
      >
        <span className="sourcecado-activity-summary">{receipt}</span>{" "}
        <StepStatus state={shown} />{" "}
        <ActionIcon name="chevron" />
      </button>
      {open ? (
        <div
          id={bodyId}
          ref={bodyRef}
          className="sourcecado-activity-body"
          role="group"
          aria-label="Steps"
          tabIndex={0}
          onScroll={(event) => {
            const body = event.currentTarget;
            followRef.current =
              body.scrollHeight - body.scrollTop - body.clientHeight < 24;
          }}
        >
          {rows}
        </div>
      ) : null}
    </section>
  );
}
