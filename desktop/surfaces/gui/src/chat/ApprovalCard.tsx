import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { useEffect, useId, useRef, useState } from "react";

import { toolPresentation } from "./toolRegistry";
import { CalendarApprovalSummary } from "../generative/CalendarEventResult";
import { GmailDraftBody } from "../generative/GmailDraftResult";
import { sanitizeApolloNameMasks, withoutApolloNameMasks } from "../personName";

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

type GmailSendResource = {
  readonly to: string | null;
  readonly subject: string | null;
  readonly account: string | null;
};

type ShellCommandResource = {
  readonly executionTarget: "docker" | "host" | "unknown";
  readonly commandSummary: string;
  readonly commandDisplay: string;
  readonly environmentKeys: readonly string[];
  readonly cwd: string | null;
  readonly fingerprint: string | null;
  readonly unsandboxed: boolean;
  readonly permanentEligible: boolean;
};

function gmailSendResourceOf(
  part: ToolCallMessagePartProps,
): GmailSendResource | null {
  const raw = part.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  const resource = raw?.resource;
  if (!resource || typeof resource !== "object" || Array.isArray(resource)) {
    return null;
  }
  const record = resource as Record<string, unknown>;
  if (record.kind !== "gmail_draft") return null;
  const field = (key: string): string | null =>
    typeof record[key] === "string" ? (record[key] as string) : null;
  return {
    to: field("to"),
    subject: field("subject"),
    account: field("account"),
  };
}

function shellCommandResourceOf(
  part: ToolCallMessagePartProps,
): ShellCommandResource | null {
  const raw = part.providerMetadata?.sourcecado as
    | Record<string, unknown>
    | undefined;
  const resource = raw?.resource;
  if (!resource || typeof resource !== "object" || Array.isArray(resource)) {
    return null;
  }
  const record = resource as Record<string, unknown>;
  if (
    record.kind !== "shell_command" ||
    !["docker", "host", "unknown"].includes(String(record.execution_target)) ||
    typeof record.command_summary !== "string" ||
    typeof record.command_display !== "string" ||
    !Array.isArray(record.environment_keys) ||
    !record.environment_keys.every((key) => typeof key === "string") ||
    typeof record.unsandboxed !== "boolean" ||
    typeof record.permanent_eligible !== "boolean"
  ) {
    return null;
  }
  return {
    executionTarget: record.execution_target as "docker" | "host" | "unknown",
    commandSummary: record.command_summary,
    commandDisplay: record.command_display,
    environmentKeys: record.environment_keys as string[],
    cwd: typeof record.cwd === "string" ? record.cwd : null,
    fingerprint:
      typeof record.fingerprint === "string" ? record.fingerprint : null,
    unsandboxed: record.unsandboxed,
    permanentEligible: record.permanent_eligible === true,
  };
}

function knownOrUnknown(value: string | null, unknown: string): string {
  return value && value.trim() ? value.trim() : unknown;
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
  if (toolName === "gmail_send") {
    return [["Draft", value("draft_id")]].filter(
      (row): row is [string, string] => row[1] !== null,
    );
  }
  return [];
}

function scopeStatement(toolName: string): string {
  if (toolName === "gmail_send") {
    return "Scope: allow once. Sourcecado will send this email now.";
  }
  if (toolName === "gmail_draft" || toolName === "gmail_create_draft") {
    return "Scope: allow once. Sourcecado will not send email.";
  }
  if (toolName === "calendar_create" || toolName === "calendar_update") {
    return "Scope: allow once. This changes Google Calendar and will not send email.";
  }
  if (toolName === "shell_exec") {
    return "Scope: allow once. Only this exact command request will run.";
  }
  return "Scope: allow once.";
}

function displayedArguments(
  toolName: string,
  args: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> {
  if (toolName !== "apollo_enrich_contact") return args;
  return sanitizeApolloNameMasks(args) as Readonly<Record<string, unknown>>;
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
  readonly onDecision: (
    approved: boolean,
    scope?: "once" | "always",
  ) => Promise<"queued" | void> | "queued" | void;
}) {
  const audit = auditOf(part);
  const [submitting, setSubmitting] = useState(false);
  const [submitQueued, setSubmitQueued] = useState(false);
  const [submitOutcomeUnknown, setSubmitOutcomeUnknown] = useState(false);
  const [submitFailed, setSubmitFailed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [showReceipt, setShowReceipt] = useState(false);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const previousState = useRef<ApprovalState>(audit.approvalState);
  const detailsId = useId();
  const pending = audit.approvalState === "pending";
  const presentation = toolPresentation(part.toolName);
  const args = part.args as Readonly<Record<string, unknown>>;
  const safeArgs = displayedArguments(part.toolName, args);
  const fields = decisionFields(part.toolName, args);
  const gmailDraft =
    part.toolName === "gmail_draft" || part.toolName === "gmail_create_draft";
  const gmailSend = part.toolName === "gmail_send";
  const gmailSendResource = gmailSend ? gmailSendResourceOf(part) : null;
  const calendarWrite =
    part.toolName === "calendar_create" || part.toolName === "calendar_update";
  const gmailBody =
    gmailDraft && typeof args.body === "string" ? args.body : null;
  const shellCommand =
    part.toolName === "shell_exec" ? shellCommandResourceOf(part) : null;
  const approvalReason = part.approval?.reason
    ? part.toolName === "apollo_enrich_contact"
      ? withoutApolloNameMasks(part.approval.reason)
      : part.approval.reason
    : "This external action needs your approval.";

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

  // The sidecar itself gives up waiting for execution after ~60s
  // (desktop/coworker/inbox.py _DEFAULT_WAIT_TIMEOUT_SECONDS) and writes no
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
    <section
      className="sourcecado-approval-card"
      aria-busy={
        (submitting && !submitOutcomeUnknown && !submitQueued) || undefined
      }
    >
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
      {gmailSend ? (
        <div className="sourcecado-gmail-approval-preview">
          <p>
            Gmail ·{" "}
            {gmailSendResource
              ? knownOrUnknown(
                  gmailSendResource.account,
                  "Account could not be determined",
                )
              : "Connected Google account"}
          </p>
          <strong>Not yet sent</strong>
          {gmailSendResource ? (
            <dl>
              <div>
                <dt>To</dt>
                <dd>
                  {knownOrUnknown(
                    gmailSendResource.to,
                    "Recipient could not be determined",
                  )}
                </dd>
              </div>
              <div>
                <dt>Subject</dt>
                <dd>
                  {knownOrUnknown(
                    gmailSendResource.subject,
                    "Subject could not be determined",
                  )}
                </dd>
              </div>
            </dl>
          ) : (
            <p>
              Sourcecado can’t show the recipient or subject for this approval.
              Review the drafted email before choosing Allow once.
            </p>
          )}
        </div>
      ) : null}
      {part.toolName === "shell_exec" ? (
        <div className="sourcecado-shell-approval-summary">
          <strong>{shellCommand?.unsandboxed ? "Not sandboxed" : "Docker sandbox"}</strong>
          <dl>
            <div>
              <dt>Command</dt>
              <dd><code>{shellCommand?.commandDisplay ?? "Command details unavailable"}</code></dd>
            </div>
            <div>
              <dt>Working folder</dt>
              <dd>{shellCommand?.cwd ?? "Could not be resolved"}</dd>
            </div>
          </dl>
          {shellCommand?.environmentKeys.length ? (
            <p>Environment keys: {shellCommand.environmentKeys.join(", ")}</p>
          ) : (
            <p>No additional environment values.</p>
          )}
          {shellCommand?.unsandboxed ? (
            <p>This runs directly under your macOS account and can access everything that account can access.</p>
          ) : (
            <p>The real workspace is mounted read-write and container networking is unrestricted.</p>
          )}
        </div>
      ) : null}
      {part.toolName === "apollo_enrich_contact" ? (
        <p className="sourcecado-approval-credit-note">
          Apollo enrichment uses credits. No credit is spent until you choose Allow once.
        </p>
      ) : null}
      <p>{approvalReason}</p>
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
          {part.toolName === "shell_exec" ? (
            <p>
              Exact fingerprint: {shellCommand?.fingerprint?.slice(0, 16) ?? "unavailable"}
            </p>
          ) : (
            <pre>{JSON.stringify(safeArgs, null, 2)}</pre>
          )}
          <p>{scopeStatement(part.toolName)}</p>
        </div>
      ) : null}
      {submitFailed ? (
        <p role="alert">The decision couldn’t be saved. Try again.</p>
      ) : null}
      {submitOutcomeUnknown ? (
        <p>
          Outcome is unknown. Sourcecado didn’t confirm this decision before
          its wait ended. Verify the external resource before retrying.
        </p>
      ) : submitQueued ? (
        <p>
          Waiting for the connection. Your decision will send once
          Sourcecado reconnects.
        </p>
      ) : submitting ? (
        <p>Submitting decision…</p>
      ) : null}
      <div className="sourcecado-approval-actions">
        <button type="button" disabled={submitting} onClick={() => void decide(false)}>
          Deny
        </button>
        <button type="button" disabled={submitting} onClick={() => void decide(true)}>
          Allow once
        </button>
        {shellCommand?.unsandboxed &&
        shellCommand.fingerprint &&
        shellCommand.permanentEligible ? (
          <button
            type="button"
            disabled={submitting}
            onClick={() => void decide(true, "always")}
          >
            Always allow this exact command
          </button>
        ) : null}
      </div>
    </section>
  );
}
