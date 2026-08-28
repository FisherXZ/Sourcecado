import { useSendComposer } from "./ComposerDraftContext";
import {
  CONTINUE_RUN_PROMPT,
  runBudgetStopText,
  runBudgetWarningText,
  type RunBudgetStatus,
} from "./protocol";

const RECEIPT_LIMIT = 12;

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

function money(value: number): string {
  const digits = value === 0 ? 2 : value < 0.01 ? 4 : 2;
  return `$${value.toFixed(digits)}`;
}

/**
 * What the run is spending, in the same dense strip the header metrics use.
 * Deliberately quiet: it sits under the activity list and reads as a status
 * line, not as an alarm.
 */
function RunBudgetStrip({ status }: { readonly status: RunBudgetStatus }) {
  const { consumed, limits } = status;
  return (
    <section
      className="sourcecado-run-metrics sourcecado-run-metrics-running"
      aria-label="Run budget"
    >
      <span>{`Step ${consumed.model_turns} of ${limits.model_turns}`}</span>
      <span>{`${consumed.tool_calls} of ${limits.tool_calls} tool calls`}</span>
      <span>
        {`${duration(consumed.elapsed_seconds)} of ${duration(limits.elapsed_seconds)}`}
      </span>
      <span>
        {`${money(consumed.estimated_cost_usd)} of ${money(limits.estimated_cost_usd)} est.`}
      </span>
    </section>
  );
}

/**
 * The stop. A run that ran out of budget did not finish, so this says so
 * first, then lists what it actually completed, what it had queued, and
 * offers to go on.
 *
 * Continue puts the director's own message in the composer and sends it. It
 * starts an ordinary turn on the same conversation, which is why nothing here
 * carries permission forward: every approval gate is asked again by the run
 * itself.
 */
function RunBudgetStop({ status }: { readonly status: RunBudgetStatus }) {
  const send = useSendComposer();
  const receipts = status.completed.slice(0, RECEIPT_LIMIT);
  const hidden = status.completed.length - receipts.length;
  return (
    <section
      className="sourcecado-notice"
      role="note"
      aria-label="Run stopped before finishing"
    >
      <p className="sourcecado-notice-title">This run did not finish.</p>
      <p className="sourcecado-notice-detail">{runBudgetStopText(status)}</p>
      {receipts.length > 0 ? (
        <ul aria-label="Completed tool steps">
          {receipts.map((receipt) => (
            <li key={receipt.id}>
              {receipt.name}
              {receipt.ok ? "" : " — failed"}
            </li>
          ))}
          {hidden > 0 ? <li>{`and ${hidden} more`}</li> : null}
        </ul>
      ) : null}
      {status.continue_available && send ? (
        <div className="sourcecado-recovery-actions">
          <button type="button" onClick={() => send(CONTINUE_RUN_PROMPT)}>
            Continue this run
          </button>
        </div>
      ) : null}
    </section>
  );
}

/** The warning that arrives before a budget runs out. One sentence, no badge. */
function RunBudgetWarning({
  status,
}: {
  readonly status: RunBudgetStatus;
}) {
  if (!status.warning) return null;
  return (
    <p className="sourcecado-terminal-receipt" role="status">
      {runBudgetWarningText(status.warning)}
    </p>
  );
}

/** Picks the one view that fits the run's current state. */
export function RunBudgetView({
  status,
  messageState,
}: {
  readonly status: RunBudgetStatus | undefined;
  readonly messageState: unknown;
}) {
  if (!status) return null;
  if (status.state === "exhausted") return <RunBudgetStop status={status} />;
  if (status.state === "finished") return null;
  const live = messageState === "running" || messageState === "waiting-approval";
  if (!live) return null;
  return (
    <>
      <RunBudgetStrip status={status} />
      <RunBudgetWarning status={status} />
    </>
  );
}
