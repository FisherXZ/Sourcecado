import { useEffect, useState, type FormEvent } from "react";

import { OutreachPanel } from "./OutreachPanel";

import {
  attachPersonMeeting,
  attachPersonDriveEvidence,
  getPerson,
  openPersonSourcingChat,
  refreshPersonMeetings,
  rejectPersonMeeting,
  revertPerson,
  savePersonHandoff,
  searchPersonDriveEvidence,
  setPersonSequence,
  type BriefClaim,
  type BriefSourceRef,
  type DriveEvidenceCandidate,
  type PersonFile,
  type PersonKnowledgeGap,
} from "./api";

const STATES = [
  { id: "open", label: "Open" },
  { id: "in_conversation", label: "In conversation" },
  { id: "done", label: "Done" },
] as const;

const CLAIM_STATE_LABEL: Record<string, string> = {
  current: "Current",
  stale: "Stale",
  conflicting: "Conflicting",
  missing: "Missing",
};

const EVIDENCE_LABEL: Record<string, string> = {
  present: "Read",
  partial: "Partly read",
  unsupported: "Body unavailable",
  missing: "Not reachable",
  ambiguous: "Unattributed",
  expired: "Aged out",
  absent: "Not present",
};

const HANDOFF_FIELDS = [
  { key: "who", label: "Who this is" },
  { key: "wanted", label: "What we wanted" },
  { key: "happened", label: "What happened" },
  { key: "theyWant", label: "What they want" },
] as const;

const STALE_HANDOFF_LABELS: Record<string, string> = {
  who: "Who this is",
  wanted: "What we wanted",
  happened: "What happened",
  they_want: "What they want",
};

type HandoffDraft = Record<(typeof HANDOFF_FIELDS)[number]["key"], string>;

/** One brief claim: what it says, how far it holds, and what backs it. */
function Claim({ claim }: { claim: BriefClaim }) {
  return (
    <li className="person-claim">
      <p className="person-claim-line">
        <span className={`person-claim-state is-${claim.state}`}>
          {CLAIM_STATE_LABEL[claim.state] ?? claim.state}
        </span>
        <span>{claim.text}</span>
        {claim.truncated ? <span className="person-claim-flag">Truncated</span> : null}
      </p>
      <p className="person-claim-refs">
        {claim.source_refs.length > 0
          ? claim.source_refs.join(" · ")
          : "No source reference"}
      </p>
    </li>
  );
}

function ClaimList({ claims, empty }: { claims: BriefClaim[]; empty: string }) {
  if (claims.length === 0) return <p className="person-empty">{empty}</p>;
  return (
    <ul className="person-claims">
      {claims.map((claim) => (
        <Claim key={claim.id} claim={claim} />
      ))}
    </ul>
  );
}

function sourceNote(source: BriefSourceRef): string {
  const parts = [EVIDENCE_LABEL[source.evidence] ?? source.evidence];
  parts.push(source.fresh ? "fresh" : "stale");
  if (source.truncated) parts.push("truncated");
  return parts.join(" · ");
}

const FOLLOW_UP_LINE: Record<string, string> = {
  reply_unanswered: "They replied and are waiting on a response.",
  reply_needs_review: "A reply arrived that could not be tied to this person.",
};

type InboundReply = {
  eventId: string;
  from: string;
  subject: string;
  snippet: string;
  receivedAt: string | null;
  threadId: string | null;
  messageId: string | null;
  url: string | null;
};

/** The inbound replies filed on this person, newest last, from the ledger. */
function inboundReplies(file: PersonFile): InboundReply[] {
  return file.timeline
    .filter((event) => event.payload?.direction === "inbound")
    .map((event) => {
      const source = (event.payload.source_ref ?? {}) as Record<string, unknown>;
      return {
        eventId: event.event_id,
        from: String(event.payload.from ?? "Unknown sender"),
        subject: String(event.payload.subject ?? "No subject"),
        snippet: String(event.payload.snippet ?? ""),
        receivedAt:
          typeof event.payload.received_at === "string" ? event.payload.received_at : null,
        threadId: typeof source.thread_id === "string" ? source.thread_id : null,
        messageId: typeof source.message_id === "string" ? source.message_id : null,
        url: typeof source.url === "string" ? source.url : null,
      };
    });
}

function unassignedReplyGaps(file: PersonFile): PersonKnowledgeGap[] {
  return (file.person.knowledge_gaps ?? []).filter(
    (gap) => gap.fields?.kind === "unassigned_reply",
  );
}

export function PersonFileView({ personId }: { personId: string }) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [moving, setMoving] = useState<string | null>(null);
  const [moveFailed, setMoveFailed] = useState(false);
  const [openingChat, setOpeningChat] = useState(false);
  const [chatFailed, setChatFailed] = useState(false);
  const [meetingBusy, setMeetingBusy] = useState<string | null>(null);
  const [meetingStatus, setMeetingStatus] = useState<string | null>(null);
  const [driveQuery, setDriveQuery] = useState("");
  const [driveResults, setDriveResults] = useState<DriveEvidenceCandidate[] | null>(null);
  const [driveSearching, setDriveSearching] = useState(false);
  const [driveBusy, setDriveBusy] = useState<string | null>(null);
  const [driveStatus, setDriveStatus] = useState<string | null>(null);
  const [handoff, setHandoff] = useState<HandoffDraft | null>(null);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const [handoffStatus, setHandoffStatus] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setFailed(false);
    setFile(null);
    getPerson(personId).then(
      (next) => {
        if (active) setFile(next);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt, personId]);

  // The form starts from what the brief already says: a saved handoff when
  // the director wrote one, otherwise the draft generated from the claims.
  useEffect(() => {
    if (!file) return;
    const stored = file.brief.handoff;
    setHandoff({
      who: stored.who,
      wanted: stored.wanted,
      happened: stored.happened,
      theyWant: stored.they_want,
    });
  }, [file]);

  useEffect(() => {
    const refresh = () => setAttempt((value) => value + 1);
    window.addEventListener("sourcecado:board-changed", refresh);
    return () => window.removeEventListener("sourcecado:board-changed", refresh);
  }, []);

  async function move(state: string) {
    if (moving) return;
    setMoving(state);
    setMoveFailed(false);
    try {
      await setPersonSequence(personId, state);
      setFile(await getPerson(personId));
    } catch {
      setMoveFailed(true);
    } finally {
      setMoving(null);
    }
  }

  async function openSourcingChat() {
    if (openingChat || !file) return;
    const version = file.person.version;
    if (typeof version !== "number" || !Number.isInteger(version)) {
      setChatFailed(true);
      return;
    }
    setOpeningChat(true);
    setChatFailed(false);
    try {
      const result = await openPersonSourcingChat(personId, version);
      if (result.active_person.person_id !== personId) {
        throw new Error("person binding mismatch");
      }
      window.location.hash = `#/chat/${encodeURIComponent(result.session.id)}/person/${encodeURIComponent(personId)}`;
    } catch {
      setChatFailed(true);
    } finally {
      setOpeningChat(false);
    }
  }

  async function refreshMeetings() {
    if (meetingBusy) return;
    setMeetingBusy("refresh");
    setMeetingStatus(null);
    try {
      const result = await refreshPersonMeetings(personId);
      const calendar = result.sources.calendar?.status;
      const granola = result.sources.granola?.status;
      if (calendar === "ok" && granola === "ok") {
        setMeetingStatus("Calendar and Granola refreshed.");
      } else if (calendar === "failed" && granola === "ok") {
        setMeetingStatus("Granola refreshed; Calendar is unavailable.");
      } else if (calendar === "ok" && granola === "failed") {
        setMeetingStatus("Calendar refreshed; Granola is unavailable.");
      } else {
        setMeetingStatus("Meeting evidence is unavailable from both sources.");
      }
      setFile(await getPerson(personId));
    } catch {
      setMeetingStatus("Couldn’t refresh meeting evidence. Try again.");
    } finally {
      setMeetingBusy(null);
    }
  }

  async function reviewMeeting(evidenceId: string, action: "attach" | "reject") {
    if (meetingBusy) return;
    setMeetingBusy(`${action}:${evidenceId}`);
    setMeetingStatus(null);
    try {
      if (action === "attach") {
        await attachPersonMeeting(personId, evidenceId);
      } else {
        await rejectPersonMeeting(personId, evidenceId);
      }
      setFile(await getPerson(personId));
    } catch {
      setMeetingStatus(`Couldn’t ${action} this meeting. Refresh and try again.`);
    } finally {
      setMeetingBusy(null);
    }
  }

  async function searchDrive(event: FormEvent) {
    event.preventDefault();
    if (driveSearching || !driveQuery.trim()) return;
    setDriveSearching(true);
    setDriveStatus(null);
    try {
      const result = await searchPersonDriveEvidence(personId, driveQuery.trim());
      setDriveResults(result.files);
    } catch {
      setDriveResults(null);
      setDriveStatus("Couldn’t search Drive. Try again.");
    } finally {
      setDriveSearching(false);
    }
  }

  async function attachDrive(candidate: DriveEvidenceCandidate) {
    if (driveBusy) return;
    setDriveBusy(candidate.id);
    setDriveStatus(null);
    try {
      await attachPersonDriveEvidence(personId, {
        kind: "search_result",
        fileId: candidate.id,
      });
      setFile(await getPerson(personId));
      setDriveStatus(`Attached “${candidate.name ?? "Drive file"}” to this person.`);
    } catch {
      setDriveStatus("Couldn’t attach this Drive result. Try again.");
    } finally {
      setDriveBusy(null);
    }
  }

  async function saveHandoff(event: FormEvent) {
    event.preventDefault();
    if (!file || !handoff || handoffBusy) return;
    setHandoffBusy(true);
    setHandoffStatus(null);
    try {
      const result = await savePersonHandoff(personId, {
        ...handoff,
        expectedVersion: file.brief.person_version,
      });
      setFile(await getPerson(personId));
      setHandoffStatus(
        result.saved
          ? "Saved as a new person-file version."
          : "No handoff changes to save.",
      );
    } catch {
      setHandoffStatus(
        "Couldn\u2019t save the handoff. Refresh the person file and try again.",
      );
    } finally {
      setHandoffBusy(false);
    }
  }

  if (failed) {
    return (
      <main className="route-page person-page">
        <h1>Person</h1>
        <section className="route-error" role="alert">
          <p>Couldn’t load this person file.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
          <a href="#/board">Back to Board</a>
        </section>
      </main>
    );
  }

  if (!file) {
    return (
      <main className="route-page person-page" aria-busy="true">
        <h1>Person</h1>
        <p role="status">Loading person file…</p>
      </main>
    );
  }

  const driveSources = (file.person.sources ?? []).filter(
    (source) => source.fields?.provider === "Google Drive",
  );
  const replies = inboundReplies(file);
  const replyGaps = unassignedReplyGaps(file);

  return (
    <main className="route-page person-page">
      <header className="person-page-header">
        <a className="person-back-link" href="#/board">← Board</a>
        <p className="eyebrow">Person file</p>
        <h1>{file.brief.who || "Person"}</h1>
        {file.brief.why ? <p>{file.brief.why}</p> : null}
        <div className="person-chat-action">
          <button
            type="button"
            disabled={openingChat}
            onClick={() => void openSourcingChat()}
          >
            {openingChat
              ? "Opening sourcing chat…"
              : file.sourcing_chat
                ? "Open sourcing chat"
                : "Create sourcing chat"}
          </button>
          {chatFailed ? (
            <p role="alert">
              Couldn’t open this person’s sourcing chat. Refresh the person file and try again.
            </p>
          ) : null}
        </div>
      </header>

      {file.brief.partial ? (
        <p className="person-partial" role="status">
          Partial brief. {file.brief.partial_sources.join(", ")} could not be
          refreshed, so everything below is what we already had.
        </p>
      ) : null}

      <section className="person-sequence" aria-labelledby="person-sequence-heading">
        <h2 id="person-sequence-heading">Sequence state</h2>
        <div className="person-sequence-actions">
          {STATES.map((state) => {
            const active = file.person.sequence_state === state.id;
            return (
              <button
                key={state.id}
                type="button"
                className={`person-sequence-action ${active ? "is-active" : ""}`}
                aria-pressed={active}
                disabled={moving !== null}
                onClick={() => void move(state.id)}
              >
                {moving === state.id ? "Updating…" : state.label}
              </button>
            );
          })}
        </div>
        {moveFailed ? (
          <p className="person-sequence-error" role="alert">
            Couldn’t update the sequence state. Try again.
          </p>
        ) : null}
      </section>

      <div className="person-summary-grid">
        <section className="person-summary-card" aria-labelledby="person-outcome-heading">
          <h2 id="person-outcome-heading">Outcome</h2>
          <p>{file.brief.outcome?.text ?? "None recorded"}</p>
        </section>
        <section className="person-summary-card" aria-labelledby="person-contact-heading">
          <h2 id="person-contact-heading">Last contact</h2>
          <p>
            {file.brief.last_contact.direction
              ? `${file.brief.last_contact.direction} · ${file.brief.last_contact.at ?? "date not recorded"}`
              : "No outreach sent and no reply received"}
          </p>
          {file.brief.last_contact.follow_up.needed ? (
            <p className="person-attention">Needs follow-up.</p>
          ) : null}
        </section>
        <section className="person-summary-card" aria-labelledby="person-wants-heading">
          <h2 id="person-wants-heading">What they want</h2>
          <p>{file.brief.wants.text || "Not recorded"}</p>
        </section>
        <section className="person-summary-card" aria-labelledby="person-sources-heading">
          <h2 id="person-sources-heading">Sources</h2>
          <p>{file.brief.sources.join(", ") || "None recorded"}</p>
          {file.brief.restricted_source_count > 0 ? (
            <p className="person-attention">
              {file.brief.restricted_source_count} restricted source withheld from
              this brief.
            </p>
          ) : null}
        </section>
      </div>

      <section className="person-section" aria-labelledby="person-handoff-heading">
        <h2 id="person-handoff-heading">Successor handoff</h2>
        <p>
          {file.brief.handoff.generated
            ? "Drafted from the claims below. Review each field, then save it as a version."
            : file.brief.handoff.freshness_unknown
              ? "Saved handoff. Its saved version was not recorded; review every field."
              : `Saved at version ${file.brief.handoff.version}.`}
        </p>
        {file.brief.handoff.stale ? (
          <p className="person-attention">
            Review outdated fields:{" "}
            {file.brief.handoff.stale_fields
              .map((key) => STALE_HANDOFF_LABELS[key])
              .filter((label): label is string => Boolean(label))
              .map((label, index, labels) =>
                index === labels.length - 1 && labels.length > 1
                  ? `and ${label}`
                  : label,
              )
              .join(file.brief.handoff.stale_fields.length > 2 ? ", " : " ")}.
          </p>
        ) : null}
        <form className="person-handoff" onSubmit={(event) => void saveHandoff(event)}>
          {HANDOFF_FIELDS.map((field) => (
            <p key={field.key}>
              <label htmlFor={`person-handoff-${field.key}`}>{field.label}</label>
              <textarea
                id={`person-handoff-${field.key}`}
                rows={2}
                value={handoff?.[field.key] ?? ""}
                onChange={(event) =>
                  setHandoff((current) =>
                    current === null
                      ? current
                      : { ...current, [field.key]: event.target.value },
                  )
                }
              />
            </p>
          ))}
          <button type="submit" disabled={handoffBusy}>
            {handoffBusy ? "Saving\u2026" : "Save handoff version"}
          </button>
        </form>
        {handoffStatus ? <p role="status">{handoffStatus}</p> : null}
      </section>

      <section className="person-section" aria-labelledby="person-learned-heading">
        <h2 id="person-learned-heading">Learned</h2>
        <ClaimList claims={file.brief.evidence} empty="Nothing filed yet." />
        {file.brief.omitted > 0 ? (
          <p className="person-empty">
            {file.brief.omitted} older record(s) are not in this brief. The full
            timeline is below.
          </p>
        ) : null}
      </section>

      {file.brief.conflicts.length > 0 ? (
        <section className="person-section" aria-labelledby="person-conflicts-heading">
          <h2 id="person-conflicts-heading">Conflicts</h2>
          <ClaimList claims={file.brief.conflicts} empty="None." />
        </section>
      ) : null}

      <section className="person-section" aria-labelledby="person-gaps-heading">
        <h2 id="person-gaps-heading">Knowledge gaps</h2>
        <ClaimList claims={file.brief.gaps} empty="Nothing is open." />
      </section>

      <section className="person-section" aria-labelledby="person-artifacts-heading">
        <h2 id="person-artifacts-heading">Artifacts</h2>
        <ClaimList claims={file.brief.artifacts} empty="Nothing filed yet." />
      </section>

      <OutreachPanel
        personId={personId}
        sessionId={file.sourcing_chat?.session_id ?? null}
        recipient={
          typeof file.person.email === "string" ? file.person.email : null
        }
      />

      <section className="person-section" aria-labelledby="person-replies-heading">
        <h2 id="person-replies-heading">Replies</h2>
        <p className="person-followup-line">
          {file.person.follow_up?.needed
            ? (FOLLOW_UP_LINE[file.person.follow_up.reason ?? ""] ??
              "This person needs attention.")
            : file.person.replied
              ? "The last message on this thread was ours."
              : "No reply yet."}
        </p>
        {replies.length === 0 ? (
          <p className="person-empty">No inbound reply filed yet.</p>
        ) : (
          <div className="person-timeline">
            {replies.map((reply) => (
              <article key={reply.eventId} className="person-timeline-entry">
                <p className="person-timeline-meta">
                  Gmail · {reply.receivedAt ?? "time unknown"}
                </p>
                <p>
                  {reply.from} — {reply.subject}
                </p>
                {reply.snippet ? <p>{reply.snippet}</p> : null}
                <p className="person-timeline-meta">
                  {reply.url ? (
                    <a href={reply.url} target="_blank" rel="noreferrer">
                      Open Gmail thread {reply.threadId}
                    </a>
                  ) : (
                    `Gmail thread ${reply.threadId ?? "unknown"}`
                  )}
                  {reply.messageId ? ` · message ${reply.messageId}` : null}
                </p>
              </article>
            ))}
          </div>
        )}
        {replyGaps.length > 0 ? (
          <div className="person-meeting-proposals">
            <h3>Unassigned replies</h3>
            {replyGaps.map((gap) => (
              <article key={gap.id} className="person-timeline-entry">
                <p className="person-timeline-meta">
                  Gmail thread {String(gap.fields?.thread_id ?? "unknown")} ·{" "}
                  {String(gap.fields?.received_at ?? "time unknown")}
                </p>
                <p>{String(gap.fields?.question ?? "Which person does this reply belong to?")}</p>
                <p className="person-timeline-meta">
                  Nothing was filed on this person. Reason:{" "}
                  {String(gap.fields?.reason ?? "unknown")}.
                </p>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <section className="person-section" aria-labelledby="person-meetings-heading">
        <div className="person-section-heading-row">
          <h2 id="person-meetings-heading">Meeting evidence</h2>
          <button
            type="button"
            disabled={meetingBusy !== null}
            onClick={() => void refreshMeetings()}
          >
            {meetingBusy === "refresh" ? "Refreshing…" : "Refresh meeting evidence"}
          </button>
        </div>
        {meetingStatus ? <p role="status">{meetingStatus}</p> : null}
        {(file.meeting_evidence?.attached ?? []).length === 0 ? (
          <p className="person-empty">No attached meetings yet.</p>
        ) : (
          <div className="person-timeline">
            {(file.meeting_evidence?.attached ?? []).map((meeting) => (
              <article key={meeting.evidence_id} className="person-timeline-entry">
                <p className="person-timeline-meta">
                  {meeting.source_ref.provider} · attached evidence
                </p>
                <p>{meeting.title}</p>
                {meeting.starts_at ? <p>{meeting.starts_at}</p> : null}
                <p>{meeting.notes ? "Notes attached (untrusted evidence)." : "Meeting notes missing."}</p>
              </article>
            ))}
          </div>
        )}
        {(file.meeting_evidence?.proposed ?? []).length > 0 ? (
          <div className="person-meeting-proposals">
            <h3>Needs review</h3>
            {(file.meeting_evidence?.proposed ?? []).map((meeting) => (
              <article key={meeting.evidence_id} className="person-timeline-entry">
                <p className="person-timeline-meta">{meeting.source_ref.provider}</p>
                <p>{meeting.title}</p>
                <p>
                  {meeting.match_reason === "name_only"
                    ? "Name-only match — review required"
                    : "Multiple or conflicting matches — review required"}
                </p>
                <div className="person-meeting-actions">
                  <button
                    type="button"
                    disabled={meetingBusy !== null}
                    aria-label={`Attach ${meeting.title}`}
                    onClick={() => void reviewMeeting(meeting.evidence_id, "attach")}
                  >
                    Attach
                  </button>
                  <button
                    type="button"
                    disabled={meetingBusy !== null}
                    aria-label={`Reject ${meeting.title}`}
                    onClick={() => void reviewMeeting(meeting.evidence_id, "reject")}
                  >
                    Reject
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <section className="person-section" aria-labelledby="person-drive-evidence-heading">
        <h2 id="person-drive-evidence-heading">Drive evidence</h2>
        {driveSources.length === 0 ? (
          <p className="person-empty">No Drive evidence attached yet.</p>
        ) : (
          <div className="person-timeline">
            {driveSources.map((source) => (
              <article key={source.id} className="person-timeline-entry">
                <p className="person-timeline-meta">
                  Google Drive · {String(source.fields?.extraction_status ?? "metadata_only")}
                </p>
                <p>{String(source.fields?.title ?? "Drive file")}</p>
                {source.fields?.out_of_scope ? (
                  <p role="status">Outside the browsed folder — review before relying on it.</p>
                ) : null}
                {source.fields?.sensitivity === "sensitive" ? (
                  <p role="status">Flagged sensitive — review before sharing.</p>
                ) : null}
              </article>
            ))}
          </div>
        )}

        <form className="person-drive-search" onSubmit={(event) => void searchDrive(event)}>
          <label htmlFor="person-drive-query">Search Drive</label>
          <input
            id="person-drive-query"
            type="text"
            value={driveQuery}
            onChange={(event) => setDriveQuery(event.target.value)}
            placeholder="File name or contents"
          />
          <button type="submit" disabled={driveSearching || !driveQuery.trim()}>
            {driveSearching ? "Searching…" : "Search Drive"}
          </button>
        </form>
        {driveStatus ? <p role="status">{driveStatus}</p> : null}
        {driveResults !== null && driveResults.length === 0 ? (
          <p className="person-empty">No Drive results for that search.</p>
        ) : null}
        {driveResults !== null && driveResults.length > 0 ? (
          <div className="person-timeline">
            {driveResults.map((candidate) => (
              <article key={candidate.id} className="person-timeline-entry">
                <p className="person-timeline-meta">{candidate.mimeType ?? "Drive file"}</p>
                <p>{candidate.name ?? "Untitled Drive file"}</p>
                <div className="person-drive-actions">
                  <button
                    type="button"
                    disabled={driveBusy !== null}
                    aria-label={`Attach ${candidate.name ?? "Drive file"}`}
                    onClick={() => void attachDrive(candidate)}
                  >
                    {driveBusy === candidate.id ? "Attaching…" : "Attach to person"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <section className="person-section" aria-labelledby="person-source-refs-heading">
        <h2 id="person-source-refs-heading">Source references</h2>
        {file.brief.source_refs.length === 0 ? (
          <p className="person-empty">No sources recorded.</p>
        ) : (
          <ul className="person-claims">
            {file.brief.source_refs.map((source) => (
              <li key={source.id} className="person-claim">
                <p className="person-claim-line">
                  <span
                    className={`person-claim-state is-${source.fresh ? "current" : "stale"}`}
                  >
                    {source.provider}
                  </span>
                  <span>{source.title ?? source.locator ?? source.id}</span>
                </p>
                <p className="person-claim-refs">
                  {source.id} · {sourceNote(source)} · seen {source.observed_at}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {file.versions && file.versions.length > 0 ? (
        <section className="person-section" aria-labelledby="person-history-heading">
          <h2 id="person-history-heading">Version history</h2>
          <p>
            {typeof file.person.version === "number"
              ? `Current version ${file.person.version}`
              : "Prior versions"}
          </p>
          <ol>
            {file.versions.map((entry) => {
              const current = file.person.version;
              const canRevert =
                typeof current === "number" && entry.version < current;
              return (
                <li key={entry.version}>
                  <span>Version {entry.version}</span>
                  {canRevert ? (
                    <button
                      type="button"
                      onClick={() =>
                        void revertPerson(personId, {
                          toVersion: entry.version,
                          expectedVersion: current,
                          rationaleSummary: `Restore version ${entry.version} from person history.`,
                        }).then(() => setAttempt((value) => value + 1))
                      }
                    >
                      Revert to version {entry.version}
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      <section className="person-section" aria-labelledby="person-timeline-heading">
        <h2 id="person-timeline-heading">Timeline</h2>
        {file.timeline.length === 0 ? (
          <p className="person-empty">No sourcing activity yet.</p>
        ) : (
          <div className="person-timeline">
            {file.timeline.map((event) => (
              <article key={event.event_id} className="person-timeline-entry">
                <p className="person-timeline-meta">{event.source} · {event.kind}</p>
                <p>{event.summary}</p>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
