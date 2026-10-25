import { useEffect, useRef, useState } from "react";

import { DescriptionEditor, describeDescription } from "./DescriptionEditor";
import {
  defaultPersonDescriptions,
  getBoard,
  refreshReplies,
  startCrmRefresh,
  type Board,
  type BoardPerson,
  type PersonTask,
  type ReplyRefreshResult,
} from "./api";
import { TaskList, TASKS_CHANGED } from "./Tasks";
import { MailFreshness } from "./crm/MailFreshness";
import { PersonPreview, type PreviewHandle } from "./crm/PersonPreview";
import { GlobalSuggestions } from "./crm/GlobalSuggestions";

function personName(person: BoardPerson): string {
  return [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person";
}

const FOLLOW_UP_LABEL: Record<string, string> = {
  reply_unanswered: "Reply to review",
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

function ContactDescriptionCell({ person }: { person: BoardPerson }) {
  const [editing, setEditing] = useState(false);
  const general = (person.descriptions ?? defaultPersonDescriptions()).general;
  const { text, muted } = describeDescription(general);
  if (editing) {
    return (
      <div
        className="contacts-description-edit"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <DescriptionEditor
          personId={person.person_id}
          description={general}
          rows={2}
          onSaved={() =>
            window.dispatchEvent(new Event("sourcecado:board-changed"))
          }
          onClose={() => setEditing(false)}
        />
      </div>
    );
  }
  return (
    <button
      type="button"
      className={`contacts-description${muted ? " is-muted" : ""}`}
      aria-label="Edit short description"
      onClick={(event) => {
        event.stopPropagation();
        setEditing(true);
      }}
    >
      {text}
    </button>
  );
}

function ContactRow({ person, onOpen }: { person: BoardPerson; onOpen: (id: string) => void }) {
  const name = personName(person);
  const sequence = SEQUENCE_LABEL[person.sequence_state ?? ""] ?? "Unknown";
  const href = `#/people/${encodeURIComponent(person.person_id)}`;
  const openPersonFile = () => {
    onOpen(person.person_id);
  };
  return (
    <tr
      className="contacts-row"
      tabIndex={0}
      aria-label={`Preview person ${name}`}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("a, button, textarea, input, select, summary")) return;
        openPersonFile();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        if ((event.target as HTMLElement).closest("a, button, textarea, input, select, summary")) return;
        event.preventDefault();
        openPersonFile();
      }}
    >
      <td>
        <div className="contacts-identity">
          <button type="button" id={`preview-${person.person_id}`} className="contacts-avatar" aria-label={`Preview ${name}`} onClick={openPersonFile}>
            {initials(person)}
          </button>
          <span className="contacts-cell-copy">
            <a href={href} onClick={event => { event.preventDefault(); openPersonFile(); }}>
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
        <ContactDescriptionCell person={person} />
      </td>
      <td>
        <span className={`contacts-sequence contacts-sequence-${person.sequence_state}`}>
          {sequence}
        </span>
      </td>
      <td>
        <span className="contacts-next-action" title={person.next_action?.title ?? undefined}>
          {person.next_action ? <>{person.next_action.title}<span className="contacts-secondary">{person.next_action.due_date ?? "No date"}</span></> : "No open task"}
        </span>
      </td>
      <td>
        {person.attention_reasons?.length ? (
          <details className="contacts-attention-reasons"><summary><span className="board-row-flag">{person.attention_reasons[0].label}</span>
            {person.attention_reasons.length > 1 ? ` +${person.attention_reasons.length - 1}` : null}</summary>
            {person.attention_reasons.slice(1).map(reason => <p key={reason.code}>{reason.label}{reason.count ? ` (${reason.count})` : ""}</p>)}</details>
        ) : person.attention_reasons === undefined && person.follow_up?.needed ? (
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

function ContactsTable({ people, onOpen }: { people: BoardPerson[]; onOpen: (id: string) => void }) {
  return (
    <div className="contacts-table-scroll">
      <table className="contacts-table" aria-label="Active sourcing contacts">
        <thead>
          <tr>
            <th scope="col">Contact</th>
            <th scope="col">Role &amp; company</th>
            <th scope="col">Description</th>
            <th scope="col">Sequence</th>
            <th scope="col">Next action</th>
            <th scope="col">Attention</th>
          </tr>
        </thead>
        <tbody>
          {people.map((person) => (
            <ContactRow key={person.person_id} person={person} onOpen={onOpen} />
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

type BoardViewMode = "contacts" | "tasks";

function ViewSwitch({
  active,
  onChange,
}: {
  active: BoardViewMode;
  onChange: (view: BoardViewMode) => void;
}) {
  return (
    <div className="contacts-view-switch" role="group" aria-label="Contacts view">
      {(
        [
          { value: "contacts", label: "Contacts" },
          { value: "tasks", label: "Tasks" },
        ] as const
      ).map((view) => (
        <button
          key={view.value}
          type="button"
          aria-pressed={active === view.value}
          onClick={() => onChange(view.value)}
        >
          {view.label}
        </button>
      ))}
    </div>
  );
}

/** The status filter and search apply to tasks through the person they belong to. */
function matchesTask(task: PersonTask, filter: SequenceFilter, query: string): boolean {
  if (filter !== "all" && task.person?.sequence_state !== filter) return false;
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [
    task.title,
    task.details,
    task.person?.first_name,
    task.person?.last_name,
    task.person?.company,
  ]
    .filter((value): value is string => typeof value === "string")
    .join(" ")
    .toLocaleLowerCase()
    .includes(needle);
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
  if (result.status === "failed") {
    return "Couldn’t reach Gmail. The last checked point is unchanged.";
  }
  const parts: string[] = [];
  if (result.status === "partial") parts.push("Collection is unfinished. Refresh again to continue.");
  if (result.outgoing) parts.push(`Collected ${result.outgoing} outgoing ${result.outgoing === 1 ? "message" : "messages"}.`);
  if (result.filed > 0) {
    parts.push(`Filed ${result.filed} ${result.filed === 1 ? "reply" : "replies"}.`);
  }
  if (result.unassigned > 0) {
    parts.push(
      `${result.unassigned} ${result.unassigned === 1 ? "reply" : "replies"} need review.`,
    );
  }
  if (parts.length === 0) {
    return `Checked ${result.scanned} ${result.scanned === 1 ? "message" : "messages"}. No new mail.`;
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
  const [view, setView] = useState<BoardViewMode>("contacts");
  const [needsAttention, setNeedsAttention] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const preview = useRef<PreviewHandle>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  function openPreview(id: string) {
    const next = () => {
      returnFocus.current = document.getElementById(`preview-${id}`);
      setSelected(id);
    };
    if (selected) preview.current?.navigate(next);
    else next();
  }

  function closePreview() {
    setSelected(null);
    returnFocus.current?.focus();
  }

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
    window.addEventListener(TASKS_CHANGED, refresh);
    const timer = window.setInterval(refresh, 15000);
    void startCrmRefresh(false).catch(() => {});
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("sourcecado:board-changed", refresh);
      window.removeEventListener(TASKS_CHANGED, refresh);
    };
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
      setReplyStatusText("Couldn’t check Gmail. Try again.");
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
    ).filter((person) => matchesContact(person, contactQuery))
    .filter(person => !needsAttention || (person.attention_reasons !== undefined
      ? person.attention_reasons.length > 0 : Boolean(person.follow_up?.needed)))
    .sort((a, b) => (a.attention_rank ?? 9999) - (b.attention_rank ?? 9999));
  const empty =
    board !== null &&
    contacts.length === 0;

  return (
    <main className={`route-page board-page${selected ? " has-preview" : ""}`}>
      <div className="contacts-workspace">
      <header className="board-page-header">
        <p className="eyebrow">Active sequences</p>
        <h1>Contacts</h1>
        <p>Keep active people moving from open outreach through completed follow-up.</p>
        <ViewSwitch active={view} onChange={setView} />
        <div className="board-page-actions">
          <button type="button" disabled={checking} onClick={() => void checkReplies()}>
            {checking ? "Checking Gmail…" : "Refresh Gmail"}
          </button>
          {replyStatusText ? <p role="status">{replyStatusText}</p> : null}
        </div>
        {board ? <MailFreshness state={board.mail_sync} /> : null}
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
      {empty && view === "contacts" ? (
        <section className="route-empty" role="status">
          <h2>No active contacts</h2>
          <p>Start a sequence from a Person File to add someone here.</p>
        </section>
      ) : null}
      {board && (!empty || view === "tasks") ? (
        <section
          className="contacts-list"
          aria-label={view === "tasks" ? "Tasks for contacts" : "Sequence contacts"}
        >
          <div className="contacts-controls">
            <ContactsFilters
              active={sequenceFilter}
              board={board}
              onChange={setSequenceFilter}
            />
            <button type="button" aria-pressed={needsAttention} onClick={() => setNeedsAttention(value => !value)}>Needs attention</button>
            <label className="contacts-search">
              <span className="contacts-visually-hidden">
                {view === "tasks" ? "Search tasks" : "Search active contacts"}
              </span>
              <input
                type="search"
                aria-label={view === "tasks" ? "Search tasks" : "Search active contacts"}
                placeholder={view === "tasks" ? "Search tasks" : "Search active contacts"}
                value={contactQuery}
                onChange={(event) => setContactQuery(event.currentTarget.value)}
              />
            </label>
          </div>
          {view === "tasks" ? (
            <>
            <TaskList
              people={[...(board.backlog ?? []), ...contacts]}
              heading="Open tasks"
              matches={(task) => matchesTask(task, sequenceFilter, contactQuery)
                && (!needsAttention || [...(board.backlog ?? []), ...contacts].some(p => p.person_id === task.person_id && Boolean(p.attention_reasons?.length))
                  || task.due_status === "overdue" || task.due_status === "today")}
            />
            <GlobalSuggestions query={contactQuery} status={sequenceFilter} onOpen={openPreview} />
            </>
          ) : <><ContactsTable people={visibleContacts} onOpen={openPreview} />{visibleContacts.length === 0 ? (
            <p className="contacts-filter-empty" role="status">
              {contactQuery.trim()
                ? `No contacts match “${contactQuery.trim()}”.`
                : needsAttention ? "No known work needs attention. Check Gmail freshness above."
                : `No contacts in ${SEQUENCE_LABEL[sequenceFilter] ?? "this status"}.`}
            </p>
          ) : null}</>}
        </section>
      ) : null}
      {empty && view === "contacts" ? <ContactsTable people={[]} onOpen={openPreview} /> : null}
      </div>
      {selected ? <PersonPreview key={selected} ref={preview} personId={selected} onClose={closePreview} /> : null}
    </main>
  );
}
