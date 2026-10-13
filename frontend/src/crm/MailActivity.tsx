import { useState } from "react";
import type { FollowUp, MailAnalysisState, MailSyncState, PersonFile, PersonKnowledgeGap } from "../api";
import { MailFreshness } from "./MailFreshness";

const FILTERS = [{ id: "all", label: "All" }, { id: "inbound", label: "Incoming" }, { id: "outbound", label: "Outgoing" }] as const;

function gmailUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "mail.google.com" ? value : null;
  } catch { return null; }
}

export function MailActivity({ events, gaps, sync, analysis, followUp, replied }: {
  events: PersonFile["timeline"];
  gaps: PersonKnowledgeGap[];
  sync?: MailSyncState;
  analysis?: MailAnalysisState;
  followUp?: FollowUp;
  replied?: boolean;
}) {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const messages = events.flatMap(event => {
    const payload = event.payload;
    const direction = event.kind === "send" && payload.sent ? "outbound" : payload.direction;
    if (direction !== "inbound" && direction !== "outbound") return [];
    const source = payload.source_ref && typeof payload.source_ref === "object"
      ? payload.source_ref as Record<string, unknown> : {};
    const thread = String(source.thread_id ?? payload.thread_id ?? "unknown");
    const fallback = thread !== "unknown" ? `https://mail.google.com/mail/u/0/#all/${encodeURIComponent(thread)}` : null;
    // SQLite's historical receipt timestamps are UTC without an offset.
    const received = typeof payload.received_at === "string" ? payload.received_at : null;
    const recorded = event.created_at?.replace(" ", "T");
    const time = received ?? (recorded && !/[Zz]|[+-]\d{2}:\d{2}$/.test(recorded) ? `${recorded}Z` : recorded) ?? "";
    return [{ id: event.event_id, direction, time, stamp: Date.parse(time) || 0,
      from: String(payload.from ?? "Unknown sender"), to: String(payload.to ?? "Unknown recipient"),
      subject: String(payload.subject ?? "No subject"), snippet: String(payload.snippet ?? ""),
      thread, message: String(source.message_id ?? payload.message_id ?? ""),
      url: gmailUrl(source.url ?? fallback), approved: event.kind === "send", bodyState: payload.body_state }];
  }).sort((a, b) => b.stamp - a.stamp);
  const visible = messages.filter(message => filter === "all" || message.direction === filter);
  const unresolved = gaps.filter(gap => gap.fields?.kind === "unassigned_reply" && !gap.fields.resolved);

  return <section className="person-section person-mail-activity" aria-labelledby="person-mail-heading">
    <h2 id="person-mail-heading">Gmail activity</h2>
    <MailFreshness state={sync} analysis={analysis} />
    <p className="person-followup-line">
      {followUp?.needed ? followUp.reason === "reply_unanswered"
        ? "They replied and are waiting on a response." : "A message arrived that could not be tied to this person."
        : replied ? "The last message on this thread was ours." : "No reply yet."}
    </p>
    <div className="contacts-view-controls" role="group" aria-label="Activity direction">
      {FILTERS.map(option => <button key={option.id} type="button"
        aria-pressed={filter === option.id} onClick={() => setFilter(option.id)}>{option.label}</button>)}
    </div>
    {visible.length === 0 ? <p className="person-empty">
      {filter === "all" ? "No messages collected yet." : `No ${filter === "inbound" ? "incoming" : "outgoing"} messages collected.`}
    </p> : <div className="person-timeline">
      {visible.map(message => <article key={message.id} className="person-timeline-entry">
        <p className="person-timeline-meta">
          {message.direction === "inbound" ? "Incoming" : "Outgoing"} · Gmail · {message.time || "time unknown"}
          {message.direction === "outbound" ? ` · Sent in ${message.approved ? "Sourcecado" : "Gmail"}` : ""}
        </p>
        <p>{message.direction === "inbound" ? message.from : message.to} — {message.subject}</p>
        {message.snippet ? <p>{message.snippet}</p> : null}
        {message.bodyState === "missing" || message.bodyState === "partial" ? <p className="person-timeline-meta">Body unavailable or incomplete.</p> : null}
        <p className="person-timeline-meta">
          {message.url ? <a href={message.url} target="_blank" rel="noreferrer">Open Gmail thread {message.thread}</a> : `Gmail thread ${message.thread}`}
          {message.message ? ` · message ${message.message}` : null}
        </p>
      </article>)}
    </div>}
    {unresolved.length > 0 ? <div className="person-meeting-proposals">
      <h3>Unassigned messages</h3>
      {unresolved.map(gap => <article key={gap.id} className="person-timeline-entry">
        <p className="person-timeline-meta">Gmail thread {String(gap.fields?.thread_id ?? "unknown")} · {String(gap.fields?.received_at ?? "time unknown")}</p>
        <p>{String(gap.fields?.question ?? "Which person does this message belong to?")}</p>
        <p className="person-timeline-meta">Nothing was filed on this person. Reason: {String(gap.fields?.reason ?? "unknown")}.</p>
      </article>)}
    </div> : null}
  </section>;
}
