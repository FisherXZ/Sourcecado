import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type FocusEvent,
  type KeyboardEvent,
} from "react";

import { ApprovalIcon } from "./approvalIcons";
import {
  approvalView,
  auditOf,
  canOfferAlways,
  executionText,
  formatDecisionTime,
  rawRequestText,
  receiptOf,
  scopeStatement,
  visibleReason,
  type ApprovalRow,
  type ApprovalState,
} from "./approvalModel";
import { CalendarApprovalSummary } from "../generative/CalendarEventResult";

/**
 * A value longer than this is cut and offered behind an expand control, so a
 * long write or message cannot push Allow and Deny out of reach.
 */
const VALUE_CLAMP = 280;

function ClampedValue({
  text,
  noun,
}: {
  readonly text: string;
  readonly noun: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const valueId = useId();
  const truncated = text.length > VALUE_CLAMP;
  const visible =
    truncated && !expanded ? `${text.slice(0, VALUE_CLAMP).trimEnd()}…` : text;
  return (
    <div className="sourcecado-approval-body">
      <p id={valueId}>{visible}</p>
      {truncated ? (
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={valueId}
          onClick={() => setExpanded((current) => !current)}
        >
          <ApprovalIcon name={expanded ? "chevron-down" : "chevron-right"} />
          {expanded ? `Collapse ${noun}` : `Expand ${noun}`}
        </button>
      ) : null}
    </div>
  );
}

function RowValue({ row }: { readonly row: ApprovalRow }) {
  if (row.value === null) {
    return <span className="sourcecado-approval-unavailable">{row.unavailable}</span>;
  }
  // A command is shown whole: cutting it could hide what will run. The table
  // scrolls inside a bounded region instead.
  if (row.variant === "code") return <code>{row.value}</code>;
  return (
    <ClampedValue
      text={row.value}
      noun={row.variant === "body" ? "draft body" : row.label.toLowerCase()}
    />
  );
}

function ApprovalTable({ rows }: { readonly rows: readonly ApprovalRow[] }) {
  return (
    <div
      className="sourcecado-approval-table-scroll"
      role="region"
      aria-label="Request fields"
      tabIndex={0}
    >
      <table className="sourcecado-approval-table">
        <thead>
          <tr>
            <th scope="col">Field</th>
            <th scope="col">Value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              <th scope="row">
                <ApprovalIcon name={row.icon} />
                <span>{row.label}</span>
              </th>
              <td>
                <RowValue row={row} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * True while the operator is typing: a text field has focus, or the composer
 * holds a draft. A card that appears then must not take focus.
 */
function operatorIsTyping(): boolean {
  const active = document.activeElement;
  if (
    active instanceof HTMLElement &&
    active.matches('input, textarea, select, [contenteditable=""], [contenteditable="true"]')
  ) {
    return true;
  }
  const composer = document.querySelector<HTMLTextAreaElement>(
    '[aria-label="Message Sourcecado"]',
  );
  return Boolean(composer && composer.value.trim());
}

export function ApprovalCard({
  part,
  onDecision,
}: {
  readonly part: ToolCallMessagePartProps;
  readonly onDecision: (
    approved: boolean,
    scope?: "once" | "always",
  ) => Promise<"queued" | void> | "queued" | void;
}) {
  const audit = auditOf(part);
  const view = approvalView(part);
  const [submitting, setSubmitting] = useState(false);
  const [submitQueued, setSubmitQueued] = useState(false);
  const [submitOutcomeUnknown, setSubmitOutcomeUnknown] = useState(false);
  const [submitFailed, setSubmitFailed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [showReceipt, setShowReceipt] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // Command-Enter works only after the operator has moved focus into the card
  // on purpose. Focus that the card gave itself on mount never counts.
  const armed = useRef(false);
  const mountFocus = useRef(false);
  const previousState = useRef<ApprovalState>(audit.approvalState);
  const detailsId = useId();
  const pending = audit.approvalState === "pending";
  const alwaysOffered = canOfferAlways(part.toolName, view.shell);
  const reason = visibleReason(part.approval?.reason, part.toolName);

  // The thread's live region announces that an approval is needed. Take focus
  // only when nobody is typing; otherwise the card would swallow keystrokes.
  useEffect(() => {
    if (!pending || operatorIsTyping()) return;
    mountFocus.current = true;
    // Focus alone must not scroll: a card that mounts while the operator
    // reads earlier output would pull the thread to the bottom. At the
    // bottom the thread already follows the new card.
    headingRef.current?.focus({ preventScroll: true });
    mountFocus.current = false;
  }, [pending]);

  useEffect(() => {
    if (
      previousState.current === "pending" &&
      audit.approvalState !== "pending"
    ) {
      setSubmitting(false);
      document
        .querySelector<HTMLTextAreaElement>('[aria-label="Message Sourcecado"]')
        ?.focus();
    }
    previousState.current = audit.approvalState;
  }, [audit.approvalState]);

  // The backend itself gives up waiting for execution after ~60s
  // (backend/coworker/inbox.py _DEFAULT_WAIT_TIMEOUT_SECONDS) and writes no
  // receipt for a non-terminal outcome. Mirror that deadline here: if no
  // approval_resolved has arrived by then, stop implying the decision is
  // still "in progress" — it may never resolve — without claiming it was
  // denied, failed, or allowed.
  useEffect(() => {
    if (!submitting) {
      setSubmitOutcomeUnknown(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setSubmitOutcomeUnknown(true);
    }, 60_000);
    return () => window.clearTimeout(timer);
  }, [submitting]);

  async function decide(approved: boolean, scope: "once" | "always" = "once") {
    // A standing grant exists only for the one shell command the model
    // allows. Refuse here as well as hiding the button, so no keyboard or
    // overflow path can request it for an email send or paid enrichment.
    if (scope === "always" && !(approved && alwaysOffered)) return;
    setSubmitting(true);
    setSubmitFailed(false);
    setSubmitOutcomeUnknown(false);
    setSubmitQueued(false);
    try {
      const outcome =
        scope === "once"
          ? await onDecision(approved)
          : await onDecision(approved, scope);
      if (outcome === "queued") setSubmitQueued(true);
    } catch {
      setSubmitting(false);
      setSubmitFailed(true);
    }
  }

  if (!pending) {
    const receipt = receiptOf(audit);
    const time = formatDecisionTime(audit.resolvedAt);
    const line = [receipt.label, view.title, receipt.note, time]
      .filter(Boolean)
      .join(" · ");
    const outcomeUnknown = audit.executionStatus === "interrupted";
    return (
      <section
        className="sourcecado-approval-receipt"
        data-tone={receipt.tone}
      >
        <button
          type="button"
          aria-expanded={showReceipt}
          aria-controls={detailsId}
          onClick={() => setShowReceipt((current) => !current)}
        >
          <ApprovalIcon name={receipt.icon} className="sourcecado-approval-state" />
          <span>{line}</span>
          <ApprovalIcon name={showReceipt ? "chevron-down" : "chevron-right"} />
        </button>
        {showReceipt ? (
          <div id={detailsId}>
            <p>
              {receipt.label}
              {audit.actor ? ` by ${audit.actor}` : ""}
            </p>
            <p>Scope: {audit.scope ?? "once"}</p>
            {outcomeUnknown ? (
              <p>
                {audit.executionError ??
                  "Outcome is unknown after Sourcecado restarted. Verify the external resource before retrying."}
              </p>
            ) : (
              <p>Execution {executionText(audit.executionStatus)}</p>
            )}
            {audit.resolvedAt ? (
              <time dateTime={audit.resolvedAt}>
                {new Date(audit.resolvedAt).toString() === "Invalid Date"
                  ? audit.resolvedAt
                  : new Date(audit.resolvedAt).toLocaleString()}
              </time>
            ) : null}
          </div>
        ) : null}
      </section>
    );
  }

  function onFocus() {
    if (!mountFocus.current) armed.current = true;
  }

  function onBlur(event: FocusEvent<HTMLElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      armed.current = false;
    }
  }

  // Command-Enter allows once, only after the operator moved focus into this
  // card on purpose and while the decision is not already in flight. Escape is
  // not handled: it never denies, never allows, and never closes the card.
  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (
      !armed.current ||
      event.key !== "Enter" ||
      !(event.metaKey || event.ctrlKey) ||
      event.repeat ||
      submitting
    ) {
      return;
    }
    if ((event.target as HTMLElement).closest('[data-decision="deny"]')) return;
    event.preventDefault();
    void decide(true);
  }

  return (
    <section
      className="sourcecado-approval-card"
      aria-busy={
        (submitting && !submitOutcomeUnknown && !submitQueued) || undefined
      }
      onKeyDown={onKeyDown}
      onFocus={onFocus}
      onBlur={onBlur}
      onPointerDown={() => {
        armed.current = true;
      }}
    >
      <div className="sourcecado-approval-head">
        <ApprovalIcon name={view.icon} className="sourcecado-approval-action" />
        <h2 ref={headingRef} tabIndex={-1}>
          {view.title}
        </h2>
      </div>
      {view.calendarWrite ? (
        <CalendarApprovalSummary
          args={part.args as Readonly<Record<string, unknown>>}
          toolName={part.toolName as "calendar_create" | "calendar_update"}
        />
      ) : (
        <ApprovalTable rows={view.rows} />
      )}
      {view.warning ? (
        <p className="sourcecado-approval-warning" role="note">
          <ApprovalIcon name={view.spendsCredits ? "coins" : "alert"} />
          <span>{view.warning}</span>
        </p>
      ) : null}
      {view.caveat ? (
        <p className="sourcecado-approval-warning" role="note">
          <ApprovalIcon name="alert" />
          <span>{view.caveat}</span>
        </p>
      ) : null}
      {reason ? <p className="sourcecado-approval-reason">{reason}</p> : null}
      {showDetails ? (
        <div id={detailsId} className="sourcecado-approval-details">
          {view.shell || part.toolName === "shell_exec" ? (
            <p>
              Exact fingerprint:{" "}
              {view.shell?.fingerprint?.slice(0, 16) ?? "unavailable"}
            </p>
          ) : (
            <pre>{rawRequestText(part)}</pre>
          )}
          <p>{scopeStatement(part.toolName)}</p>
        </div>
      ) : null}
      {submitFailed ? (
        <p role="alert" className="sourcecado-approval-status">
          The decision couldn’t be saved. Try again.
        </p>
      ) : null}
      {submitOutcomeUnknown ? (
        <p className="sourcecado-approval-status">
          Outcome is unknown. Sourcecado didn’t confirm this decision before
          its wait ended. Verify the external resource before retrying.
        </p>
      ) : submitQueued ? (
        <p className="sourcecado-approval-status">
          Waiting for the connection. Your decision will send once
          Sourcecado reconnects.
        </p>
      ) : submitting ? (
        <p className="sourcecado-approval-status">Submitting decision…</p>
      ) : null}
      <div className="sourcecado-approval-foot">
        <button
          type="button"
          className="sourcecado-approval-toggle"
          aria-expanded={showDetails}
          aria-controls={detailsId}
          onClick={() => setShowDetails((current) => !current)}
        >
          <ApprovalIcon name={showDetails ? "chevron-down" : "chevron-right"} />
          Details
        </button>
        <div className="sourcecado-approval-actions">
          <button
            type="button"
            data-decision="deny"
            aria-label="Deny"
            disabled={submitting}
            onClick={() => void decide(false)}
          >
            <ApprovalIcon name="x" />
            <span>Deny</span>
          </button>
          <button
            type="button"
            data-decision="allow"
            aria-label="Allow once"
            aria-keyshortcuts="Meta+Enter Control+Enter"
            disabled={submitting}
            onClick={() => void decide(true)}
          >
            <ApprovalIcon name="check" />
            <span>Allow once</span>
            <kbd aria-hidden="true">⌘⏎</kbd>
          </button>
          {alwaysOffered ? (
            <button
              type="button"
              data-decision="always"
              aria-label="Always allow this exact command"
              disabled={submitting}
              onClick={() => void decide(true, "always")}
            >
              <ApprovalIcon name="shield" />
              <span>Always allow this exact command</span>
            </button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
