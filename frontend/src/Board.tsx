import { useEffect, useRef, useState, type ReactNode } from "react";

import { DescriptionEditor, describeDescription } from "./DescriptionEditor";
import { FindSavedPerson } from "./FindSavedPerson";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";
import { personInitials } from "./personName";
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
import { MailStatusChip } from "./crm/MailFreshness";
import { PersonPreview, type PreviewHandle } from "./crm/PersonPreview";
import { GlobalSuggestions } from "./crm/GlobalSuggestions";

function personName(person: BoardPerson): string {
  return [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person";
}

const FOLLOW_UP_LABEL: Record<string, string> = {
  reply_unanswered: "Reply to review",
  reply_needs_review: "Reply needs review",
};

type SequenceFilter = "all" | "open" | "in_conversation" | "done" | "not_started";
type Sequence = Exclude<SequenceFilter, "all">;

const SEQUENCE_FILTERS: { value: SequenceFilter; label: string; icon: ReactNode }[] = [
  { value: "all", label: "All", icon: <Glyph name="user" /> },
  { value: "open", label: "Open", icon: <Icon name="send" /> },
  { value: "in_conversation", label: "In conversation", icon: <Glyph name="chat" /> },
  { value: "done", label: "Done", icon: <Icon name="check" /> },
  { value: "not_started", label: "Not started", icon: <Glyph name="empty" /> },
];

const SEQUENCE_LABEL = Object.fromEntries(
  SEQUENCE_FILTERS.map((filter) => [filter.value, filter.label]),
) as Record<SequenceFilter, string>;

/** A saved person outside Open, In conversation and Done has not started outreach. */
function sequenceOf(person: Pick<BoardPerson, "sequence_state">): Sequence {
  const state = person.sequence_state;
  return state === "open" || state === "in_conversation" || state === "done" ? state : "not_started";
}

/** Canonical task dates are YYYY-MM-DD days; show them as a short local day. */
function formatDue(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(year === new Date().getFullYear() ? {} : { year: "numeric" }),
  });
}

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
          startEditing
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
      className="contacts-description"
      aria-label={muted ? "Edit short description" : `Edit short description: ${text}`}
      title={muted ? "Add a short description" : text}
      onClick={(event) => {
        event.stopPropagation();
        setEditing(true);
      }}
    >
      <span>{muted ? null : text}</span>
      <Icon name="edit" />
    </button>
  );
}

function NextTask({ task }: { task?: PersonTask | null }) {
  if (!task) return null;
  const overdue = task.due_status === "overdue";
  return (
    <span className="contacts-task" title={task.due_date ? `${task.title} · due ${task.due_date}` : task.title}>
      <span className="contacts-task-title">{task.title}</span>
      {task.due_date ? (
        <span className={`contacts-due${overdue ? " is-overdue" : task.due_status === "today" ? " is-today" : ""}`}>
          <time dateTime={task.due_date}>{formatDue(task.due_date)}</time>
          {overdue ? <span className="sr-only">, overdue</span> : null}
        </span>
      ) : null}
    </span>
  );
}

function AttentionCell({ person }: { person: BoardPerson }) {
  const reasons = person.attention_reasons;
  if (reasons === undefined) {
    // Older payloads without saved attention fall back to the follow-up flag.
    const reason = person.follow_up?.needed ? person.follow_up.reason : null;
    return reason ? (
      <span className="contacts-pill is-warn">{FOLLOW_UP_LABEL[reason] ?? "Needs follow-up"}</span>
    ) : null;
  }
  if (reasons.length === 0) return null;
  const [first, ...rest] = reasons;
  if (rest.length === 0) {
    return <span className="contacts-pill is-warn" title={first.label}>{first.label}</span>;
  }
  return (
    <details className="contacts-attention">
      <summary title={reasons.map((reason) => reason.label).join(" · ")}>
        <span className="contacts-pill is-warn">{first.label}</span>
        <span className="contacts-more">+{rest.length}</span>
      </summary>
      {rest.map((reason) => (
        <p key={reason.code}>{reason.label}{reason.count ? ` (${reason.count})` : ""}</p>
      ))}
    </details>
  );
}

function ContactRow({
  person,
  selected,
  onOpen,
}: {
  person: BoardPerson;
  selected: boolean;
  onOpen: (id: string) => void;
}) {
  const name = personName(person);
  const sequence = sequenceOf(person);
  const href = `#/people/${encodeURIComponent(person.person_id)}`;
  const openPreview = () => {
    onOpen(person.person_id);
  };
  return (
    <tr
      className="contacts-row"
      tabIndex={0}
      aria-label={`Preview person ${name}`}
      aria-current={selected ? "true" : undefined}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("a, button, textarea, input, select, summary")) return;
        openPreview();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        if ((event.target as HTMLElement).closest("a, button, textarea, input, select, summary")) return;
        event.preventDefault();
        openPreview();
      }}
    >
      <td>
        <div className="contacts-person">
          <button type="button" id={`preview-${person.person_id}`} className="contacts-avatar" aria-label={`Preview ${name}`} onClick={openPreview}>
            {personInitials(person)}
          </button>
          <a href={href} onClick={event => { event.preventDefault(); openPreview(); }}>{name}</a>
          {person.last_name_status === "hidden_by_apollo" ? (
            <span className="contacts-hidden-surname" title="Surname hidden by Apollo">
              <Glyph name="restricted" />
              <span className="sr-only">Surname hidden by Apollo</span>
            </span>
          ) : null}
        </div>
      </td>
      <td title={person.title ?? undefined}>{person.title}</td>
      <td title={person.company ?? undefined}>{person.company}</td>
      <td>
        <span className={`contacts-pill contacts-sequence-${sequence}`}>{SEQUENCE_LABEL[sequence]}</span>
      </td>
      <td>
        <NextTask task={person.next_action} />
      </td>
      <td>
        <AttentionCell person={person} />
      </td>
      <td>
        <ContactDescriptionCell person={person} />
      </td>
    </tr>
  );
}

const COLUMNS: { label: string; icon: ReactNode }[] = [
  { label: "Person", icon: <Glyph name="user" /> },
  { label: "Role", icon: <Glyph name="card" /> },
  { label: "Company", icon: <Icon name="company" /> },
  { label: "Sequence", icon: <Glyph name="partial" /> },
  { label: "Next task", icon: <Glyph name="clock" /> },
  { label: "Attention", icon: <Glyph name="info" /> },
  { label: "Summary", icon: <Glyph name="doc" /> },
];

function ContactsTable({
  people,
  selected,
  onOpen,
}: {
  people: BoardPerson[];
  selected: string | null;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="contacts-table-scroll">
      <table className="contacts-table" aria-label="Contacts">
        <thead>
          <tr>
            {COLUMNS.map((column) => (
              <th key={column.label} scope="col">
                <span className="contacts-th">{column.icon}{column.label}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {people.map((person) => (
            <ContactRow
              key={person.person_id}
              person={person}
              selected={person.person_id === selected}
              onOpen={onOpen}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ContactsFilters({
  active,
  people,
  onChange,
}: {
  active: SequenceFilter;
  people: BoardPerson[];
  onChange: (filter: SequenceFilter) => void;
}) {
  const count = (filter: SequenceFilter) =>
    filter === "all" ? people.length : people.filter((person) => sequenceOf(person) === filter).length;
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
          {filter.icon}
          <span>{filter.label}</span>
          <span className="contacts-filter-count">{count(filter.value)}</span>
        </button>
      ))}
    </div>
  );
}

type BoardViewMode = "contacts" | "tasks";
type ContactsSort = "attention" | "name" | "due";
type ContactsViewPreferences = {
  view: BoardViewMode;
  sequenceFilter: SequenceFilter;
  contactQuery: string;
  needsAttention: boolean;
  sort: ContactsSort;
};

const CONTACTS_VIEW_KEY = "sourcecado:contacts-view:v1";
const DEFAULT_CONTACTS_VIEW: ContactsViewPreferences = {
  view: "contacts",
  sequenceFilter: "all",
  contactQuery: "",
  needsAttention: false,
  sort: "attention",
};

function readContactsView(): ContactsViewPreferences {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(CONTACTS_VIEW_KEY) ?? "null");
    if (!saved || typeof saved !== "object" || Array.isArray(saved)) return DEFAULT_CONTACTS_VIEW;
    const values = saved as Record<string, unknown>;
    return {
      view: values.view === "tasks" ? "tasks" : "contacts",
      sequenceFilter: SEQUENCE_FILTERS.some(filter => filter.value === values.sequenceFilter)
        ? values.sequenceFilter as SequenceFilter : "all",
      contactQuery: typeof values.contactQuery === "string" ? values.contactQuery : "",
      needsAttention: values.needsAttention === true,
      sort: values.sort === "name" || values.sort === "due" ? values.sort : "attention",
    };
  } catch {
    return DEFAULT_CONTACTS_VIEW;
  }
}

function compareContacts(a: BoardPerson, b: BoardPerson, sort: ContactsSort): number {
  let order: number;
  if (sort === "name") {
    order = personName(a).toLocaleLowerCase().localeCompare(personName(b).toLocaleLowerCase());
  } else if (sort === "due") {
    // Canonical task dates are YYYY-MM-DD. Missing dates follow every dated task.
    const first = a.next_action?.due_date;
    const second = b.next_action?.due_date;
    order = first && second ? first.localeCompare(second) : first ? -1 : second ? 1 : 0;
  } else {
    order = (a.attention_rank ?? 9999) - (b.attention_rank ?? 9999);
  }
  return order || a.person_id.localeCompare(b.person_id);
}

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
          { value: "contacts", label: "People", icon: <Glyph name="user" /> },
          { value: "tasks", label: "Tasks", icon: <Glyph name="review" /> },
        ] as const
      ).map((view) => (
        <button
          key={view.value}
          type="button"
          aria-pressed={active === view.value}
          onClick={() => onChange(view.value)}
        >
          {view.icon}
          {view.label}
        </button>
      ))}
    </div>
  );
}

/** The status filter and search apply to tasks through the person they belong to. */
function matchesTask(task: PersonTask, filter: SequenceFilter, query: string): boolean {
  if (filter !== "all" && (!task.person || sequenceOf(task.person) !== filter)) return false;
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

type PreviewStep = "previous" | "next";

export function BoardView() {
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [checking, setChecking] = useState(false);
  const [replyStatusText, setReplyStatusText] = useState<string | null>(null);
  const [initialView] = useState(readContactsView);
  const [sequenceFilter, setSequenceFilter] = useState<SequenceFilter>(initialView.sequenceFilter);
  const [contactQuery, setContactQuery] = useState(initialView.contactQuery);
  const [view, setView] = useState<BoardViewMode>(initialView.view);
  const [needsAttention, setNeedsAttention] = useState(initialView.needsAttention);
  const [sort, setSort] = useState<ContactsSort>(initialView.sort);
  const [selected, setSelected] = useState<string | null>(null);
  const [previewStep, setPreviewStep] = useState<PreviewStep | undefined>();
  const preview = useRef<PreviewHandle>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(CONTACTS_VIEW_KEY, JSON.stringify({ view, sequenceFilter, contactQuery, needsAttention, sort }));
    } catch {
      // The current view still works when local storage is unavailable or full.
    }
  }, [view, sequenceFilter, contactQuery, needsAttention, sort]);

  function resetView() {
    setView(DEFAULT_CONTACTS_VIEW.view);
    setSequenceFilter(DEFAULT_CONTACTS_VIEW.sequenceFilter);
    setContactQuery(DEFAULT_CONTACTS_VIEW.contactQuery);
    setNeedsAttention(DEFAULT_CONTACTS_VIEW.needsAttention);
    setSort(DEFAULT_CONTACTS_VIEW.sort);
  }

  function openPreview(id: string, step?: PreviewStep) {
    const next = () => {
      returnFocus.current = document.getElementById(`preview-${id}`);
      setPreviewStep(step);
      setSelected(id);
    };
    if (selected) preview.current?.navigate(next);
    else next();
  }

  function closePreview() {
    setSelected(null);
    returnFocus.current?.focus();
  }
  const [finderOpen, setFinderOpen] = useState(false);


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

  // Contacts-scoped "/" shortcut opens the finder. It never collides with the
  // global ⌘K palette, and is ignored while the director is typing in a field.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable='true']")) return;
      event.preventDefault();
      setFinderOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
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

  // Every saved person, including people kept before outreach starts.
  const people = board
    ? [...(board.backlog ?? []), ...board.open, ...board.in_conversation, ...board.done]
    : [];
  const visibleContacts = people
    .filter((person) => sequenceFilter === "all" || sequenceOf(person) === sequenceFilter)
    .filter((person) => matchesContact(person, contactQuery))
    .filter(person => !needsAttention || (person.attention_reasons !== undefined
      ? person.attention_reasons.length > 0 : Boolean(person.follow_up?.needed)))
    .sort((a, b) => compareContacts(a, b, sort));
  const empty = board !== null && people.length === 0;
  const selectedIndex = view === "contacts" && selected
    ? visibleContacts.findIndex((person) => person.person_id === selected)
    : -1;
  const position = selectedIndex >= 0 ? {
    index: selectedIndex,
    total: visibleContacts.length,
    onPrevious: () => openPreview(visibleContacts[selectedIndex - 1].person_id, "previous"),
    onNext: () => openPreview(visibleContacts[selectedIndex + 1].person_id, "next"),
  } : undefined;

  return (
    <main className={`route-page board-page${selected ? " has-preview" : ""}`}>
      <div className="contacts-workspace">
      <header className="contacts-titlebar">
        <div className="contacts-heading">
          <h1>Contacts</h1>
          {board ? (
            <span className="contacts-count">
              {people.length}
              <span className="sr-only">{people.length === 1 ? " person" : " people"}</span>
            </span>
          ) : null}
          <ViewSwitch active={view} onChange={setView} />
        </div>
        <div className="contacts-actions">
          {replyStatusText ? <p className="contacts-mail-result" role="status">{replyStatusText}</p> : null}
          {board ? <MailStatusChip state={board.mail_sync} /> : null}
          <button
            type="button"
            className={`contacts-icon-button${checking ? " is-spinning" : ""}`}
            aria-label="Refresh Gmail"
            title={checking ? "Checking Gmail…" : "Refresh Gmail"}
            aria-busy={checking}
            disabled={checking}
            onClick={() => void checkReplies()}
          >
            <Icon name="refresh" />
          </button>
          <button
            type="button"
            className="contacts-icon-button"
            aria-label="Find saved person"
            aria-keyshortcuts="/"
            title="Find saved person (/)"
            onClick={() => setFinderOpen(true)}
          >
            <Icon name="search" />
          </button>
        </div>
      </header>
      {finderOpen ? <FindSavedPerson onClose={() => setFinderOpen(false)} /> : null}
      {board === null && !failed ? <p className="contacts-loading" role="status">Loading contacts…</p> : null}
      {failed ? (
        <section className="route-error" role="alert">
          <p>Couldn’t load contacts.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            <Glyph name="retry" />
            Retry
          </button>
        </section>
      ) : null}
      {empty && view === "contacts" ? (
        <section className="contacts-empty" role="status">
          <Glyph name="user" />
          <h2>No saved people yet</h2>
          <a className="contacts-chip" href="#/chat">
            <Icon name="search" />
            Find people
          </a>
        </section>
      ) : null}
      {board && (!empty || view === "tasks") ? (
        <>
          <div className="contacts-toolbar">
            <ContactsFilters
              active={sequenceFilter}
              people={people}
              onChange={setSequenceFilter}
            />
            <div className="contacts-toolbar-end">
              <button
                type="button"
                className="contacts-chip"
                aria-pressed={needsAttention}
                onClick={() => setNeedsAttention(value => !value)}
              >
                <Glyph name="info" />
                Needs attention
              </button>
              {view === "contacts" ? (
                <label className="contacts-chip contacts-sort" title="Sort">
                  <Icon name="sort" />
                  <select aria-label="Sort contacts" value={sort} onChange={event => setSort(event.currentTarget.value as ContactsSort)}>
                    <option value="attention">Attention</option>
                    <option value="name">Name</option>
                    <option value="due">Next task due</option>
                  </select>
                </label>
              ) : null}
              <label className="contacts-search">
                <Icon name="search" />
                <input
                  type="search"
                  aria-label={view === "tasks" ? "Search tasks" : "Search contacts"}
                  placeholder="Search"
                  value={contactQuery}
                  onChange={(event) => setContactQuery(event.currentTarget.value)}
                />
              </label>
              <button
                type="button"
                className="contacts-icon-button"
                aria-label="Reset view"
                title="Reset view"
                onClick={resetView}
              >
                <Glyph name="retry" />
              </button>
            </div>
          </div>
          <section
            className="contacts-list"
            aria-label={view === "tasks" ? "Tasks for contacts" : "Contacts table"}
          >
            {view === "tasks" ? (
              <>
                <TaskList
                  people={people}
                  heading="Open tasks"
                  matches={(task) => matchesTask(task, sequenceFilter, contactQuery)
                    && (!needsAttention || people.some(p => p.person_id === task.person_id && Boolean(p.attention_reasons?.length))
                      || task.due_status === "overdue" || task.due_status === "today")}
                />
                <GlobalSuggestions query={contactQuery} status={sequenceFilter} onOpen={openPreview} />
              </>
            ) : (
              <>
                <ContactsTable people={visibleContacts} selected={selected} onOpen={openPreview} />
                {visibleContacts.length === 0 ? (
                  <p className="contacts-filter-empty" role="status">
                    {contactQuery.trim()
                      ? `No contacts match “${contactQuery.trim()}”.`
                      : needsAttention ? "Nobody needs attention."
                      : `No contacts in ${SEQUENCE_LABEL[sequenceFilter]}.`}
                  </p>
                ) : (
                  <p className="contacts-footer">
                    {visibleContacts.length} {visibleContacts.length === 1 ? "person" : "people"}
                  </p>
                )}
              </>
            )}
          </section>
        </>
      ) : null}
      </div>
      {selected ? (
        <PersonPreview
          key={selected}
          ref={preview}
          personId={selected}
          onClose={closePreview}
          position={position}
          initialFocus={previewStep}
        />
      ) : null}
    </main>
  );
}
