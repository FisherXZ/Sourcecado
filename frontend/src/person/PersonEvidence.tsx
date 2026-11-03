import { useState, type FormEvent } from "react";

import { Icon } from "../icons";
import {
  attachPersonDriveEvidence,
  attachPersonMeeting,
  refreshPersonMeetings,
  rejectPersonMeeting,
  searchPersonDriveEvidence,
  type DriveEvidenceCandidate,
  type PersonFile,
} from "../api";

function when(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/** Calendar and Granola meetings: attached ones, and uncertain matches to review. */
export function MeetingEvidence({
  personId,
  evidence,
  onChanged,
}: {
  personId: string;
  evidence: PersonFile["meeting_evidence"];
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const attached = evidence?.attached ?? [];
  const proposed = evidence?.proposed ?? [];

  async function refresh() {
    if (busy) return;
    setBusy("refresh");
    setStatus(null);
    try {
      const result = await refreshPersonMeetings(personId);
      const calendar = result.sources.calendar?.status;
      const granola = result.sources.granola?.status;
      if (calendar === "ok" && granola === "ok") {
        setStatus("Calendar and Granola refreshed.");
      } else if (calendar === "failed" && granola === "ok") {
        setStatus("Granola refreshed; Calendar is unavailable.");
      } else if (calendar === "ok" && granola === "failed") {
        setStatus("Calendar refreshed; Granola is unavailable.");
      } else {
        setStatus("Meeting evidence is unavailable from both sources.");
      }
      await onChanged();
    } catch {
      setStatus("Couldn’t refresh meeting evidence. Try again.");
    } finally {
      setBusy(null);
    }
  }

  async function review(evidenceId: string, action: "attach" | "reject") {
    if (busy) return;
    setBusy(`${action}:${evidenceId}`);
    setStatus(null);
    try {
      if (action === "attach") {
        await attachPersonMeeting(personId, evidenceId);
      } else {
        await rejectPersonMeeting(personId, evidenceId);
      }
      await onChanged();
    } catch {
      setStatus(`Couldn’t ${action} this meeting. Refresh and try again.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="person-section" aria-labelledby="person-meetings-heading">
      <div className="person-section-heading-row">
        <h2 id="person-meetings-heading">Meeting evidence</h2>
        <button
          type="button"
          className="pf-btn"
          disabled={busy !== null}
          onClick={() => void refresh()}
        >
          <Icon name="refresh" />
          {busy === "refresh" ? "Refreshing…" : "Refresh meeting evidence"}
        </button>
      </div>
      {status ? <p role="status">{status}</p> : null}
      {attached.length === 0 ? (
        <p className="person-empty">No attached meetings yet.</p>
      ) : (
        <div className="person-timeline">
          {attached.map((meeting) => (
            <article key={meeting.evidence_id} className="person-timeline-entry">
              <p className="person-timeline-meta">
                {[meeting.source_ref.provider, when(meeting.starts_at)]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
              <p>{meeting.title}</p>
              {meeting.notes ? (
                <span
                  className="pf-badge is-warn"
                  title="Meeting notes are untrusted evidence."
                >
                  Untrusted notes
                </span>
              ) : (
                <span className="pf-badge">No notes</span>
              )}
            </article>
          ))}
        </div>
      )}
      {proposed.length > 0 ? (
        <div className="person-meeting-proposals">
          <h3>Needs review</h3>
          {proposed.map((meeting) => (
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
                  className="pf-btn"
                  disabled={busy !== null}
                  aria-label={`Attach ${meeting.title}`}
                  onClick={() => void review(meeting.evidence_id, "attach")}
                >
                  <Icon name="add" />
                  Attach
                </button>
                <button
                  type="button"
                  className="pf-btn"
                  disabled={busy !== null}
                  aria-label={`Reject ${meeting.title}`}
                  onClick={() => void review(meeting.evidence_id, "reject")}
                >
                  <Icon name="deny" />
                  Reject
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

/** Drive files attached to this person, and an explicit search-and-attach. */
export function DriveEvidence({
  personId,
  sources,
  onChanged,
}: {
  personId: string;
  sources: NonNullable<PersonFile["person"]["sources"]>;
  onChanged: () => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DriveEvidenceCandidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const attached = sources.filter((source) => source.fields?.provider === "Google Drive");

  async function search(event: FormEvent) {
    event.preventDefault();
    if (searching || !query.trim()) return;
    setSearching(true);
    setStatus(null);
    try {
      const result = await searchPersonDriveEvidence(personId, query.trim());
      setResults(result.files);
    } catch {
      setResults(null);
      setStatus("Couldn’t search Drive. Try again.");
    } finally {
      setSearching(false);
    }
  }

  async function attach(candidate: DriveEvidenceCandidate) {
    if (busy) return;
    setBusy(candidate.id);
    setStatus(null);
    try {
      await attachPersonDriveEvidence(personId, {
        kind: "search_result",
        fileId: candidate.id,
      });
      await onChanged();
      setStatus(`Attached “${candidate.name ?? "Drive file"}” to this person.`);
    } catch {
      setStatus("Couldn’t attach this Drive result. Try again.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="person-section" aria-labelledby="person-drive-evidence-heading">
      <h2 id="person-drive-evidence-heading">Drive evidence</h2>
      {attached.length === 0 ? (
        <p className="person-empty">No Drive evidence attached yet.</p>
      ) : (
        <div className="person-timeline">
          {attached.map((source) => (
            <article key={source.id} className="person-timeline-entry">
              <p className="person-timeline-meta">
                Google Drive ·{" "}
                {String(source.fields?.extraction_status ?? "metadata_only").replace(/_/g, " ")}
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

      <form className="person-drive-search" onSubmit={(event) => void search(event)}>
        <label htmlFor="person-drive-query" className="pf-visually-hidden">
          Search Drive
        </label>
        <input
          id="person-drive-query"
          type="text"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search Drive by file name or contents"
        />
        <button type="submit" className="pf-btn" disabled={searching || !query.trim()}>
          <Icon name="search" />
          {searching ? "Searching…" : "Search Drive"}
        </button>
      </form>
      {status ? <p role="status">{status}</p> : null}
      {results !== null && results.length === 0 ? (
        <p className="person-empty">No Drive results for that search.</p>
      ) : null}
      {results !== null && results.length > 0 ? (
        <div className="person-timeline">
          {results.map((candidate) => (
            <article key={candidate.id} className="person-timeline-entry">
              <p className="person-timeline-meta">{candidate.mimeType ?? "Drive file"}</p>
              <p>{candidate.name ?? "Untitled Drive file"}</p>
              <div className="person-drive-actions">
                <button
                  type="button"
                  className="pf-btn"
                  disabled={busy !== null}
                  aria-label={`Attach ${candidate.name ?? "Drive file"}`}
                  onClick={() => void attach(candidate)}
                >
                  <Icon name="add" />
                  {busy === candidate.id ? "Attaching…" : "Attach to person"}
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
