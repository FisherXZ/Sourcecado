import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";

import { TaskDraft } from "./crm/TaskDraft";
import { UndoToast, useUndoToast, type UndoToastData } from "./crm/UndoToast";
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
  type TaskSaveResult,
  type TaskSourceLink,
} from "./api";
import { followUpHash } from "./app/route";
import { Icon, type IconName } from "./icons";
import { Glyph } from "./generative/ResultCard";

/** Fired after any task save so every mounted list reloads the same records. */
export const TASKS_CHANGED = "sourcecado:tasks-changed";

const GROUPS: { status: TaskDueStatus; label: string }[] = [
  { status: "overdue", label: "Overdue" },
  { status: "today", label: "Today" },
  { status: "upcoming", label: "Upcoming" },
  { status: "none", label: "No date" },
];

const ACTION: Record<TaskChangeAction, { label: string; icon: IconName }> = {
  create: { label: "Created", icon: "add" },
  update: { label: "Edited", icon: "edit" },
  complete: { label: "Marked done", icon: "check" },
  reopen: { label: "Reopened", icon: "refresh" },
  cancel: { label: "Cancelled", icon: "deny" },
  undo: { label: "Undid a change", icon: "reply" },
};

const STATE_CHANGE: Record<PersonTask["state"], { verb: string; doing: string; icon: IconName }> = {
  completed: { verb: "Completed", doing: "completing", icon: "check" },
  cancelled: { verb: "Cancelled", doing: "cancelling", icon: "deny" },
  open: { verb: "Reopened", doing: "reopening", icon: "refresh" },
};

type Draft = { title: string; details: string; dueDate: string };

function draftFrom(task: PersonTask): Draft {
  return { title: task.title, details: task.details ?? "", dueDate: task.due_date ?? "" };
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

/** Calendar arithmetic on YYYY-MM-DD, independent of the browser's time zone. */
function addDays(date: string, days: number): string {
  const next = new Date(`${date}T00:00:00Z`);
  if (Number.isNaN(next.getTime())) return date;
  next.setUTCDate(next.getUTCDate() + days);
  return next.toISOString().slice(0, 10);
}

/** "Today", "Tomorrow" or "Yesterday" near `today`, else "Oct 16" (with the year when it differs). */
export function dueText(date: string, today?: string): string {
  if (today) {
    if (date === today) return "Today";
    if (date === addDays(today, 1)) return "Tomorrow";
    if (date === addDays(today, -1)) return "Yesterday";
  }
  const parsed = new Date(`${date}T12:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return date;
  const year = (today ?? new Date().toISOString()).slice(0, 4);
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
    ...(date.startsWith(year) ? {} : { year: "numeric" }),
  });
}

function browserZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    return null;
  }
}

/** The task timezone, named only when it is not the one this computer uses. */
export function ZoneNote({ timezone }: { timezone?: string | null }) {
  const local = browserZone();
  if (!timezone || !local || timezone === local) return null;
  return (
    <p className="task-zone">
      <Glyph name="clock" />
      Dates use {timezone}
    </p>
  );
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
  return sources.length ? <span className="task-sources">{sources}</span> : null;
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
export function useOperationId() {
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

/** A toast for a saved change, with Undo when the server allows reversing it. */
export function changeToast(
  personId: string,
  message: string,
  icon: IconName,
  change: TaskSaveResult["change"] | undefined,
  undoLabel: string,
): UndoToastData {
  if (!change || change.can_undo === false) return { message, icon };
  const operationId = newOperationId();
  return {
    message,
    icon,
    undoLabel,
    onUndo: async () => {
      try {
        await undoTaskChange(personId, change.change_id, { operationId });
      } catch (failure) {
        if (failure instanceof TaskSaveError && failure.status === 409) {
          window.dispatchEvent(new Event(TASKS_CHANGED));
          throw new Error("Couldn’t undo. The task changed again.");
        }
        throw new Error("Couldn’t undo. Use Changes to try again.");
      }
      window.dispatchEvent(new Event(TASKS_CHANGED));
    },
  };
}

/** "Completed “Send deck”", with Undo. */
export function stateToast(
  task: Pick<PersonTask, "person_id" | "title">,
  state: PersonTask["state"],
  change: TaskSaveResult["change"] | undefined,
): UndoToastData {
  const copy = STATE_CHANGE[state];
  return changeToast(
    task.person_id,
    `${copy.verb} “${task.title}”`,
    copy.icon,
    change,
    `Undo ${copy.doing} ${task.title}`,
  );
}

/** Closes a popover on a pointer press outside `area`. Escape is handled by the popover. */
function useOutsideClose(area: RefObject<HTMLElement>, onOutside: (() => void) | null) {
  useEffect(() => {
    if (!onOutside) return;
    const press = (event: MouseEvent) => {
      if (area.current && !area.current.contains(event.target as Node)) onOutside();
    };
    document.addEventListener("mousedown", press);
    return () => document.removeEventListener("mousedown", press);
  }, [area, onOutside]);
}

/** Keeps Escape inside an inline editor from also closing the panel around it. */
function onEscape(event: KeyboardEvent, handle: () => void) {
  if (event.key !== "Escape") return;
  event.preventDefault();
  event.stopPropagation();
  handle();
}

/** The round checkbox: open → done, done or cancelled → open. */
export function TaskCheck({
  task,
  pending = null,
  disabled = false,
  onToggle,
}: {
  task: Pick<PersonTask, "title" | "state">;
  pending?: PersonTask["state"] | null;
  disabled?: boolean;
  onToggle: () => void;
}) {
  const state = pending ?? task.state;
  const open = task.state === "open";
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={state !== "open"}
      className={`task-check is-${state}`}
      aria-label={open ? `Mark task ${task.title} done` : `Reopen task ${task.title}`}
      title={open ? "Mark done" : "Reopen"}
      disabled={disabled}
      onClick={onToggle}
    >
      <span className="task-check-box" aria-hidden="true">
        <Icon name={state === "cancelled" ? "deny" : "check"} />
      </span>
    </button>
  );
}

const QUICK_DATES: { label: string; days: number }[] = [
  { label: "Today", days: 0 },
  { label: "Tomorrow", days: 1 },
  { label: "In a week", days: 7 },
];

/** Quick dates, an exact date, and Remove. The timezone shows only when it differs. */
function DuePicker({
  name,
  date,
  today,
  timezone,
  onPick,
  onClose,
}: {
  name: string;
  date: string | null;
  today?: string;
  timezone?: string;
  onPick: (date: string | null) => Promise<void> | void;
  onClose: (refocus: boolean) => void;
}) {
  const [value, setValue] = useState(date ?? "");
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    box.current?.querySelector<HTMLElement>("button, input")?.focus();
  }, []);

  async function pick(next: string | null) {
    if (busy) return;
    setBusy(true);
    try {
      await onPick(next);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      ref={box}
      className="crm-popover task-due-picker"
      role="dialog"
      aria-label={`Due date for ${name}`}
      onKeyDown={(event) => onEscape(event, () => onClose(true))}
    >
      {today ? (
        <div className="task-due-options">
          {QUICK_DATES.map((option) => {
            const day = addDays(today, option.days);
            return (
              <button
                key={option.label}
                type="button"
                className={`crm-menu-item${day === date ? " is-selected" : ""}`}
                disabled={busy}
                onClick={() => void pick(day)}
              >
                <Icon name="calendar" />
                <span>{option.label}</span>
                <span className="crm-menu-hint">{dueText(day)}</span>
              </button>
            );
          })}
        </div>
      ) : null}
      <div className="task-due-exact">
        <input
          type="date"
          aria-label="Due date"
          value={value}
          disabled={busy}
          onChange={(event) => setValue(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            void pick(value || null);
          }}
        />
        <button
          type="button"
          className="crm-icon-btn"
          aria-label="Set date"
          title="Set date"
          disabled={busy || !value}
          onClick={() => void pick(value || null)}
        >
          <Icon name="check" />
        </button>
      </div>
      {date ? (
        <button type="button" className="crm-menu-item" disabled={busy} onClick={() => void pick(null)}>
          <Icon name="close" />
          <span>Remove date</span>
        </button>
      ) : null}
      <ZoneNote timezone={timezone} />
    </div>
  );
}

/**
 * The due date as a tinted chip: warn when overdue, accent when today. With
 * `onPick` it opens the date picker; `onPick` resolves false to keep it open.
 */
export function DueChip({
  name = "this task",
  date,
  status,
  today,
  timezone,
  onPick,
  disabled = false,
  reveal = false,
}: {
  name?: string;
  date: string | null;
  status?: TaskDueStatus;
  today?: string;
  timezone?: string;
  onPick?: (date: string | null) => Promise<boolean> | boolean;
  disabled?: boolean;
  /** Undated chips on saved rows stay hidden until the row is hovered or focused. */
  reveal?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const area = useRef<HTMLSpanElement>(null);
  const chip = useRef<HTMLButtonElement>(null);
  const tone =
    status ?? (date && today ? (date < today ? "overdue" : date === today ? "today" : "upcoming") : "none");
  const text = date ? (tone === "today" && !today ? "Today" : dueText(date, today)) : null;
  const toneClass = tone === "overdue" ? " is-overdue" : tone === "today" ? " is-today" : "";
  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) requestAnimationFrame(() => chip.current?.focus());
  }, []);
  const closeOutside = useCallback(() => close(false), [close]);
  useOutsideClose(area, open ? closeOutside : null);

  if (!onPick) {
    return date && text ? (
      <span className={`task-due-chip${toneClass}`}>
        <Icon name="calendar" />
        <time dateTime={date}>{text}</time>
      </span>
    ) : null;
  }
  return (
    <span ref={area} className="task-due">
      <button
        ref={chip}
        type="button"
        className={`task-due-chip${toneClass}${date ? "" : " is-empty"}${!date && reveal ? " task-reveal" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={date ? `Due date for ${name}: ${text}` : `Set due date for ${name}`}
        title={date ? "Change due date" : "Set due date"}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="calendar" />
        {date ? <time dateTime={date}>{text}</time> : null}
      </button>
      {open ? (
        <DuePicker
          name={name}
          date={date}
          today={today}
          timezone={timezone}
          onClose={close}
          onPick={async (next) => {
            if (await onPick(next)) close(true);
          }}
        />
      ) : null}
    </span>
  );
}

type MenuItem = { label: string; icon: ReactNode; onSelect: () => void; danger?: boolean };

/** A row's secondary actions behind one icon button: arrows move, Escape closes. */
function RowMenu({ name, items }: { name: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false);
  const area = useRef<HTMLSpanElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const close = useCallback((refocus: boolean) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  }, []);
  const closeOutside = useCallback(() => close(false), [close]);
  useOutsideClose(area, open ? closeOutside : null);

  useEffect(() => {
    if (open) menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);

  function onMenuKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Tab") {
      close(false);
      return;
    }
    if (event.key === "Escape") {
      onEscape(event, () => close(true));
      return;
    }
    const entries = Array.from(
      menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? [],
    );
    if (entries.length === 0) return;
    const index = entries.indexOf(document.activeElement as HTMLElement);
    const next =
      event.key === "ArrowDown" ? (index + 1) % entries.length
        : event.key === "ArrowUp" ? (index - 1 + entries.length) % entries.length
          : event.key === "Home" ? 0
            : event.key === "End" ? entries.length - 1
              : -1;
    if (next < 0) return;
    event.preventDefault();
    entries[next].focus();
  }

  return (
    <span ref={area} className="task-menu">
      <button
        ref={trigger}
        type="button"
        className="crm-icon-btn task-reveal"
        aria-label={`More actions for ${name}`}
        title="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <Icon name="more" />
      </button>
      {open ? (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={`Actions for ${name}`}
          className="crm-popover crm-menu"
          onKeyDown={onMenuKey}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={`crm-menu-item${item.danger ? " is-danger" : ""}`}
              onClick={() => {
                close(true);
                item.onSelect();
              }}
            >
              {item.icon}
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      ) : null}
    </span>
  );
}

/** One edit against the version it started from; a conflict rebases onto the newer save. */
function useTaskPatch(task: PersonTask) {
  const [base, setBase] = useState<PersonTask>(task);
  const [newer, setNewer] = useState<PersonTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useOperationId();

  async function save(fields: Partial<TaskFields>): Promise<TaskSaveResult | "unchanged" | null> {
    if (busy) return null;
    const changes: Partial<TaskFields> = {};
    if (fields.title !== undefined && fields.title !== base.title) changes.title = fields.title;
    if (fields.details !== undefined && fields.details !== base.details) changes.details = fields.details;
    if (fields.due_date !== undefined && fields.due_date !== base.due_date) changes.due_date = fields.due_date;
    if (Object.keys(changes).length === 0) return "unchanged";
    setBusy(true);
    setError(null);
    try {
      const result = await updateTask(task.person_id, task.task_id, {
        changes,
        expectedVersion: base.version,
        operationId: operation.forPayload({ changes, version: base.version }),
      });
      operation.settle();
      window.dispatchEvent(new Event(TASKS_CHANGED));
      return result;
    } catch (failure) {
      if (failure instanceof TaskSaveError && failure.code === "task_conflict" && failure.current) {
        // Show the newer saved task and keep the director's unsaved text.
        operation.settle();
        setNewer(failure.current);
        setBase(failure.current);
      } else {
        setError(saveFailure(failure));
      }
      return null;
    } finally {
      setBusy(false);
    }
  }

  return { base, newer, busy, error, setError, save };
}

/** Click the title, type, Enter saves, Escape cancels. */
function TaskTitleEditor({
  task,
  onDone,
}: {
  task: PersonTask;
  onDone: (result: TaskSaveResult | null) => void;
}) {
  const [title, setTitle] = useState(task.title);
  const patch = useTaskPatch(task);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) {
      patch.setError("Give the task a title.");
      return;
    }
    const result = await patch.save({ title: title.trim() });
    if (result === "unchanged") onDone(null);
    else if (result) onDone(result);
  }

  return (
    <form
      className="task-form task-title-form"
      aria-label={`Edit title of ${task.title}`}
      onSubmit={(event) => void submit(event)}
      onKeyDown={(event) => onEscape(event, () => onDone(null))}
      onBlur={(event) => {
        // Leaving an unchanged title closes it; changed text stays until saved or cancelled.
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        if (!patch.busy && title.trim() === patch.base.title) onDone(null);
      }}
    >
      <input
        aria-label="Task title"
        value={title}
        maxLength={200}
        autoFocus
        disabled={patch.busy}
        onChange={(event) => setTitle(event.currentTarget.value)}
      />
      <button type="submit" className="crm-icon-btn" aria-label="Save title" title="Save (Enter)" disabled={patch.busy}>
        <Icon name="check" />
      </button>
      <button
        type="button"
        className="crm-icon-btn"
        aria-label="Cancel title edit"
        title="Cancel (Escape)"
        disabled={patch.busy}
        onClick={() => onDone(null)}
      >
        <Icon name="close" />
      </button>
      {patch.newer ? (
        <p className="crm-note" role="alert">
          <Glyph name="info" />
          Saved elsewhere as “{patch.newer.title}”. Enter replaces it with yours.
        </p>
      ) : null}
      {patch.error ? <p className="crm-alert" role="alert">{patch.error}</p> : null}
    </form>
  );
}

/** Title, description and due date together, opened from the row menu. */
function TaskEditor({
  task,
  today,
  timezone,
  onDone,
}: {
  task: PersonTask;
  today?: string;
  timezone?: string;
  onDone: (result: TaskSaveResult | null) => void;
}) {
  const [draft, setDraft] = useState<Draft>(draftFrom(task));
  const [armed, setArmed] = useState(false);
  const patch = useTaskPatch(task);
  const titleField = useRef<HTMLInputElement>(null);
  const fields = fieldsFrom(draft);
  const dirty =
    fields.title !== patch.base.title ||
    fields.details !== (patch.base.details ?? "") ||
    fields.due_date !== patch.base.due_date;

  useEffect(() => titleField.current?.focus(), []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!fields.title) {
      patch.setError("Give the task a title.");
      return;
    }
    const result = await patch.save(fields);
    if (result === "unchanged") onDone(null);
    else if (result) onDone(result);
  }

  function edit(next: Draft) {
    setArmed(false);
    setDraft(next);
  }

  return (
    <form
      className="task-form task-editor"
      aria-label={`Edit task ${task.title}`}
      onSubmit={(event) => void submit(event)}
      onKeyDown={(event) =>
        onEscape(event, () => {
          // Unchanged closes at once; changed text needs a second Escape.
          if (!dirty || armed) onDone(null);
          else setArmed(true);
        })
      }
    >
      {patch.newer ? (
        <div className="task-conflict" role="alert">
          <p>
            <Glyph name="info" />
            This task changed while you were editing. The saved version is now:
          </p>
          <p className="task-conflict-saved">
            <strong>{patch.newer.title}</strong>
            {patch.newer.due_date ? (
              <>
                {" · "}
                <time dateTime={patch.newer.due_date}>{dueText(patch.newer.due_date, today)}</time>
              </>
            ) : null}
          </p>
          {patch.newer.details ? <p>{patch.newer.details}</p> : null}
          <p>Save to replace it with your text, or cancel.</p>
        </div>
      ) : null}
      <input
        ref={titleField}
        className="task-editor-title"
        aria-label="Task title"
        placeholder="Task title"
        value={draft.title}
        maxLength={200}
        required
        disabled={patch.busy}
        onChange={(event) => edit({ ...draft, title: event.currentTarget.value })}
      />
      <textarea
        className="task-editor-details"
        aria-label="Description"
        placeholder="Add a description"
        rows={2}
        value={draft.details}
        maxLength={4000}
        disabled={patch.busy}
        onChange={(event) => edit({ ...draft, details: event.currentTarget.value })}
      />
      <div className="task-editor-foot">
        <DueChip
          name={task.title}
          date={draft.dueDate || null}
          today={today}
          timezone={timezone}
          disabled={patch.busy}
          onPick={(date) => {
            edit({ ...draft, dueDate: date ?? "" });
            return true;
          }}
        />
        <button type="button" className="crm-btn is-ghost" disabled={patch.busy} onClick={() => onDone(null)}>
          <Icon name="close" />
          Cancel
        </button>
        <button type="submit" className="crm-btn is-primary" disabled={patch.busy}>
          <Icon name="check" />
          {patch.busy ? "Saving…" : "Save"}
        </button>
      </div>
      {armed && dirty ? (
        <p className="crm-note" role="status">
          <Glyph name="info" />
          Press Escape again to discard your changes.
        </p>
      ) : null}
      {patch.error ? <p className="crm-alert" role="alert">{patch.error}</p> : null}
    </form>
  );
}

/** A new row at the top: title focused, optional due chip, Enter saves, Escape cancels. */
function TaskComposer({
  personId,
  people,
  today,
  timezone,
  onClose,
}: {
  personId?: string;
  people: BoardPerson[];
  today?: string;
  timezone?: string;
  onClose: () => void;
}) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState<string | null>(null);
  const [owner, setOwner] = useState(personId ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const operation = useOperationId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const fields: TaskFields = { title: title.trim(), details: "", due_date: due };
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
    <form
      className="task-form task-new"
      aria-label="New task"
      onSubmit={(event) => void submit(event)}
      onKeyDown={(event) => onEscape(event, onClose)}
    >
      <span className="task-check is-placeholder" aria-hidden="true">
        <span className="task-check-box" />
      </span>
      <input
        className="task-new-title"
        aria-label="Task title"
        placeholder="Task title"
        value={title}
        maxLength={200}
        autoFocus
        disabled={busy}
        onChange={(event) => setTitle(event.currentTarget.value)}
      />
      {personId ? null : (
        <select
          className="task-new-person"
          aria-label="Person"
          value={owner}
          disabled={busy}
          onChange={(event) => setOwner(event.currentTarget.value)}
        >
          <option value="">Choose a person</option>
          {people.map((person) => (
            <option key={person.person_id} value={person.person_id}>
              {[person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person"}
              {person.company ? ` · ${person.company}` : ""}
            </option>
          ))}
        </select>
      )}
      <span className="task-new-actions">
        <DueChip
          name="the new task"
          date={due}
          today={today}
          timezone={timezone}
          disabled={busy}
          onPick={(date) => {
            setDue(date);
            return true;
          }}
        />
        <button type="submit" className="crm-icon-btn is-primary" aria-label="Save task" title="Save (Enter)" disabled={busy}>
          <Icon name="check" />
        </button>
        <button
          type="button"
          className="crm-icon-btn"
          aria-label="Cancel new task"
          title="Cancel (Escape)"
          disabled={busy}
          onClick={onClose}
        >
          <Icon name="close" />
        </button>
      </span>
      {error ? <p className="crm-alert" role="alert">{error}</p> : null}
    </form>
  );
}

function changeTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value.slice(0, 16).replace("T", " ")
    : date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

/** The recorded changes of one task, newest first: icon, actor, time and Undo. */
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
      <div className="task-history-head">
        <Glyph name="clock" />
        <span>Changes</span>
        <button type="button" className="crm-icon-btn" aria-label="Hide changes" title="Hide changes" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      {changes === null && !failed ? <p className="task-quiet">Loading changes…</p> : null}
      {failed ? (
        <div className="crm-alert" role="alert">
          <span>Couldn’t load changes.</span>
          <button type="button" className="crm-btn" onClick={() => setAttempt((value) => value + 1)}>
            <Icon name="refresh" />
            Retry
          </button>
        </div>
      ) : null}
      {changes ? (
        <ol className="task-history-rows">
          {changes.map((change) => {
            const action = ACTION[change.action] ?? { label: change.action, icon: "edit" as IconName };
            return (
              <li key={change.change_id} className="task-history-row">
                <Icon name={action.icon} />
                <span className="task-history-copy">
                  <span className="task-history-line">
                    <strong>{action.label}</strong>
                    <span>{change.actor === "assistant" ? "Assistant" : "You"}</span>
                    <time dateTime={change.created_at}>{changeTime(change.created_at)}</time>
                  </span>
                  {change.reason && change.reason !== action.label ? <span>{change.reason}</span> : null}
                  <TaskSources links={change.source_links} />
                </span>
                {(change.can_undo ?? change.action !== "create") ? (
                  <button
                    type="button"
                    className="crm-btn is-ghost"
                    aria-label={`Undo ${action.label.toLowerCase()} on ${task.title}`}
                    disabled={busy !== null}
                    onClick={() => void undo(change)}
                  >
                    <Icon name="reply" />
                    {busy === change.change_id ? "Undoing…" : "Undo"}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ol>
      ) : null}
      {notice ? <p className="crm-alert" role="alert">{notice}</p> : null}
    </div>
  );
}

function TaskRow({
  task,
  showPerson,
  today,
  timezone,
  onToast,
}: {
  task: PersonTask;
  showPerson: boolean;
  today?: string;
  timezone?: string;
  onToast: (toast: UndoToastData, linger?: string) => void;
}) {
  const [editing, setEditing] = useState<"title" | "details" | null>(null);
  const [panel, setPanel] = useState<"history" | "draft" | null>(null);
  const [pending, setPending] = useState<PersonTask["state"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const titleButton = useRef<HTMLButtonElement>(null);
  const operation = useOperationId();
  const status = statusOf(task);
  const finished = task.state !== "open";

  // The optimistic tick stays until the reload brings the saved state.
  useEffect(() => setPending(null), [task.state, task.version]);

  function closeEditor(result: TaskSaveResult | null) {
    setEditing(null);
    requestAnimationFrame(() => titleButton.current?.focus());
    if (!result) return;
    const title = result.task?.title ?? task.title;
    onToast(changeToast(task.person_id, `Saved “${title}”`, "save", result.change, `Undo the edit to ${title}`));
  }

  async function changeState(state: PersonTask["state"]) {
    if (busy) return;
    setBusy(true);
    setPending(state);
    setNotice(null);
    try {
      const result = await updateTask(task.person_id, task.task_id, {
        changes: { state },
        expectedVersion: task.version,
        operationId: operation.forPayload({ task: task.task_id, state, version: task.version }),
      });
      operation.settle();
      onToast(stateToast(task, state, result?.change), task.task_id);
      window.dispatchEvent(new Event(TASKS_CHANGED));
    } catch (failure) {
      setPending(null);
      if (failure instanceof TaskSaveError && failure.code === "task_conflict") {
        operation.settle();
        window.dispatchEvent(new Event(TASKS_CHANGED));
      }
      setNotice(saveFailure(failure));
    } finally {
      setBusy(false);
    }
  }

  async function saveDue(date: string | null): Promise<boolean> {
    if (date === task.due_date) return true;
    if (busy) return false;
    setBusy(true);
    setNotice(null);
    try {
      const result = await updateTask(task.person_id, task.task_id, {
        changes: { due_date: date },
        expectedVersion: task.version,
        operationId: operation.forPayload({ task: task.task_id, due_date: date, version: task.version }),
      });
      operation.settle();
      const text = date ? dueText(date, today) : null;
      const message = text
        ? `Due ${/^(Today|Tomorrow|Yesterday)$/.test(text) ? text.toLowerCase() : text}`
        : "Due date removed";
      onToast(changeToast(task.person_id, message, "calendar", result?.change, `Undo the due date change on ${task.title}`));
      window.dispatchEvent(new Event(TASKS_CHANGED));
      return true;
    } catch (failure) {
      const conflict = failure instanceof TaskSaveError && failure.code === "task_conflict";
      if (conflict) {
        operation.settle();
        window.dispatchEvent(new Event(TASKS_CHANGED));
      }
      setNotice(saveFailure(failure));
      return conflict;
    } finally {
      setBusy(false);
    }
  }

  const toggle = (next: "history" | "draft") => setPanel((value) => (value === next ? null : next));
  const changesItem: MenuItem = { label: "Changes", icon: <Glyph name="clock" />, onSelect: () => toggle("history") };
  const items: MenuItem[] = finished
    ? [{ label: "Reopen", icon: <Icon name="refresh" />, onSelect: () => void changeState("open") }, changesItem]
    : [
        { label: "Edit details", icon: <Icon name="edit" />, onSelect: () => setEditing("details") },
        changesItem,
        { label: "Local draft text", icon: <Icon name="draft" />, onSelect: () => toggle("draft") },
        { label: "Cancel task", icon: <Icon name="deny" />, danger: true, onSelect: () => void changeState("cancelled") },
      ];
  const sources = <TaskSources links={task.source_links} />;

  return (
    <li className={`task-row is-${finished ? "finished" : status} is-state-${pending ?? task.state}`}>
      <div className="task-row-line">
        <TaskCheck
          task={task}
          pending={pending}
          disabled={busy}
          onToggle={() => void changeState(finished ? "open" : "completed")}
        />
        <div className="task-row-copy">
          {editing === "title" ? (
            <TaskTitleEditor task={task} onDone={closeEditor} />
          ) : finished ? (
            <span className="task-title">{task.title}</span>
          ) : (
            <button
              ref={titleButton}
              type="button"
              className="task-title"
              aria-label={`Edit title: ${task.title}`}
              title="Edit title"
              disabled={busy}
              onClick={() => setEditing("title")}
            >
              {task.title}
            </button>
          )}
          {task.details && editing !== "details" ? <span className="task-details">{task.details}</span> : null}
          {showPerson || task.source_links?.length ? (
            <span className="task-meta">
              {showPerson ? (
                <a href={`#/people/${encodeURIComponent(task.person_id)}`}>{taskPersonName(task)}</a>
              ) : null}
              {sources}
            </span>
          ) : null}
        </div>
        <div className="task-row-trail">
          {task.origin === "assistant" ? (
            <span className="task-origin" title="Added by the assistant">
              <Icon name="assistant" />
              Assistant
            </span>
          ) : null}
          {task.state === "cancelled" ? <span className="task-state-pill">Cancelled</span> : null}
          <DueChip
            name={task.title}
            date={task.due_date}
            // A finished task's date is history, so it never shows the overdue tint.
            status={finished ? "upcoming" : status}
            today={today}
            timezone={timezone}
            onPick={finished ? undefined : saveDue}
            disabled={busy}
            reveal
          />
          {finished ? null : (
            <a
              id={`task-prepare-${task.task_id}`}
              className="crm-icon-btn task-reveal task-row-prepare"
              href={followUpHash(task.person_id, task.task_id)}
              aria-label={`Prepare a follow-up draft for ${task.title}`}
              title="Prepare draft"
            >
              <Icon name="draft" />
            </a>
          )}
          <RowMenu name={task.title} items={items} />
        </div>
      </div>
      {notice ? (
        <p className="crm-alert task-row-notice" role="alert">
          <Glyph name="info" />
          {notice}
        </p>
      ) : null}
      {editing === "details" ? (
        <TaskEditor task={task} today={today} timezone={timezone} onDone={closeEditor} />
      ) : null}
      {panel === "history" ? <TaskHistory task={task} onClose={() => setPanel(null)} /> : null}
      {panel === "draft" && !finished ? (
        <div className="task-local-draft" role="group" aria-label={`Local draft text for ${task.title}`}>
          <TaskDraft task={task} />
          <button
            type="button"
            className="crm-icon-btn"
            aria-label="Close local draft text"
            title="Close"
            onClick={() => setPanel(null)}
          >
            <Icon name="close" />
          </button>
        </div>
      ) : null}
    </li>
  );
}

/**
 * Open tasks, grouped Overdue / Today / Upcoming / No date, with completed
 * and cancelled tasks behind one Completed toggle.
 *
 * With `personId` it is that person's list; without it, the global list in
 * Contacts. Both read the same saved records, and a save in either reloads
 * every list on screen. A task completed here stays in place, struck through,
 * while its Undo toast shows.
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
  const [pages, setPages] = useState(1);
  const [showFinished, setShowFinished] = useState(false);
  const [lingering, setLingering] = useState<string | null>(null);
  const { toast, show, dismiss } = useUndoToast();
  const addButton = useRef<HTMLButtonElement>(null);
  const Heading = `h${headingLevel}` as "h2" | "h3";
  const GroupHeading = `h${headingLevel + 1}` as "h3" | "h4";
  const headingId = `tasks-heading-${personId ?? "all"}`;

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all(Array.from({ length: pages }, (_, page) =>
      listTasks({ ...(personId ? { personId } : {}), state: "all", offset: page * 200, limit: 200 }),
    )).then(
      (results) => {
        if (!active) return;
        const next = { ...results[0], tasks: [...new Map(results.flatMap(r => r.tasks).map(task => [task.task_id, task])).values()], truncated: results[results.length - 1].truncated };
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
  }, [attempt, personId, pages]);

  useEffect(() => {
    const reload = () => setAttempt((value) => value + 1);
    window.addEventListener(TASKS_CHANGED, reload);
    window.addEventListener("sourcecado:board-changed", reload);
    const timer = window.setInterval(reload, 15000);
    return () => {
      window.removeEventListener(TASKS_CHANGED, reload);
      window.removeEventListener("sourcecado:board-changed", reload);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!toast) setLingering(null);
  }, [toast]);

  const notify = useCallback(
    (data: UndoToastData, linger?: string) => {
      show(data);
      setLingering(linger ?? null);
    },
    [show],
  );

  const showPerson = !personId;
  const today = listing?.today;
  const timezone = listing?.timezone;
  const matching = (listing?.tasks ?? []).filter((task) => (matches ? matches(task) : true));
  const tasks = matching.filter((task) => task.state === "open" || task.task_id === lingering);
  const finished = matching
    .filter((task) => task.state !== "open" && task.task_id !== lingering)
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  const openCount = (listing?.tasks ?? []).filter((task) => task.state === "open").length;
  const filteredOut = listing !== null && listing.tasks.length > 0 && matching.length === 0;
  const row = (task: PersonTask) => (
    <TaskRow
      key={task.task_id}
      task={task}
      showPerson={showPerson}
      today={today}
      timezone={timezone}
      onToast={notify}
    />
  );

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
            className="crm-btn is-ghost"
            onClick={() => setAdding(true)}
            disabled={!personId && people.length === 0}
          >
            <Icon name="add" />
            Add task
          </button>
        )}
      </div>
      {adding ? (
        <TaskComposer
          personId={personId}
          people={people}
          today={today}
          timezone={timezone}
          onClose={() => {
            setAdding(false);
            requestAnimationFrame(() => addButton.current?.focus());
          }}
        />
      ) : null}
      {listing === null && loading ? (
        <p className="task-quiet" role="status">
          Loading tasks…
        </p>
      ) : null}
      {failed ? (
        <div className="crm-alert task-list-error" role="alert">
          <span>
            {listing === null
              ? "Couldn’t load tasks."
              : "Couldn’t refresh tasks. Showing what was last loaded."}
          </span>
          <button type="button" className="crm-btn" onClick={() => setAttempt((value) => value + 1)}>
            <Icon name="refresh" />
            Retry
          </button>
        </div>
      ) : null}
      {listing !== null && openCount === 0 && tasks.length === 0 ? (
        <p className="task-empty" role="status">
          <Glyph name="review" />
          No open tasks
        </p>
      ) : null}
      {filteredOut ? (
        <p className="task-empty" role="status">
          <Icon name="search" />
          No tasks match these filters
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
              <li key={`group-${group.status}`} className={`task-group-label is-${group.status}`}>
                <GroupHeading>{group.label}</GroupHeading>
                <span className="task-group-count">{inGroup.length}</span>
              </li>,
              ...inGroup.map(row),
            ];
          })}
        </ul>
      ) : null}
      {finished.length > 0 ? (
        <div className="task-finished">
          <button
            type="button"
            className="task-finished-toggle"
            aria-expanded={showFinished}
            onClick={() => setShowFinished((value) => !value)}
          >
            <Glyph name="chevron" />
            <span>Completed</span>
            <span className="task-group-count">{finished.length}</span>
          </button>
          {showFinished ? <ul className="task-rows">{finished.map(row)}</ul> : null}
        </div>
      ) : null}
      {listing?.truncated ? (
        <p className="task-more">
          <span>Showing the first {listing.tasks.length} tasks.</span>
          <button type="button" className="crm-btn" disabled={loading} onClick={() => setPages((n) => n + 1)}>
            <Icon name="add" />
            Load more tasks
          </button>
        </p>
      ) : null}
      <UndoToast toast={toast} onDismiss={dismiss} />
    </section>
  );
}
