import type { ReactNode } from "react";

import type { MailAnalysisState, MailSyncState } from "../api";
import { Glyph } from "../generative/ResultCard";

function checkedTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "time unknown" : date.toLocaleString();
}

/** Time of day for a check made today, otherwise the day. */
function shortCheckedTime(date: Date): string {
  return date.toDateString() === new Date().toDateString()
    ? date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * One chip for Gmail freshness on Contacts. A failure or an unfinished
 * collection wins over the last good check, so the chip never says two
 * contradicting things. The full detail is in the tooltip and for screen readers.
 */
export function MailStatusChip({ state }: { state?: MailSyncState }) {
  const last = state?.last_success_at ? new Date(state.last_success_at) : null;
  const checked = last && !Number.isNaN(last.getTime()) ? last : null;
  const lastLine = checked
    ? `Last successful check: ${checked.toLocaleString()}.`
    : "No successful check yet.";
  const bodies = state?.warning === "body_unavailable"
    ? " Some message bodies are unavailable or incomplete." : "";
  const chip: { warn: boolean; glyph: "failed" | "partial" | "mail"; label: ReactNode; detail: string } =
    state?.status === "failed"
      ? { warn: true, glyph: "failed", label: "Gmail check failed", detail: `${lastLine} Saved activity remains available.` }
      : state?.status === "partial"
        ? { warn: true, glyph: "partial", label: "Gmail unfinished", detail: `Collection is unfinished. Refresh again to continue. ${lastLine}` }
        : checked
          ? { warn: false, glyph: "mail", label: <>Gmail · <time dateTime={state?.last_success_at ?? ""}>{shortCheckedTime(checked)}</time></>, detail: lastLine }
          : { warn: false, glyph: "mail", label: "Gmail not checked", detail: "Gmail has not been checked yet." };
  return <span className={`contacts-mail-chip${chip.warn ? " is-warn" : ""}`} title={chip.detail + bodies}>
    <Glyph name={chip.glyph} />
    {chip.label}
    <span className="sr-only"> {chip.detail}{bodies}</span>
  </span>;
}

/** Collection and interpretation are separate facts, even while Gmail is offline. */
export function MailFreshness({ state, analysis }: {
  state?: MailSyncState;
  analysis?: MailAnalysisState;
}) {
  return <div className="mail-freshness">
    <p className="person-timeline-meta">
      {state?.last_success_at
        ? <>Last successful Gmail check: <time dateTime={state.last_success_at}>{checkedTime(state.last_success_at)}</time>.</>
        : "Gmail has not been checked yet."}
    </p>
    {state?.status === "failed" ? <p role="alert">Gmail check failed. Saved activity remains available.</p> : null}
    {state?.status === "partial" ? <p role="status">Gmail collection is unfinished. Refresh again to continue.</p> : null}
    {state?.warning === "body_unavailable" ? <p>Some message bodies are unavailable or incomplete. Their previews are not complete evidence.</p> : null}
    {analysis ? <p className="person-timeline-meta">
      {analysis.status === "failed" ? "Conversation analysis failed. Saved work is kept; retry when ready."
        : analysis.status === "retrying" ? "Conversation analysis is waiting to retry. Saved work is kept."
        : analysis.status === "pending" ? "Analysis pending: collected mail has not been analyzed."
        : analysis.status === "current" ? "Analysis is current." : "Mail has not been analyzed."}
    </p> : null}
  </div>;
}
