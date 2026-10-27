import { useEffect, useRef, useState } from "react";

import { DescriptionEditor, describeDescription } from "./DescriptionEditor";
import { TaskList } from "./Tasks";
import {
  defaultPersonDescriptions,
  getPerson,
  newOperationId,
  setPersonSequence,
  StatusSaveError,
  undoStatusChange,
  type BoardPerson,
  type PersonDescriptions,
  type PersonFile,
  type StatusChange,
} from "./api";

const SEQUENCE_PILLS: { value: string; label: string; icon: string }[] = [
  { value: "open", label: "Open", icon: "○" },
  { value: "in_conversation", label: "In conversation", icon: "◐" },
  { value: "done", label: "Done", icon: "●" },
];

function personName(person: BoardPerson): string {
  return [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person";
}

function initials(person: BoardPerson): string {
  const letters = [person.first_name, person.last_name]
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .map((value) => Array.from(value)[0])
    .join("")
    .slice(0, 2);
  return letters.toLocaleUpperCase() || "?";
}

/** Copy-to-clipboard with a brief "Copied" acknowledgement. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="preview-copy"
      aria-label={`Copy ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable: leave the value on screen to copy by hand */
        }
      }}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

/** The Contact block: only the details that exist, each copyable. */
function ContactDetails({ person }: { person: BoardPerson }) {
  const rows: { field: string; label: string; value: string }[] = [];
  if (typeof person.email === "string" && person.email.trim()) {
    rows.push({ field: "email", label: "email", value: person.email });
  }
  if (typeof person.phone === "string" && person.phone.trim()) {
    rows.push({ field: "phone", label: "phone", value: person.phone });
  }
  if (rows.length === 0) return null;
  return (
    <section className="preview-section" aria-labelledby="preview-contact-heading">
      <h3 id="preview-contact-heading" className="preview-section-heading">
        Contact
      </h3>
      <dl className="preview-contact">
        {rows.map((row) => (
          <div key={row.field} className="preview-contact-row">
            <dt>{row.field === "email" ? "Email" : "Phone"}</dt>
            <dd>
              <span className="preview-contact-value">{row.value}</span>
              <CopyButton value={row.value} label={row.label} />
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Segmented Open / In conversation / Done control with history + undo. */
function StatusControl({
  person,
  onChanged,
}: {
  person: BoardPerson;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [lastChange, setLastChange] = useState<StatusChange | null>(null);
  // The version to save against; a conflict replaces it with the server's.
  const [current, setCurrent] = useState<BoardPerson>(person);

  useEffect(() => {
    setCurrent(person);
    setLastChange(null);
    setStatus(null);
  }, [person]);

  async function move(state: string) {
    if (busy || state === current.sequence_state) return;
    const expectedVersion = current.version;
    if (typeof expectedVersion !== "number") {
      setStatus("Couldn’t read this person’s version. Reopen the preview.");
      return;
    }
    setBusy(state);
    setStatus(null);
    try {
      const result = await setPersonSequence(current.person_id, state, {
        expectedVersion,
        operationId: newOperationId(),
      });
      setCurrent(result.person);
      setLastChange(result.change);
      onChanged();
    } catch (error) {
      if (error instanceof StatusSaveError && error.status === 409) {
        if (error.current) setCurrent(error.current);
        setStatus("The status changed elsewhere. This is the current value — try again.");
      } else {
        setStatus("Couldn’t save the status. It’s unchanged — try again.");
      }
    } finally {
      setBusy(null);
    }
  }

  async function undo() {
    if (busy || !lastChange) return;
    setBusy("undo");
    setStatus(null);
    try {
      const result = await undoStatusChange(current.person_id, lastChange.change_id, {
        operationId: newOperationId(),
      });
      setCurrent(result.person);
      setLastChange(null);
      onChanged();
    } catch (error) {
      if (error instanceof StatusSaveError && error.status === 409) {
        if (error.current) setCurrent(error.current);
        setLastChange(null);
        setStatus("The status moved on since then, so this undo no longer applies.");
      } else {
        setStatus("Couldn’t undo. Try again.");
      }
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="preview-section" aria-labelledby="preview-status-heading">
      <h3 id="preview-status-heading" className="preview-section-heading">
        Status
      </h3>
      <div className="preview-status-pills" role="group" aria-label="Relationship status">
        {SEQUENCE_PILLS.map((pill) => (
          <button
            key={pill.value}
            type="button"
            className={`preview-status-pill preview-status-${pill.value}`}
            aria-pressed={current.sequence_state === pill.value}
            disabled={busy !== null}
            onClick={() => void move(pill.value)}
          >
            <span className="preview-status-icon" aria-hidden="true">
              {pill.icon}
            </span>
            <span>{busy === pill.value ? "Saving…" : pill.label}</span>
          </button>
        ))}
      </div>
      {lastChange ? (
        <button
          type="button"
          className="preview-status-undo"
          disabled={busy !== null}
          onClick={() => void undo()}
        >
          ↩ Undo status change
        </button>
      ) : null}
      {status ? (
        <p className="preview-status-message" role="status">
          {status}
        </p>
      ) : null}
    </section>
  );
}

function Descriptions({
  personId,
  descriptions,
  onSaved,
}: {
  personId: string;
  descriptions: PersonDescriptions;
  onSaved: (next: PersonDescriptions) => void;
}) {
  const [editingShort, setEditingShort] = useState(false);
  const [showDetailed, setShowDetailed] = useState(false);
  const general = describeDescription(descriptions.general);

  return (
    <section className="preview-section" aria-labelledby="preview-about-heading">
      <h3 id="preview-about-heading" className="preview-section-heading">
        About
      </h3>
      {editingShort ? (
        <DescriptionEditor
          personId={personId}
          description={descriptions.general}
          rows={2}
          onSaved={onSaved}
          onClose={() => setEditingShort(false)}
        />
      ) : (
        <button
          type="button"
          className={`preview-description${general.muted ? " is-muted" : ""}`}
          aria-label="Edit short description"
          onClick={() => setEditingShort(true)}
        >
          {general.text}
        </button>
      )}
      <button
        type="button"
        className="preview-disclosure"
        aria-expanded={showDetailed}
        onClick={() => setShowDetailed((value) => !value)}
      >
        {showDetailed ? "Hide detailed description" : "Show detailed description"}
      </button>
      {showDetailed ? (
        <DescriptionEditor
          personId={personId}
          description={descriptions.detailed}
          rows={6}
          onSaved={onSaved}
        />
      ) : null}
    </section>
  );
}

const ACTIVITY_PREVIEW_COUNT = 4;

function Activity({ timeline }: { timeline: PersonFile["timeline"] }) {
  const [showAll, setShowAll] = useState(false);
  // Most recent first; the timeline is stored oldest-first.
  const ordered = [...timeline].reverse();
  const shown = showAll ? ordered : ordered.slice(0, ACTIVITY_PREVIEW_COUNT);
  return (
    <section className="preview-section" aria-labelledby="preview-activity-heading">
      <h3 id="preview-activity-heading" className="preview-section-heading">
        Recent activity
      </h3>
      {ordered.length === 0 ? (
        <p className="preview-empty-line">No activity yet.</p>
      ) : (
        <>
          <ul className="preview-activity">
            {shown.map((event) => (
              <li key={event.event_id}>
                <span className="preview-activity-summary">{event.summary}</span>
                <span className="preview-activity-source">{event.source}</span>
              </li>
            ))}
          </ul>
          {ordered.length > ACTIVITY_PREVIEW_COUNT ? (
            <button
              type="button"
              className="preview-disclosure"
              aria-expanded={showAll}
              onClick={() => setShowAll((value) => !value)}
            >
              {showAll
                ? "Show less activity"
                : `Show all activity (${ordered.length})`}
            </button>
          ) : null}
        </>
      )}
    </section>
  );
}

/**
 * Reserved, non-spending home for the deliberate enrichment action and its
 * last result (ticket #240) and the identity-correction action (ticket #243).
 * Those tickets fill these in; the preview only keeps the place so the layout
 * is stable and opening the preview never spends credits.
 */
function EnrichmentSlot() {
  return (
    <section className="preview-section preview-reserved" aria-labelledby="preview-enrich-heading">
      <h3 id="preview-enrich-heading" className="preview-section-heading">
        Enrichment &amp; corrections
      </h3>
      <div className="preview-reserved-actions">
        <button type="button" disabled title="Arrives in a later update (needs approval)">
          ⬡ Find missing contact info
        </button>
        <button type="button" disabled title="Arrives in a later update">
          ✎ Correct details
        </button>
      </div>
      <p className="preview-empty-line">
        Enrichment spends credits and always needs your approval; it arrives in a
        later update.
      </p>
    </section>
  );
}

/**
 * The person preview shown beside the Contacts list. A pure local read of the
 * person file — it never enriches or spends credits. `onClose` returns the
 * operator to the list; `onBoardChanged` lets the list refresh its counts.
 */
export function ContactPreview({
  personId,
  onClose,
  onBoardChanged,
}: {
  personId: string;
  onClose: () => void;
  onBoardChanged: () => void;
}) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let active = true;
    setFile(null);
    setFailed(false);
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
  }, [personId, attempt]);

  // Move focus into the panel when it opens so keyboard users land here.
  useEffect(() => {
    closeRef.current?.focus();
  }, [personId]);

  // Escape closes the preview and returns focus to the list.
  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  }

  const person = file?.person as BoardPerson | undefined;
  const href = `#/people/${encodeURIComponent(personId)}`;

  return (
    <aside
      className="contact-preview"
      aria-label="Person preview"
      aria-busy={file === null && !failed}
      onKeyDown={onKeyDown}
    >
      <div className="preview-topbar">
        <button
          ref={closeRef}
          type="button"
          className="preview-close"
          onClick={onClose}
        >
          ← Back to list
        </button>
        <a className="preview-open-file" href={href}>
          Open person file →
        </a>
      </div>

      {file === null && !failed ? (
        <div className="preview-loading" role="status">
          <p>Loading preview…</p>
          <div className="preview-skeleton" aria-hidden="true" />
          <div className="preview-skeleton" aria-hidden="true" />
          <div className="preview-skeleton" aria-hidden="true" />
        </div>
      ) : null}

      {failed ? (
        <div className="preview-error" role="alert">
          <p>Couldn’t load this preview.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
        </div>
      ) : null}

      {file && person ? (
        <div className="preview-body">
          <header className="preview-identity">
            <span className="preview-avatar" aria-hidden="true">
              {initials(person)}
            </span>
            <div className="preview-identity-copy">
              <h2 className="preview-name">{personName(person)}</h2>
              <p className="preview-role">
                {[person.title, person.company].filter(Boolean).join(" · ") ||
                  "No role recorded"}
              </p>
              {person.last_name_status === "hidden_by_apollo" ? (
                <p className="preview-note">Surname hidden by Apollo</p>
              ) : null}
            </div>
          </header>

          <StatusControl person={person} onChanged={onBoardChanged} />
          <ContactDetails person={person} />
          <Descriptions
            personId={personId}
            descriptions={file.descriptions ?? defaultPersonDescriptions()}
            onSaved={() => setAttempt((value) => value + 1)}
          />
          <section className="preview-section" aria-label="Tasks">
            <TaskList personId={personId} people={[]} heading="Tasks" headingLevel={3} />
          </section>
          <Activity timeline={file.timeline} />
          <EnrichmentSlot />
        </div>
      ) : null}
    </aside>
  );
}
