import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  createTask,
  listTasks,
  newOperationId,
  TaskSaveError,
  updateTask,
  type BoardPerson,
  type PersonTask,
  type TaskDueStatus,
  type TaskFields,
  type TaskListing,
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

function saveFailure(error: unknown): string {
  if (error instanceof TaskSaveError) {
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

function TaskRow({ task, showPerson }: { task: PersonTask; showPerson: boolean }) {
  const [editing, setEditing] = useState(false);
  const status = statusOf(task);
  const editButton = useRef<HTMLButtonElement>(null);
  const wasEditing = useRef(false);

  useEffect(() => {
    // Return focus to the row's Edit control when the editor closes.
    if (wasEditing.current && !editing) editButton.current?.focus();
    wasEditing.current = editing;
  }, [editing]);

  return (
    <li className={`task-row is-${status}`}>
      {editing ? (
        <TaskEditor task={task} onClose={() => setEditing(false)} />
      ) : (
        <>
          <div className="task-row-copy">
            <strong className="task-title">{task.title}</strong>
            {task.details ? <span className="task-details">{task.details}</span> : null}
            <span className="task-meta">
              {showPerson ? (
                <a href={`#/people/${encodeURIComponent(task.person_id)}`}>
                  {taskPersonName(task)}
                </a>
              ) : null}
              <span className="task-due">
                {task.due_date ? `${DUE_LABEL[status]} · ${task.due_date}` : "No date"}
              </span>
              <span>{task.origin === "assistant" ? "Added by assistant" : "Added by you"}</span>
            </span>
          </div>
          <button
            ref={editButton}
            type="button"
            className="task-row-action"
            aria-label={`Edit task ${task.title}`}
            onClick={() => setEditing(true)}
          >
            Edit
          </button>
        </>
      )}
    </li>
  );
}

/**
 * Open tasks, grouped Overdue / Today / Upcoming / No date.
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
    listTasks(personId ? { personId } : {}).then(
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
  const tasks = (listing?.tasks ?? []).filter((task) => (matches ? matches(task) : true));
  const filteredOut = listing !== null && listing.tasks.length > 0 && tasks.length === 0;

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
      {listing !== null && listing.tasks.length === 0 ? (
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
      {listing?.truncated ? (
        <p className="task-list-status">Showing the first {listing.tasks.length} tasks.</p>
      ) : null}
      {listing ? (
        <p className="task-list-timezone">Dates use {listing.timezone}.</p>
      ) : null}
    </section>
  );
}
