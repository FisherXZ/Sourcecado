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
  searchPersonDriveEvidence,
  setPersonSequence,
  type DriveEvidenceCandidate,
  type PersonFile,
} from "./api";

const STATES = [
  { id: "open", label: "Open" },
  { id: "in_conversation", label: "In conversation" },
  { id: "done", label: "Done" },
] as const;

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
        <section className="person-summary-card">
          <h2>Knowledge gaps</h2>
          <p>{file.brief.missing.join(", ") || "None recorded"}</p>
        </section>
        <section className="person-summary-card">
          <h2>Sources</h2>
          <p>{file.brief.sources.join(", ") || "None recorded"}</p>
        </section>
      </div>

      <section className="person-section" aria-labelledby="person-learned-heading">
        <h2 id="person-learned-heading">Learned</h2>
        {file.brief.learned.length === 0 ? (
          <p className="person-empty">Nothing filed yet.</p>
        ) : (
          <ul>
            {file.brief.learned.map((line) => <li key={line}>{line}</li>)}
          </ul>
        )}
      </section>

      <OutreachPanel
        personId={personId}
        sessionId={file.sourcing_chat?.session_id ?? null}
        recipient={
          typeof file.person.email === "string" ? file.person.email : null
        }
      />

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
