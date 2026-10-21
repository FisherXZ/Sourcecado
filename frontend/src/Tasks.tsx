import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  createTask,
  listTaskChanges,
  listTasks,
  newOperationId,
  TaskSaveError,
  undoTaskChange,
  updateTask,
  type BoardPerson,
  type PersonTask,
  type TaskChange,
  type TaskChangeAction,
  type TaskDueStatus,
  type TaskFields,
  type TaskListing,
  type TaskSourceLink,
} from "./api";

/** Fired after any task save so every mounted list reloads the same records. */
export const TASKS_CHANGED = "sourcecado:tasks-changed";

const GROUPS: { status: TaskDueStatus; label: string }[] = [
  { status: "overdue", label: "Overdue" },
  { status: "today", label: "Today" },
  { status: "upcoming", label: "Upcoming" },
  { status: "none", label: "No date" },
];

const DUE_LABEL: Record<TaskDueStatus, string> = {
  overdue: "Overdue",
  today: "Due today",
  upcoming: "Upcoming",
  none: "No date",
};

const STATE_LABEL: Record<PersonTask["state"], string> = {
  open: "Open",
  completed: "Completed",
  cancelled: "Cancelled",
};

const ACTION_LABEL: Record<TaskChangeAction, string> = {
  create: "Created",
  update: "Edited",
  complete: "Marked done",
  reopen: "Reopened",
  cancel: "Cancelled",
  undo: "Undid a change",
};

type Draft = { title: string; details: string; dueDate: string };

function draftFrom(task: PersonTask | null): Draft {
  return {
    title: task?.title ?? "",
    details: task?.details ?? "",
    dueDate: task?.due_date ?? "",
  };
}

function fieldsFrom(draft: Draft): TaskFields {
  return {
    title: draft.title.trim(),
    details: draft.details.trim(),
    due_date: draft.dueDate || null,
  };
}

function statusOf(task: PersonTask): TaskDueStatus {
  return task.due_status ?? (task.due_date ? "upcoming" : "none");
}

function taskPersonName(task: PersonTask): string {
  const person = task.person;
  return (
    [person?.first_name, person?.last_name].filter(Boolean).join(" ") || "Unknown person"
  );
}

function sourceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function TaskSources({ links }: { links?: TaskSourceLink[] }) {
  const sources = (links ?? []).slice(0, 3).flatMap((source) => {
    const href = source.url ? sourceUrl(source.url) : null;
    if (source.url && !href) return [];
    const label = `Source: ${source.label}`;
    return [href ? (
      <a key={source.id} href={href} target="_blank" rel="noreferrer">{label}</a>
    ) : (
      <span key={source.id}>{label}</span>
    )];
  });
  return sources.length ? <span className="task-meta">{sources}</span> : null;
}

function saveFailure(error: unknown): string {
  if (error instanceof TaskSaveError) {
    if (error.code === "task_conflict") {
      return "This task changed since it was loaded. Showing the current version.";
    }
    if (error.status === 404) {
      return "This person or task no longer exists. Your text is still here.";
    }
    if (error.status === 400) return `Couldn’t save: ${error.message}. Your text is still here.`;
    if (error.code === "operation_conflict") {
      return "This save collided with an earlier one. Your text is still here; save again.";
    }
  }
  return "Couldn’t save the task. Your text is still here; try again.";
}

/**
 * Reuse an operation id while the director retries the same text, so a save
 * that reached the server before the connection dropped is not added twice.
 * Changing the text starts a new operation.
 */
function useOperationId() {
  const pending = useRef<{ key: string; id: string } | null>(null);
  return {
    forPayload(payload: unknown): string {
      const key = JSON.stringify(payload);
      if (pending.current?.key !== key) {
        pending.current = { key, id: newOperationId() };
      }
      return pending.current.id;
    },
    settle() {
      pending.current = null;
    },
  };
}

function TaskFieldsForm({
  idPrefix,
  draft,
  onChange,
  disabled = false,
}: {
  idPrefix: string;
  draft: Draft;
  onChange: (draft: Draft) => void;
  // Locked while a save is in flight so text typed against a form that is
  // about to close on success cannot be silently discarded.
  disabled?: boolean;
}) {
  return (
    <>
      <p className="task-field">
        <label htmlFor={`${idPrefix}-title`}>Task</label>
        <input
          id={`${idPrefix}-title`}
          value={draft.title}
          maxLength={200}
          required
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, title: event.currentTarget.value })}
        />
      </p>
      <p className="task-field">
        <label htmlFor={`${idPrefix}-details`}>Description (optional)</label>
        <textarea
          id={`${idPrefix}-details`}
          rows={2}
          value={draft.details}
          maxLength={4000}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, details: event.currentTarget.value })}
        />
      </p>
      <p className="task-field task-field-date">
        <label htmlFor={`${idPrefix}-due`}>Due date (optional)</label>
        <input
          id={`${idPrefix}-due`}
          type="date"
          value={draft.dueDate}
          disabled={disabled}
          onChange={(event) => onChange({ ...draft, dueDate: event.currentTarget.value })}
        />
      </p>
    </>
  );
}

function TaskComposer({
  personId,
  people,
  onClose,
}: {
  personId?: string;
  people: BoardPerson[];
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(draftFrom(null));
  const [owner, setOwner] = useState(personId ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useOperationId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const fields = fieldsFrom(draft);
    if (!owner) {
      setError("Choose who this task is for.");
      return;
    }
    if (!fields.title) {
      setError("Give the task a title.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createTask(owner, {
        ...fields,
        operationId: operation.forPayload({ owner, ...fields }),
      });
      operation.settle();
      window.dispatchEvent(new Event(TASKS_CHANGED));
      onClose();
    } catch (failure) {
      setError(saveFailure(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="task-form" aria-label="New task" onSubmit={(event) => void submit(event)}>
      {personId ? null : (
        <p className="task-field">
          <label htmlFor="task-new-person">Person</label>
          <select
            id="task-new-person"
            value={owner}
            disabled={busy}
            onChange={(event) => setOwner(event.currentTarget.value)}
          >
            <option value="">Choose a person</option>
            {people.map((person) => (
              <option key={person.person_id} value={person.person_id}>
                {[person.first_name, person.last_name].filter(Boolean).join(" ") ||
                  "Unknown person"}
                {person.company ? ` · ${person.company}` : ""}
              </option>
            ))}
          </select>
        </p>
      )}
      <TaskFieldsForm idPrefix="task-new" draft={draft} onChange={setDraft} disabled={busy} />
      <div className="task-form-actions">
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save task"}
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  );
}

function TaskEditor({ task, onClose }: { task: PersonTask; onClose: () => void }) {
  const [draft, setDraft] = useState<Draft>(draftFrom(task));
  // The version this draft is based on. A conflict moves it to the newer
  // saved version only once the director has seen that version.
  const [base, setBase] = useState<PersonTask>(task);
  const [newer, setNewer] = useState<PersonTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useOperationId();
  const idPrefix = `task-edit-${task.task_id}`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const fields = fieldsFrom(draft);
    if (!fields.title) {
      setError("Give the task a title.");
      return;
    }
    const changes: Partial<TaskFields> = {};
    if (fields.title !== base.title) changes.title = fields.title;
    if (fields.details !== base.details) changes.details = fields.details;
    if (fields.due_date !== base.due_date) changes.due_date = fields.due_date;
    if (Object.keys(changes).length === 0) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await updateTask(task.person_id, task.task_id, {
        changes,
        expectedVersion: base.version,
        operationId: operation.forPayload({ changes, version: base.version }),
      });
      operation.settle();
      window.dispatchEvent(new Event(TASKS_CHANGED));
      onClose();
    } catch (failure) {
      if (failure instanceof TaskSaveError && failure.code === "task_conflict" && failure.current) {
        // Show the newer saved task and keep the director's unsaved text.
        operation.settle();
        setNewer(failure.current);
        setBase(failure.current);
        setError(null);
      } else {
        setError(saveFailure(failure));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="task-form"
      aria-label={`Edit task ${task.title}`}
      onSubmit={(event) => void submit(event)}
    >
      {newer ? (
        <div className="task-conflict" role="alert">
          <p>
            This task changed while you were editing. The saved version is now:
          </p>
          <dl>
            <dt>Task</dt>
            <dd>{newer.title}</dd>
            <dt>Description</dt>
            <dd>{newer.details || "None"}</dd>
            <dt>Due date</dt>
            <dd>{newer.due_date ?? "No date"}</dd>
          </dl>
          <p>Your unsaved text is kept below. Save to replace the saved version, or cancel.</p>
        </div>
      ) : null}
      <TaskFieldsForm idPrefix={idPrefix} draft={draft} onChange={setDraft} disabled={busy} />
      <div className="task-form-actions">
        <button type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" disabled={busy} onClick={onClose}>
          Cancel
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </form>
  );
}

/** The recorded changes of one task, newest first, each with Undo. */
function TaskHistory({ task, onClose }: { task: PersonTask; onClose: () => void }) {
  const [changes, setChanges] = useState<TaskChange[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const operation = useOperationId();

  useEffect(() => {
    let active = true;
    listTaskChanges(task.person_id, task.task_id).then(
      (next) => {
        if (!active) return;
        setChanges([...next].reverse());
        setFailed(false);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [task.person_id, task.task_id, task.version, attempt]);

  async function undo(change: TaskChange) {
    if (busy) return;
    setBusy(change.change_id);
    setNotice(null);
    try {
      await undoTaskChange(task.person_id, change.change_id, {
        operationId: operation.forPayload({ undo: change.change_id }),
      });
      operation.settle();
      window.dispatchEvent(new Event(TASKS_CHANGED));
    } catch (failure) {
      if (failure instanceof TaskSaveError && failure.code === "task_conflict") {
        operation.settle();
        setNotice("That change was followed by a newer edit. The current version is kept.");
        window.dispatchEvent(new Event(TASKS_CHANGED));
      } else {
        setNotice(saveFailure(failure));
      }
    } finally {
      setBusy(null);
      setAttempt((value) => value + 1);
    }
  }

  return (
    <div className="task-history" aria-label={`Changes to ${task.title}`} role="group">
      {changes === null && !failed ? <p className="task-list-status">Loading changes…</p> : null}
      {failed ? (
        <div className="task-list-error" role="alert">
          <p>Couldn’t load changes.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
        </div>
      ) : null}
      {changes ? (
        <ol className="task-history-rows">
          {changes.map((change) => (
            <li key={change.change_id} className="task-history-row">
              <span className="task-history-copy">
                <strong>{ACTION_LABEL[change.action] ?? change.action}</strong>
                <span className="task-meta">
                  <span>{change.actor === "assistant" ? "Assistant" : "You"}</span>
                  <span className="task-due">{change.created_at.slice(0, 16).replace("T", " ")}</span>
                  {change.reason && change.reason !== ACTION_LABEL[change.action] ? (
                    <span>{change.reason}</span>
                  ) : null}
                </span>
                <TaskSources links={change.source_links} />
              </span>
              {(change.can_undo ?? change.action !== "create") ? (
                <button
                  type="button"
                  className="task-row-action"
                  aria-label={`Undo ${ACTION_LABEL[change.action].toLowerCase()} on ${task.title}`}
                  disabled={busy !== null}
                  onClick={() => void undo(change)}
                >
                  {busy === change.change_id ? "Undoing…" : "Undo"}
                </button>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}
      {notice ? <p role="alert">{notice}</p> : null}
      <button type="button" className="task-row-action" onClick={onClose}>
        Hide changes
      </button>
    </div>
  );
}

function TaskRow({ task, showPerson }: { task: PersonTask; showPerson: boolean }) {
  const [editing, setEditing] = useState(false);
  const [history, setHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const status = statusOf(task);
  const editButton = useRef<HTMLButtonElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);
  const wasHistory = useRef(false);
  const operation = useOperationId();

  useEffect(() => {
    // Return focus to the row's control when its panel closes.
    if (wasEditing.current && !editing) editButton.current?.focus();
    wasEditing.current = editing;
  }, [editing]);
  useEffect(() => {
    if (wasHistory.current && !history) historyButton.current?.focus();
    wasHistory.current = history;
  }, [history]);

  async function setState(state: PersonTask["state"]) {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await updateTask(task.person_id, task.task_id, {
        changes: { state },
        expectedVersion: task.version,
        operationId: operation.forPayload({ task: task.task_id, state, version: task.version }),
      });
      operation.settle();
      window.dispatchEvent(new Event(TASKS_CHANGED));
    } catch (failure) {
      if (failure instanceof TaskSaveError && failure.code === "task_conflict") {
        operation.settle();
        window.dispatchEvent(new Event(TASKS_CHANGED));
      }
      setNotice(saveFailure(failure));
    } finally {
      setBusy(false);
    }
  }

  const finished = task.state !== "open";
  return (
    <li className={`task-row is-${finished ? "finished" : status} is-state-${task.state}`}>
      {editing ? (
        <TaskEditor task={task} onClose={() => setEditing(false)} />
      ) : (
        <>
          <div className="task-row-copy">
            <strong className="task-title">{task.title}</strong>
            {task.details ? <span className="task-details">{task.details}</span> : null}
            <span className="task-meta">
              {finished ? <span className="task-state">{STATE_LABEL[task.state]}</span> : null}
              {showPerson ? (
                <a href={`#/people/${encodeURIComponent(task.person_id)}`}>
                  {taskPersonName(task)}
                </a>
              ) : null}
              <span className="task-due">
                {!task.due_date
                  ? "No date"
                  : finished
                    ? `Due ${task.due_date}`
                    : `${DUE_LABEL[status]} · ${task.due_date}`}
              </span>
              <span>{task.origin === "assistant" ? "Added by assistant" : "Added by you"}</span>
            </span>
            <TaskSources links={task.source_links} />
            {notice ? <p className="task-row-notice" role="alert">{notice}</p> : null}
          </div>
          <div className="task-row-actions">
            {finished ? (
              <button
                type="button"
                className="task-row-action"
                aria-label={`Reopen task ${task.title}`}
                disabled={busy}
                onClick={() => void setState("open")}
              >
                Reopen
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="task-row-action"
                  aria-label={`Mark task ${task.title} done`}
                  disabled={busy}
                  onClick={() => void setState("completed")}
                >
                  Done
                </button>
                <button
                  type="button"
                  className="task-row-action"
                  aria-label={`Cancel task ${task.title}`}
                  disabled={busy}
                  onClick={() => void setState("cancelled")}
                >
                  Cancel
                </button>
                <button
                  ref={editButton}
                  type="button"
                  className="task-row-action"
                  aria-label={`Edit task ${task.title}`}
                  disabled={busy}
                  onClick={() => setEditing(true)}
                >
                  Edit
                </button>
              </>
            )}
            <button
              ref={historyButton}
              type="button"
              className="task-row-action"
              aria-label={`Changes to ${task.title}`}
              aria-expanded={history}
              onClick={() => setHistory((value) => !value)}
            >
              Changes
            </button>
          </div>
        </>
      )}
      {history ? <TaskHistory task={task} onClose={() => setHistory(false)} /> : null}
    </li>
  );
}

/**
 * Open tasks, grouped Overdue / Today / Upcoming / No date, with completed
 * and cancelled tasks kept in a collapsible History section.
 *
 * With `personId` it is that person's list; without it, the global list in
 * Contacts. Both read the same saved records, and a save in either reloads
 * every list on screen.
 */
export function TaskList({
  personId,
  people = [],
  matches,
  heading = "Tasks",
  headingLevel = 2,
}: {
  personId?: string;
  people?: BoardPerson[];
  matches?: (task: PersonTask) => boolean;
  heading?: string;
  headingLevel?: 2 | 3;
}) {
  const [listing, setListing] = useState<TaskListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  const Heading = `h${headingLevel}` as "h2" | "h3";
  const GroupHeading = `h${headingLevel + 1}` as "h3" | "h4";
  const headingId = `tasks-heading-${personId ?? "all"}`;

  useEffect(() => {
    let active = true;
    setLoading(true);
    listTasks({ ...(personId ? { personId } : {}), state: "all" }).then(
      (next) => {
        if (!active) return;
        setListing(next);
        setFailed(false);
        setLoading(false);
      },
      () => {
        if (!active) return;
        // Keep whatever was already on screen; a failed reload is not an empty list.
        setFailed(true);
        setLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt, personId]);

  useEffect(() => {
    const reload = () => setAttempt((value) => value + 1);
    window.addEventListener(TASKS_CHANGED, reload);
    window.addEventListener("sourcecado:board-changed", reload);
    return () => {
      window.removeEventListener(TASKS_CHANGED, reload);
      window.removeEventListener("sourcecado:board-changed", reload);
    };
  }, []);

  const showPerson = !personId;
  const matching = (listing?.tasks ?? []).filter((task) => (matches ? matches(task) : true));
  const tasks = matching.filter((task) => task.state === "open");
  const finished = matching
    .filter((task) => task.state !== "open")
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  const openCount = (listing?.tasks ?? []).filter((task) => task.state === "open").length;
  const filteredOut = listing !== null && listing.tasks.length > 0 && matching.length === 0;

  return (
    <section
      className="task-list"
      aria-labelledby={headingId}
      aria-busy={loading && listing === null ? "true" : undefined}
    >
      <div className="task-list-header">
        <Heading id={headingId}>{heading}</Heading>
        {adding ? null : (
          <button
            ref={addButton}
            type="button"
            onClick={() => setAdding(true)}
            disabled={!personId && people.length === 0}
          >
            Add task
          </button>
        )}
      </div>
      {adding ? (
        <TaskComposer
          personId={personId}
          people={people}
          onClose={() => {
            setAdding(false);
            requestAnimationFrame(() => addButton.current?.focus());
          }}
        />
      ) : null}
      {listing === null && loading ? (
        <p className="task-list-status" role="status">
          Loading tasks…
        </p>
      ) : null}
      {failed ? (
        <div className="task-list-error" role="alert">
          <p>
            {listing === null
              ? "Couldn’t load tasks."
              : "Couldn’t refresh tasks. Showing what was last loaded."}
          </p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
        </div>
      ) : null}
      {listing !== null && openCount === 0 ? (
        <p className="task-list-status" role="status">
          {personId
            ? "No open tasks for this person."
            : "No open tasks for people in Contacts."}
        </p>
      ) : null}
      {filteredOut ? (
        <p className="task-list-status" role="status">
          No tasks match these filters.
        </p>
      ) : null}
      {tasks.length > 0 ? (
        // One flat list, so a row being edited keeps its draft even when a
        // reload moves the task into another due group.
        <ul className="task-rows">
          {GROUPS.flatMap((group) => {
            const inGroup = tasks.filter((task) => statusOf(task) === group.status);
            if (inGroup.length === 0) return [];
            return [
              <li key={`group-${group.status}`} className="task-group-label">
                <GroupHeading>{group.label}</GroupHeading>
                <span className="task-group-count">{inGroup.length}</span>
              </li>,
              ...inGroup.map((task) => (
                <TaskRow key={task.task_id} task={task} showPerson={showPerson} />
              )),
            ];
          })}
        </ul>
      ) : null}
      {finished.length > 0 ? (
        <details className="task-history-section">
          <summary>
            History <span className="task-group-count">{finished.length}</span>
          </summary>
          <ul className="task-rows">
            {finished.map((task) => (
              <TaskRow key={task.task_id} task={task} showPerson={showPerson} />
            ))}
          </ul>
        </details>
      ) : null}
      {listing?.truncated ? (
        <p className="task-list-status">Showing the first {listing.tasks.length} tasks.</p>
      ) : null}
      {listing ? (
        <p className="task-list-timezone">Dates use {listing.timezone}.</p>
      ) : null}
    </section>
  );
}
