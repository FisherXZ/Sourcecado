import type { ToolCallMessagePart } from "@assistant-ui/react";
import { useId, useState } from "react";

import { Glyph } from "../generative/ResultCard";
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
import { failureReason, recoveryChoices } from "./recovery";
import { showResultCard, stepHasResultCard } from "./resultCards";
import { StepRecovery } from "./StepRecovery";
import { toolPresentation } from "./toolRegistry";
import { sanitizeApolloNameMasks } from "../personName";

/** Words for every state except the two that read fine as an icon alone. */
const STATE_WORDS: Readonly<Partial<Record<StepState, string>>> = {
  waiting: "Needs approval",
  partial: "Partial",
  failed: "Failed",
  blocked: "Not allowed",
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
 * not a plain success. A failed step may say why in place of the word.
 */
export function StepStatus({
  state,
  count,
  elapsed,
  reason,
}: {
  readonly state: StepState;
  readonly count?: string | null;
  readonly elapsed?: string | null;
  /** A short reason that replaces the state word, such as "Could not connect". */
  readonly reason?: string | null;
}) {
  const word = reason ?? STATE_WORDS[state];
  return (
    <span className="sourcecado-step-status">
      {elapsed ? <span className="sourcecado-step-elapsed">{elapsed}</span> : null}{" "}
      {count ? <span className="sourcecado-step-count">{count}</span> : null}{" "}
      <StatusIcon state={state} />{" "}
      {word ? (
        <span className="sourcecado-step-state">
          {reason && reason !== STATE_WORDS[state] ? (
            <span className="sr-only">{STATE_WORDS[state]}: </span>
          ) : null}
          {word}
        </span>
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
  const choices = recoveryChoices(state, failure, recovery);
  const { select } = useInspector();
  const [showFailureDetail, setShowFailureDetail] = useState(false);
  // The operator's open or close choice wins. Until they make one, a step
  // that needs a decision from them opens by itself and the rest stay shut.
  const [chosen, setChosen] = useState<boolean | null>(null);
  const needsAttention =
    state === "failed"
      ? failure !== null
      : state === "blocked" || state === "unknown";
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
      <div className="sourcecado-step-row">
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
          reason={state === "failed" && failure ? failureReason(failure) : null}
          elapsed={
            state === "running" && elapsedMs !== null
              ? formatElapsed(elapsedMs, true)
              : null
          }
        />{" "}
        <ActionIcon name="chevron" />
      </button>
      {choices && failure ? (
        <StepRecovery
          label={summary ? `${label} ${summary}` : label}
          failure={failure}
          choices={choices}
          recoveryKey={`${String(recovery?.commandId ?? "")}:${String(recovery?.status ?? "")}`}
        />
      ) : null}
      </div>
      {open ? (
        <div id={detailId} className="sourcecado-step-detail">
          {state === "denied" ? <p>Denied. This step did not run.</p> : null}
          {state === "interrupted" ? (
            <p>The run stopped before this step finished.</p>
          ) : null}
          {state === "unknown" ? (
            <p>No result was recorded. The step may have run. Check it before you repeat it.</p>
          ) : null}
          {(state === "failed" || state === "blocked") && failure ? (
            <p role="alert">{failure.summary}</p>
          ) : null}
          {choices?.retry?.asksApproval ? (
            <p>Retry asks for approval first.</p>
          ) : null}
          {recovery?.outcome ? <p>{String(recovery.outcome)}</p> : null}
          {elapsedMs !== null && state !== "running" ? (
            <p>Took {formatElapsed(elapsedMs)}</p>
          ) : null}
          <div className="sourcecado-step-actions">
            {stepHasResultCard(tool) ? (
              <button
                type="button"
                className="sourcecado-show-result"
                aria-label={`Show result of ${presentation.label}`}
                title="Show result"
                onClick={() => showResultCard(tool.toolCallId)}
              >
                <Glyph name="card" />
              </button>
            ) : null}
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
            {(state === "failed" || state === "blocked") && failure ? (
              <button
                type="button"
                className="sourcecado-failure-toggle"
                aria-expanded={showFailureDetail}
                onClick={() => setShowFailureDetail((current) => !current)}
              >
                <ActionIcon name="details" />
                Failure details
              </button>
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
