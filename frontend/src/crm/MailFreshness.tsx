import type { MailAnalysisState, MailSyncState } from "../api";

function checkedTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "time unknown" : date.toLocaleString();
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
