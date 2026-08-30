import { useEffect, useState } from "react";

import {
  getBoard,
  refreshReplies,
  type Board,
  type BoardPerson,
  type ReplyRefreshResult,
} from "./api";

function personName(person: BoardPerson): string {
  return [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person";
}

function day(stamp: string | null | undefined): string | null {
  if (typeof stamp !== "string" || !stamp.trim()) return null;
  return stamp.slice(0, 10);
}

/** Last contact and replied state, in the operator's words. */
function contactLine(person: BoardPerson): string {
  const when = day(person.last_contact_at);
  if (!when) return "No contact yet";
  const who = person.last_contact_direction === "inbound" ? "They replied" : "We wrote";
  return `${who} · ${when}`;
}

const FOLLOW_UP_LABEL: Record<string, string> = {
  reply_unanswered: "Needs follow-up",
  reply_needs_review: "Reply needs review",
};

function FollowUpChip({ person }: { person: BoardPerson }) {
  const reason = person.follow_up?.needed ? person.follow_up.reason : null;
  if (!reason) return null;
  return (
    <span className="board-row-flag">{FOLLOW_UP_LABEL[reason] ?? "Needs follow-up"}</span>
  );
}

function initials(person: BoardPerson): string {
  const letters = [person.first_name, person.last_name]
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .map((value) => Array.from(value)[0])
    .join("")
    .slice(0, 2);
  return letters.toLocaleUpperCase() || "?";
}

const SEQUENCE_LABEL: Record<string, string> = {
  open: "Open",
  in_conversation: "In conversation",
  done: "Done",
};

type SequenceFilter = "all" | "open" | "in_conversation" | "done";

const SEQUENCE_FILTERS: { value: SequenceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "open", label: "Open" },
  { value: "in_conversation", label: "In conversation" },
  { value: "done", label: "Done" },
];

function ContactRow({ person }: { person: BoardPerson }) {
  const name = personName(person);
  const sequence = SEQUENCE_LABEL[person.sequence_state ?? ""] ?? "Unknown";
  const href = `#/people/${encodeURIComponent(person.person_id)}`;
  const openPersonFile = () => {
    window.location.hash = href;
  };
  return (
    <tr
      className="contacts-row"
      tabIndex={0}
      aria-label={`Open Person File for ${name}`}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("a")) return;
        openPersonFile();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        openPersonFile();
      }}
    >
      <td>
        <div className="contacts-identity">
          <span className="contacts-avatar" aria-hidden="true">
            {initials(person)}
          </span>
          <span className="contacts-cell-copy">
            <a href={href} tabIndex={-1}>
              <strong>{name}</strong>
            </a>
            {person.last_name_status === "hidden_by_apollo" ? (
              <span>Surname hidden by Apollo</span>
            ) : null}
          </span>
        </div>
      </td>
      <td>
        <span className="contacts-primary">{person.title || "No title recorded"}</span>
        <span className="contacts-secondary">{person.company || "No company recorded"}</span>
      </td>
      <td>
        <span className={`contacts-sequence contacts-sequence-${person.sequence_state}`}>
          {sequence}
        </span>
      </td>
      <td>
        <span className="contacts-last-contact">{contactLine(person)}</span>
      </td>
      <td>
        {person.follow_up?.needed ? (
          <FollowUpChip person={person} />
        ) : (
          <span className="contacts-attention-empty" aria-label="No attention needed">
            —
          </span>
        )}
      </td>
    </tr>
  );
}

function ContactsTable({ people }: { people: BoardPerson[] }) {
  return (
    <div className="contacts-table-scroll">
      <table className="contacts-table" aria-label="Active sourcing contacts">
        <thead>
          <tr>
            <th scope="col">Contact</th>
            <th scope="col">Role &amp; company</th>
            <th scope="col">Sequence</th>
            <th scope="col">Last contact</th>
            <th scope="col">Attention</th>
          </tr>
        </thead>
        <tbody>
          {people.map((person) => (
            <ContactRow key={person.person_id} person={person} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ContactsFilters({
  active,
  board,
  onChange,
}: {
  active: SequenceFilter;
  board: Board;
  onChange: (filter: SequenceFilter) => void;
}) {
  const counts: Record<SequenceFilter, number> = {
    all: board.open.length + board.in_conversation.length + board.done.length,
    open: board.open.length,
    in_conversation: board.in_conversation.length,
    done: board.done.length,
  };
  return (
    <div
      className="contacts-filters"
      role="group"
      aria-label="Filter contacts by sequence status"
    >
      {SEQUENCE_FILTERS.map((filter) => (
        <button
          key={filter.value}
          type="button"
          aria-pressed={active === filter.value}
          onClick={() => onChange(filter.value)}
        >
          <span>{filter.label}</span>
          <span className="contacts-filter-count">{counts[filter.value]}</span>
        </button>
      ))}
    </div>
  );
}

function matchesContact(person: BoardPerson, query: string): boolean {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [person.first_name, person.last_name, person.title, person.company]
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .toLocaleLowerCase()
    .includes(needle);
}

/** What the refresh did, said plainly. Counts come from the server. */
function replyStatus(result: ReplyRefreshResult): string {
  if (result.status !== "ok") {
    return "Couldn’t reach Gmail. The last checked point is unchanged.";
  }
  const parts: string[] = [];
  if (result.filed > 0) {
    parts.push(`Filed ${result.filed} ${result.filed === 1 ? "reply" : "replies"}.`);
  }
  if (result.unassigned > 0) {
    parts.push(
      `${result.unassigned} ${result.unassigned === 1 ? "reply" : "replies"} need review.`,
    );
  }
  if (parts.length === 0) {
    return `Checked ${result.scanned} ${result.scanned === 1 ? "message" : "messages"}. No new replies.`;
  }
  return parts.join(" ");
}

export function BoardView() {
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [checking, setChecking] = useState(false);
  const [replyStatusText, setReplyStatusText] = useState<string | null>(null);
  const [sequenceFilter, setSequenceFilter] = useState<SequenceFilter>("all");
  const [contactQuery, setContactQuery] = useState("");

  useEffect(() => {
    let active = true;
    setFailed(false);
    getBoard().then(
      (next) => {
        if (active) setBoard(next);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt]);

  useEffect(() => {
    const refresh = () => setAttempt((value) => value + 1);
    window.addEventListener("sourcecado:board-changed", refresh);
    return () => window.removeEventListener("sourcecado:board-changed", refresh);
  }, []);

  async function checkReplies() {
    if (checking) return;
    setChecking(true);
    setReplyStatusText(null);
    try {
      const result = await refreshReplies();
      setReplyStatusText(replyStatus(result.refresh));
      setBoard(result.board);
    } catch {
      setReplyStatusText("Couldn’t check for replies. Try again.");
    } finally {
      setChecking(false);
    }
  }

  const contacts = board
    ? [...board.open, ...board.in_conversation, ...board.done]
    : [];
  const visibleContacts =
    (sequenceFilter === "all"
      ? contacts
      : contacts.filter((person) => person.sequence_state === sequenceFilter)
    ).filter((person) => matchesContact(person, contactQuery));
  const empty =
    board !== null &&
    contacts.length === 0;

  return (
    <main className="route-page board-page">
      <header className="board-page-header">
        <p className="eyebrow">Active sequences</p>
        <h1>Contacts</h1>
        <p>Keep active people moving from open outreach through completed follow-up.</p>
        <div className="board-page-actions">
          <button type="button" disabled={checking} onClick={() => void checkReplies()}>
            {checking ? "Checking for replies…" : "Check for replies"}
          </button>
          {replyStatusText ? <p role="status">{replyStatusText}</p> : null}
        </div>
      </header>
      {board === null && !failed ? <p role="status">Loading contacts…</p> : null}
      {failed ? (
        <section className="route-error" role="alert">
          <p>Couldn’t load contacts.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
        </section>
      ) : null}
      {empty ? (
        <section className="route-empty" role="status">
          <h2>No active contacts</h2>
          <p>Start a sequence from a Person File to add someone here.</p>
        </section>
      ) : null}
      {board && !empty ? (
        <section className="contacts-list" aria-label="Sequence contacts">
          <div className="contacts-controls">
            <ContactsFilters
              active={sequenceFilter}
              board={board}
              onChange={setSequenceFilter}
            />
            <label className="contacts-search">
              <span className="contacts-visually-hidden">Search active contacts</span>
              <input
                type="search"
                aria-label="Search active contacts"
                placeholder="Search active contacts"
                value={contactQuery}
                onChange={(event) => setContactQuery(event.currentTarget.value)}
              />
            </label>
          </div>
          {visibleContacts.length > 0 ? (
            <ContactsTable people={visibleContacts} />
          ) : (
            <p className="contacts-filter-empty" role="status">
              {contactQuery.trim()
                ? `No contacts match “${contactQuery.trim()}”.`
                : `No contacts in ${SEQUENCE_LABEL[sequenceFilter] ?? "this status"}.`}
            </p>
          )}
        </section>
      ) : null}
    </main>
  );
}
