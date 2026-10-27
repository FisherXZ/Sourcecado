import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useId, useState } from "react";

import { useInspector } from "./Inspector";
import { ActionIcon, StatusIcon, ToolIcon } from "./ProcessIcons";
import {
  failureOf,
  formatElapsed,
  recoveryOf,
  stepElapsedMs,
  stepLabel,
  type StepState,
} from "./processState";
import { useRecoveryActions } from "./recovery";
import { toolPresentation } from "./toolRegistry";
import { sanitizeApolloNameMasks } from "../personName";

/** Words for every state except the two that read fine as an icon alone. */
const STATE_WORDS: Readonly<Partial<Record<StepState, string>>> = {
  waiting: "Needs approval",
  partial: "Partial",
  failed: "Failed",
  denied: "Denied",
  interrupted: "Interrupted",
  unknown: "Outcome unknown",
};

const SCREEN_READER_WORDS: Readonly<Partial<Record<StepState, string>>> = {
  running: "Running",
  done: "Completed",
};

/**
 * The right edge of a row or of the receipt line: elapsed while running, the
 * result count, the state icon, and a state word for every outcome that is
 * not a plain success.
 */
export function StepStatus({
  state,
  count,
  elapsed,
}: {
  readonly state: StepState;
  readonly count?: string | null;
  readonly elapsed?: string | null;
}) {
  const word = STATE_WORDS[state];
  return (
    <span className="sourcecado-step-status">
      {elapsed ? <span className="sourcecado-step-elapsed">{elapsed}</span> : null}{" "}
      {count ? <span className="sourcecado-step-count">{count}</span> : null}{" "}
      <StatusIcon state={state} />{" "}
      {word ? (
        <span className="sourcecado-step-state">{word}</span>
      ) : (
        <span className="sr-only">{SCREEN_READER_WORDS[state]}</span>
      )}
    </span>
  );
}

export function StepRow({
  tool,
  state,
  now,
}: {
  readonly tool: ToolCallMessagePart;
  readonly state: StepState;
  readonly now: number;
}) {
  const presentation = toolPresentation(tool.toolName);
  const failure = failureOf(tool);
  const recovery = recoveryOf(tool);
  const recoveryStatus = recovery?.status ? String(recovery.status) : null;
  const canRecover =
    state === "failed" &&
    failure !== null &&
    recoveryStatus !== "succeeded" &&
    recoveryStatus !== "skipped" &&
    recoveryStatus !== "denied" &&
    recoveryStatus !== "interrupted";
  const { act } = useRecoveryActions();
  const { select } = useInspector();
  const [showFailureDetail, setShowFailureDetail] = useState(false);
  // The operator's open or close choice wins. Until they make one, a step
  // that needs a decision from them opens by itself and the rest stay shut.
  const [chosen, setChosen] = useState<boolean | null>(null);
  const needsAttention =
    state === "failed" ? failure !== null : state === "unknown";
  const open = chosen ?? needsAttention;
  const detailId = useId();

  const label = stepLabel(tool, state);
  const finished = state === "done" || state === "partial";
  const summary = presentation.argsSummary?.(tool.args) ?? null;
  const count = finished ? (presentation.resultCount?.(tool.result) ?? null) : null;
  const elapsedMs = stepElapsedMs(tool, state, now);
  const inspectorArgs = tool.toolName.startsWith("apollo_")
    ? sanitizeApolloNameMasks(tool.args)
    : tool.args;
  const inspectorResult = tool.toolName.startsWith("apollo_")
    ? sanitizeApolloNameMasks(tool.result)
    : tool.result;

  return (
    <li
      className={`sourcecado-step sourcecado-step-${state}`}
      data-tool-call-id={tool.toolCallId}
      {...(presentation.slot ? { "data-renderer-slot": presentation.slot } : {})}
    >
      <button
        type="button"
        className="sourcecado-step-toggle"
        aria-expanded={open}
        aria-controls={detailId}
        onClick={() => setChosen(!open)}
      >
        <ToolIcon name={presentation.icon} />{" "}
        <span className="sourcecado-step-label">
          <span className="sourcecado-step-verb">{label}</span>
          {summary ? (
            <>
              {" "}
              <span className="sourcecado-step-object">{summary}</span>
            </>
          ) : null}
        </span>{" "}
        <StepStatus
          state={state}
          count={count}
          elapsed={
            state === "running" && elapsedMs !== null
              ? formatElapsed(elapsedMs, true)
              : null
          }
        />{" "}
        <ActionIcon name="chevron" />
      </button>
      {open ? (
        <div id={detailId} className="sourcecado-step-detail">
          {state === "denied" ? <p>Denied. This step did not run.</p> : null}
          {state === "interrupted" ? (
            <p>The run stopped before this step finished.</p>
          ) : null}
          {state === "unknown" ? (
            <p>No result was recorded. The step may have run. Check it before you repeat it.</p>
          ) : null}
          {state === "failed" && failure ? <p role="alert">{failure.summary}</p> : null}
          {recovery?.outcome ? <p>{String(recovery.outcome)}</p> : null}
          {elapsedMs !== null && state !== "running" ? (
            <p>Took {formatElapsed(elapsedMs)}</p>
          ) : null}
          <div className="sourcecado-step-actions">
            <button
              type="button"
              className="sourcecado-inspect-tool"
              aria-label={`Inspect ${presentation.label}`}
              title="Inspect"
              onClick={(event) =>
                select(
                  {
                    kind: "tool",
                    id: tool.toolCallId,
                    title: presentation.label,
                    status: "success",
                    args: inspectorArgs,
                    result: inspectorResult,
                    timing: tool.timing,
                  },
                  event.currentTarget,
                )
              }
            >
              <ActionIcon name="inspect" />
            </button>
            {canRecover && failure ? (
              <div className="sourcecado-recovery-actions">
                <button type="button" onClick={() => act("retry", failure)}>
                  <ActionIcon name="retry" />
                  Retry failed step
                </button>
                {failure.repair_route ? (
                  <a
                    href={failure.repair_route}
                    onClick={() => act("repair", failure)}
                  >
                    <ActionIcon name="repair" />
                    Repair {failure.source}
                  </a>
                ) : null}
                <button type="button" onClick={() => act("continue", failure)}>
                  <ActionIcon name="continue" />
                  Continue without {failure.source}
                </button>
                <button
                  type="button"
                  aria-expanded={showFailureDetail}
                  onClick={() => setShowFailureDetail((current) => !current)}
                >
                  <ActionIcon name="details" />
                  Failure details
                </button>
              </div>
            ) : null}
          </div>
          {showFailureDetail && failure ? (
            <pre className="sourcecado-failure-detail">{failure.detail}</pre>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
