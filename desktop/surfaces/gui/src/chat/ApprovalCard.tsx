import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { useEffect, useId, useRef, useState } from "react";

import { toolPresentation } from "./toolRegistry";
import { CalendarApprovalSummary } from "../generative/CalendarEventResult";
import { GmailDraftBody } from "../generative/GmailDraftResult";

type ApprovalState =
  | "pending"
  | "submitting"
  | "allowed"
  | "denied"
  | "expired"
  | "cancelled"
  | "failed-submit"
  | "resolved-elsewhere";

type ApprovalAudit = {
  readonly approvalState: ApprovalState;
  readonly actor: string | null;
  readonly requestedAt: string | null;
  readonly resolvedAt: string | null;
  readonly scope: string | null;
  readonly executionStatus: string | null;
  readonly executionError: string | null;
};

function auditOf(part: ToolCallMessagePartProps): ApprovalAudit {
  const raw = part.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  const standardState: ApprovalState = part.approval?.resolution
    ? part.approval.resolution
    : part.approval?.approved === true
      ? "allowed"
      : part.approval?.approved === false
        ? "denied"
        : "pending";
  return {
    approvalState:
      typeof raw?.approvalState === "string"
        ? (raw.approvalState as ApprovalState)
        : standardState,
    actor: typeof raw?.actor === "string" ? raw.actor : null,
    requestedAt:
      typeof raw?.requestedAt === "string" ? raw.requestedAt : null,
    resolvedAt: typeof raw?.resolvedAt === "string" ? raw.resolvedAt : null,
    scope: typeof raw?.scope === "string" ? raw.scope : null,
    executionStatus:
      typeof raw?.executionStatus === "string" ? raw.executionStatus : null,
    executionError:
      typeof raw?.executionError === "string" ? raw.executionError : null,
  };
}

function decisionFields(
  toolName: string,
  args: Readonly<Record<string, unknown>>,
): readonly [string, string][] {
  const value = (key: string) =>
    typeof args[key] === "string" && args[key] ? String(args[key]) : null;
  if (toolName === "gmail_draft" || toolName === "gmail_create_draft") {
    return [
      ["To", value("to")],
      ["Subject", value("subject")],
    ].filter((row): row is [string, string] => row[1] !== null);
  }
  if (toolName === "calendar_create" || toolName === "calendar_update") {
    return [
      ["Event", value("title") ?? value("summary")],
      ["Starts", value("start") ?? value("start_time")],
    ].filter((row): row is [string, string] => row[1] !== null);
  }
  if (toolName === "apollo_enrich_contact") {
    return [
      ["Contact", value("name") ?? value("email")],
      ["Company", value("company") ?? value("domain")],
    ].filter((row): row is [string, string] => row[1] !== null);
  }
  return [];
}

function resolvedLabel(state: ApprovalState): string {
  switch (state) {
    case "allowed":
      return "Allowed";
    case "denied":
      return "Denied";
    case "expired":
      return "Expired";
    case "cancelled":
      return "Cancelled";
    case "resolved-elsewhere":
      return "Resolved elsewhere";
    default:
      return "Pending";
  }
}

export function ApprovalCard({
  part,
  onDecision,
}: {
  readonly part: ToolCallMessagePartProps;
  readonly onDecision: (approved: boolean) => Promise<void> | void;
}) {
  const audit = auditOf(part);
  const [submitting, setSubmitting] = useState(false);
  const [submitFailed, setSubmitFailed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [showReceipt, setShowReceipt] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousState = useRef<ApprovalState>(audit.approvalState);
  const detailsId = useId();
  const pending = audit.approvalState === "pending";
  const presentation = toolPresentation(part.toolName);
  const args = part.args as Readonly<Record<string, unknown>>;
  const fields = decisionFields(part.toolName, args);
  const gmailDraft =
    part.toolName === "gmail_draft" || part.toolName === "gmail_create_draft";
  const calendarWrite =
    part.toolName === "calendar_create" || part.toolName === "calendar_update";
  const gmailBody =
    gmailDraft && typeof args.body === "string" ? args.body : null;

  useEffect(() => {
    if (pending) headingRef.current?.focus();
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

  async function decide(approved: boolean) {
    setSubmitting(true);
    setSubmitFailed(false);
    try {
      await onDecision(approved);
    } catch {
      setSubmitting(false);
      setSubmitFailed(true);
    }
  }

  if (!pending) {
    const outcomeUnknown = audit.executionStatus === "interrupted";
    const label = outcomeUnknown
      ? "Outcome unknown"
      : resolvedLabel(audit.approvalState);
    return (
      <section className="sourcecado-approval-receipt">
        <button
          type="button"
          aria-expanded={showReceipt}
          aria-controls={detailsId}
          onClick={() => setShowReceipt((current) => !current)}
        >
          {presentation.label} · {label}
        </button>
        {showReceipt ? (
          <div id={detailsId}>
            <p>
              {label}
              {audit.actor ? ` by ${audit.actor}` : ""}
            </p>
            <p>Scope: {audit.scope ?? "once"}</p>
            {outcomeUnknown ? (
              <p>
                {audit.executionError ??
                  "Outcome is unknown after Sourcecado restarted. Verify the external resource before retrying."}
              </p>
            ) : (
              <p>Execution {audit.executionStatus ?? "not recorded"}</p>
            )}
            {audit.resolvedAt ? <time dateTime={audit.resolvedAt}>{audit.resolvedAt}</time> : null}
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <section className="sourcecado-approval-card" aria-busy={submitting || undefined}>
      <h2 ref={headingRef} tabIndex={-1}>
        {presentation.label}
      </h2>
      {calendarWrite ? (
        <CalendarApprovalSummary
          args={args}
          toolName={part.toolName as "calendar_create" | "calendar_update"}
        />
      ) : null}
      {!calendarWrite && fields.length > 0 ? (
        <dl>
          {fields.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {gmailDraft ? (
        <div className="sourcecado-gmail-approval-preview">
          <p>Gmail draft · Connected Google account</p>
          <strong>Not sent</strong>
          {gmailBody ? <GmailDraftBody body={gmailBody} /> : null}
        </div>
      ) : null}
      {part.toolName === "apollo_enrich_contact" ? (
        <p className="sourcecado-approval-credit-note">
          Apollo enrichment uses credits. No credit is spent until you choose Allow once.
        </p>
      ) : null}
      <p>{part.approval?.reason ?? "This external action needs your approval."}</p>
      <button
        type="button"
        aria-expanded={showDetails}
        aria-controls={detailsId}
        onClick={() => setShowDetails((current) => !current)}
      >
        Review full request and policy
      </button>
      {showDetails ? (
        <div id={detailsId} className="sourcecado-approval-details">
          <pre>{JSON.stringify(args, null, 2)}</pre>
          <p>Scope: allow once. Sourcecado will not send email.</p>
        </div>
      ) : null}
      {submitFailed ? (
        <p role="alert">The decision couldn’t be saved. Try again.</p>
      ) : null}
      {submitting ? <p>Submitting decision…</p> : null}
      <div className="sourcecado-approval-actions">
        <button type="button" disabled={submitting} onClick={() => void decide(false)}>
          Deny
        </button>
        <button type="button" disabled={submitting} onClick={() => void decide(true)}>
          Allow once
        </button>
      </div>
    </section>
  );
}
