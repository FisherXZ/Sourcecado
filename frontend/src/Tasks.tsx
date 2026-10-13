import { useEffect, useMemo, useRef, useState } from "react";

import {
  addMember,
  createTask,
  getBoard,
  getMembers,
  getTasks,
  setPersonOwner,
  TaskConflictError,
  updateTask,
  type BoardPerson,
  type Member,
  type Task,
} from "./api";

// ---- date helpers (due_date is a date-only 'YYYY-MM-DD' string) ----

function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function todayISO(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function addDaysISO(days: number): string {
  const now = new Date();
  now.setDate(now.getDate() + days);
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

type Bucket = "overdue" | "today" | "upcoming" | "nodate";

const BUCKET_ORDER: Bucket[] = ["overdue", "today", "upcoming", "nodate"];
const BUCKET_LABEL: Record<Bucket, string> = {
  overdue: "Overdue",
  today: "Today",
  upcoming: "Upcoming",
  nodate: "No date",
};

function bucketOf(due: string | null): Bucket {
  if (!due) return "nodate";
  const today = todayISO();
  if (due < today) return "overdue";
  if (due === today) return "today";
  return "upcoming";
}

function dueLabel(due: string | null): { text: string; cls: Bucket } {
  if (!due) return { text: "No date", cls: "nodate" };
  const bucket = bucketOf(due);
  if (bucket === "today") return { text: "Today", cls: "today" };
  const date = new Date(`${due}T00:00:00`);
  const text = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  if (bucket === "overdue") return { text: `${text} · overdue`, cls: "overdue" };
  return { text, cls: "upcoming" };
}

function groupByBucket(tasks: Task[]): Record<Bucket, Task[]> {
  const groups: Record<Bucket, Task[]> = {
    overdue: [],
    today: [],
    upcoming: [],
    nodate: [],
  };
  // Tasks arrive already ordered deterministically from the server; keep it.
  for (const task of tasks) groups[bucketOf(task.due_date)].push(task);
  return groups;
}

function personLabel(person: BoardPerson): string {
  return [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown";
}

// ---- task row ----

function TaskRow({
  task,
  showPerson,
  completing,
  onToggle,
  onEdit,
}: {
  task: Task;
  showPerson: boolean;
  completing: boolean;
  onToggle: (task: Task) => void;
  onEdit: (task: Task) => void;
}) {
  const done = task.state !== "open";
  const due = dueLabel(task.due_date);
  return (
    <li className={`task-row${completing ? " task-row-completing" : ""}${done ? " task-row-done" : ""}`}>
      <input
        type="checkbox"
        className="task-check"
        checked={done}
        aria-label={`${done ? "Reopen" : "Complete"} task: ${task.title}`}
        onChange={() => onToggle(task)}
      />
      <div className="task-row-main">
        <button type="button" className="task-row-title" onClick={() => onEdit(task)}>
          {task.title}
        </button>
        <div className="task-row-meta">
          {showPerson ? <span className="task-row-person">{task.person_name}</span> : null}
          {!done ? (
            <span className={`task-due task-due-${due.cls}`}>{due.text}</span>
          ) : (
            <span className="task-due task-due-nodate">done</span>
          )}
          {task.assignee_name ? (
            <span className="task-assignee">@{task.assignee_name}</span>
          ) : null}
        </div>
      </div>
      <button
        type="button"
        className="task-edit-btn"
        aria-label={`Edit task: ${task.title}`}
        onClick={() => onEdit(task)}
      >
        Edit
      </button>
    </li>
  );
}

function TaskGroups({
  tasks,
  showPerson,
  completing,
  emptyText,
  onToggle,
  onEdit,
}: {
  tasks: Task[];
  showPerson: boolean;
  completing: Set<string>;
  emptyText: string;
  onToggle: (task: Task) => void;
  onEdit: (task: Task) => void;
}) {
  const groups = useMemo(() => groupByBucket(tasks), [tasks]);
  const hasAny = tasks.length > 0;
  if (!hasAny) {
    return <p className="tasks-empty">{emptyText}</p>;
  }
  return (
    <>
      {BUCKET_ORDER.map((bucket) =>
        groups[bucket].length ? (
          <div key={bucket} className="task-group">
            <div className="task-group-head">
              <span className={`task-group-label task-group-${bucket}`}>
                {BUCKET_LABEL[bucket]}
              </span>
              <span className="task-group-count">{groups[bucket].length}</span>
            </div>
            <ul className="task-list">
              {groups[bucket].map((task) => (
                <TaskRow
                  key={task.task_id}
                  task={task}
                  showPerson={showPerson}
                  completing={completing.has(task.task_id)}
                  onToggle={onToggle}
                  onEdit={onEdit}
                />
              ))}
            </ul>
          </div>
        ) : null,
      )}
    </>
  );
}

// ---- new / edit task modal (structured form) ----

type ModalTarget =
  | { mode: "create"; fixedPerson?: BoardPerson }
  | { mode: "edit"; task: Task };

function TaskModal({
  target,
  people,
  members,
  onClose,
  onSaved,
  onAddMember,
}: {
  target: ModalTarget;
  people: BoardPerson[];
  members: Member[];
  onClose: () => void;
  onSaved: () => void;
  onAddMember: (name: string) => Promise<Member | null>;
}) {
  const editing = target.mode === "edit";
  const initialPerson =
    target.mode === "edit"
      ? target.task.person_id
      : target.fixedPerson?.person_id ?? people[0]?.person_id ?? "";
  const [personId, setPersonId] = useState(initialPerson);
  const [title, setTitle] = useState(editing ? target.task.title : "");
  const [details, setDetails] = useState(
    editing ? target.task.details ?? "" : "",
  );
  const [dueDate, setDueDate] = useState<string | null>(
    editing ? target.task.due_date : null,
  );
  const [assignee, setAssignee] = useState<string>(
    editing ? target.task.assignee_member_id ?? "" : "",
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const presets: { label: string; value: string | null }[] = [
    { label: "Today", value: todayISO() },
    { label: "Tomorrow", value: addDaysISO(1) },
    { label: "Next week", value: addDaysISO(7) },
    { label: "No date", value: null },
  ];

  async function handleAddMember() {
    const name = window.prompt("New committee member name");
    if (!name) return;
    const created = await onAddMember(name);
    if (created) setAssignee(created.member_id);
  }

  async function save() {
    if (!title.trim()) {
      setError("A task needs a title.");
      titleRef.current?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (target.mode === "create") {
        await createTask(personId, {
          title: title.trim(),
          details: details.trim() || null,
          due_date: dueDate,
          due_timezone: dueDate ? localTimezone() : null,
          assignee_member_id: assignee || null,
        });
      } else {
        await updateTask(target.task.person_id, target.task.task_id, {
          expectedVersion: target.task.version,
          title: title.trim(),
          details: details.trim() || null,
          due_date: dueDate,
          due_timezone: dueDate ? localTimezone() : null,
          assignee_member_id: assignee || null,
        });
      }
      onSaved();
    } catch (err) {
      // On a conflict (or any failure) keep the dialog open so typed text
      // is never lost; the operator can retry or cancel.
      setSaving(false);
      setError(
        err instanceof TaskConflictError
          ? "This task changed elsewhere. Your text is kept — reopen to see the latest, or save again."
          : err instanceof Error
            ? err.message
            : "Could not save the task.",
      );
    }
  }

  return (
    <div
      className="task-modal-scrim"
      role="presentation"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="task-modal"
        role="dialog"
        aria-modal="true"
        aria-label={editing ? "Edit task" : "New task"}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <h2>{editing ? "Edit task" : "New task"}</h2>
        <label className="task-field">
          <span>Title</span>
          <input
            ref={titleRef}
            type="text"
            value={title}
            onChange={(event) => setTitle(event.currentTarget.value)}
            placeholder="e.g. Send project examples"
          />
        </label>
        <label className="task-field">
          <span>Person</span>
          <select
            value={personId}
            disabled={editing || Boolean(target.mode === "create" && target.fixedPerson)}
            onChange={(event) => setPersonId(event.currentTarget.value)}
          >
            {people.map((person) => (
              <option key={person.person_id} value={person.person_id}>
                {personLabel(person)}
              </option>
            ))}
          </select>
        </label>
        <div className="task-field">
          <span>Due date · optional</span>
          <div className="task-presets" role="group" aria-label="Due date">
            {presets.map((preset) => (
              <button
                key={preset.label}
                type="button"
                aria-pressed={dueDate === preset.value}
                onClick={() => setDueDate(preset.value)}
              >
                {preset.label}
              </button>
            ))}
            <input
              type="date"
              aria-label="Pick a due date"
              value={dueDate ?? ""}
              onChange={(event) => setDueDate(event.currentTarget.value || null)}
            />
          </div>
        </div>
        <label className="task-field">
          <span>Assigned to · optional</span>
          <div className="task-assignee-row">
            <select
              value={assignee}
              onChange={(event) => setAssignee(event.currentTarget.value)}
            >
              <option value="">Unassigned</option>
              {members.map((member) => (
                <option key={member.member_id} value={member.member_id}>
                  {member.name}
                </option>
              ))}
            </select>
            <button type="button" className="task-link" onClick={() => void handleAddMember()}>
              + Add member
            </button>
          </div>
        </label>
        <label className="task-field">
          <span>Details · optional</span>
          <textarea
            value={details}
            onChange={(event) => setDetails(event.currentTarget.value)}
            rows={2}
          />
        </label>
        {error ? (
          <p className="task-modal-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="task-modal-actions">
          <button type="button" className="task-btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="task-btn-primary" disabled={saving} onClick={() => void save()}>
            {saving ? "Saving…" : editing ? "Save changes" : "Save task"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- shared completion handling ----

type UndoTarget = { personId: string; taskId: string; title: string; version: number };

function useTaskActions(reload: () => void) {
  const [completing, setCompleting] = useState<Set<string>>(new Set());
  const [undoTarget, setUndoTarget] = useState<UndoTarget | null>(null);
  const clearTimer = useRef<number | undefined>(undefined);

  function scheduleUndoClear() {
    window.clearTimeout(clearTimer.current);
    clearTimer.current = window.setTimeout(() => setUndoTarget(null), 7000);
  }

  function toggle(task: Task) {
    if (task.state === "open") {
      // Linger with a strike-through, then move it to completed.
      setCompleting((prev) => new Set(prev).add(task.task_id));
      window.setTimeout(() => {
        void (async () => {
          try {
            const updated = await updateTask(task.person_id, task.task_id, {
              expectedVersion: task.version,
              state: "completed",
            });
            // Offer a one-click undo against the completed task's new version.
            setUndoTarget({
              personId: task.person_id,
              taskId: task.task_id,
              title: task.title,
              version: updated.version,
            });
            scheduleUndoClear();
          } catch {
            // Leave the task open; the reload below reflects the server truth.
          } finally {
            setCompleting((prev) => {
              const next = new Set(prev);
              next.delete(task.task_id);
              return next;
            });
            reload();
          }
        })();
      }, 600);
    } else {
      void updateTask(task.person_id, task.task_id, {
        expectedVersion: task.version,
        state: "open",
      })
        .catch(() => undefined)
        .finally(reload);
    }
  }

  function undo() {
    if (!undoTarget) return;
    const target = undoTarget;
    setUndoTarget(null);
    window.clearTimeout(clearTimer.current);
    void updateTask(target.personId, target.taskId, {
      expectedVersion: target.version,
      state: "open",
    })
      .catch(() => undefined)
      .finally(reload);
  }

  return { completing, toggle, undoTarget, undo };
}

function UndoToast({ title, onUndo }: { title: string; onUndo: () => void }) {
  return (
    <div className="task-toast" role="status">
      <span>
        Completed “{title}”
      </span>
      <button type="button" onClick={onUndo}>
        Undo
      </button>
    </div>
  );
}

// ---- global two-column Tasks view ----

export function TasksView() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [people, setPeople] = useState<BoardPerson[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [selectedMember, setSelectedMember] = useState<string>("");
  const [modal, setModal] = useState<ModalTarget | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);
  const [failed, setFailed] = useState(false);

  const reload = () => {
    getTasks().then(
      (next) => setTasks(next),
      () => setFailed(true),
    );
  };

  useEffect(() => {
    reload();
    getMembers().then(setMembers, () => undefined);
    getBoard().then(
      (board) => setPeople([...board.open, ...board.in_conversation, ...board.done]),
      () => undefined,
    );
  }, []);

  const { completing, toggle, undoTarget, undo } = useTaskActions(reload);

  async function handleAddMember(name: string): Promise<Member | null> {
    try {
      const member = await addMember(name);
      setMembers((prev) =>
        prev.some((m) => m.member_id === member.member_id) ? prev : [...prev, member],
      );
      return member;
    } catch {
      return null;
    }
  }

  if (failed) {
    return (
      <section className="route-error" role="alert">
        <p>Couldn’t load tasks.</p>
        <button type="button" onClick={reload}>
          Retry
        </button>
      </section>
    );
  }
  if (tasks === null) {
    return <p role="status">Loading tasks…</p>;
  }

  const open = tasks.filter((task) => task.state === "open");
  const completed = tasks.filter((task) => task.state === "completed");
  const assigned = selectedMember
    ? open.filter((task) => task.assignee_member_id === selectedMember)
    : [];

  return (
    <section className="tasks-view" aria-label="Tasks">
      <div className="tasks-toolbar">
        <button
          type="button"
          className="task-btn-primary"
          onClick={() => setModal({ mode: "create" })}
        >
          + New task
        </button>
      </div>
      <div className="tasks-columns">
        <div className="tasks-column" aria-label="All tasks">
          <h2 className="tasks-column-head">All tasks</h2>
          <p className="tasks-column-sub">What everyone on the committee can see.</p>
          <TaskGroups
            tasks={open}
            showPerson
            completing={completing}
            emptyText="No open tasks. Nothing needs attention right now."
            onToggle={toggle}
            onEdit={(task) => setModal({ mode: "edit", task })}
          />
        </div>
        <div className="tasks-column" aria-label="Assigned tasks">
          <div className="tasks-column-head tasks-column-head-row">
            <span>Assigned to</span>
            <select
              aria-label="Filter by member"
              value={selectedMember}
              onChange={(event) => setSelectedMember(event.currentTarget.value)}
            >
              <option value="">Choose a member…</option>
              {members.map((member) => (
                <option key={member.member_id} value={member.member_id}>
                  {member.name}
                </option>
              ))}
            </select>
          </div>
          {selectedMember ? (
            <TaskGroups
              tasks={assigned}
              showPerson
              completing={completing}
              emptyText="No tasks assigned to this member."
              onToggle={toggle}
              onEdit={(task) => setModal({ mode: "edit", task })}
            />
          ) : (
            <p className="tasks-empty">Pick a committee member to see their tasks.</p>
          )}
        </div>
      </div>

      {completed.length ? (
        <div className="tasks-completed">
          <button
            type="button"
            className="task-link"
            aria-expanded={showCompleted}
            onClick={() => setShowCompleted((value) => !value)}
          >
            {showCompleted ? "Hide" : "Show"} completed ({completed.length})
          </button>
          {showCompleted ? (
            <ul className="task-list">
              {completed.map((task) => (
                <TaskRow
                  key={task.task_id}
                  task={task}
                  showPerson
                  completing={false}
                  onToggle={toggle}
                  onEdit={(t) => setModal({ mode: "edit", task: t })}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {modal ? (
        <TaskModal
          target={modal}
          people={people}
          members={members}
          onClose={() => setModal(null)}
          onSaved={() => {
            setModal(null);
            reload();
          }}
          onAddMember={handleAddMember}
        />
      ) : null}

      {undoTarget ? <UndoToast title={undoTarget.title} onUndo={undo} /> : null}
    </section>
  );
}

// ---- owner control (person file) ----

export function PersonOwner({
  personId,
  ownerMemberId,
  version,
  onChanged,
}: {
  personId: string;
  ownerMemberId: string | null;
  version: number;
  onChanged: () => void;
}) {
  const [members, setMembers] = useState<Member[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMembers().then(setMembers, () => undefined);
  }, []);

  async function change(next: string) {
    setSaving(true);
    setError(null);
    try {
      await setPersonOwner(personId, next || null, version);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not set owner.");
    } finally {
      setSaving(false);
    }
  }

  async function addNew() {
    const name = window.prompt("New committee member name");
    if (!name) return;
    try {
      const member = await addMember(name);
      setMembers((prev) =>
        prev.some((m) => m.member_id === member.member_id) ? prev : [...prev, member],
      );
      await change(member.member_id);
    } catch {
      setError("Could not add member.");
    }
  }

  return (
    <section className="person-section person-owner" aria-labelledby="person-owner-heading">
      <h2 id="person-owner-heading">Owner</h2>
      <div className="task-assignee-row">
        <select
          aria-label="Contact owner"
          value={ownerMemberId ?? ""}
          disabled={saving}
          onChange={(event) => void change(event.currentTarget.value)}
        >
          <option value="">Unassigned</option>
          {members.map((member) => (
            <option key={member.member_id} value={member.member_id}>
              {member.name}
            </option>
          ))}
        </select>
        <button type="button" className="task-link" onClick={() => void addNew()}>
          + Add member
        </button>
      </div>
      {error ? (
        <p className="person-sequence-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

// ---- per-person task list (person file) ----

export function PersonTasks({ person }: { person: BoardPerson }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [modal, setModal] = useState<ModalTarget | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);

  const reload = () => {
    getTasks({ personId: person.person_id }).then(setTasks, () => setTasks([]));
  };

  useEffect(() => {
    reload();
    getMembers().then(setMembers, () => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [person.person_id]);

  const { completing, toggle, undoTarget, undo } = useTaskActions(reload);

  async function handleAddMember(name: string): Promise<Member | null> {
    try {
      const member = await addMember(name);
      setMembers((prev) =>
        prev.some((m) => m.member_id === member.member_id) ? prev : [...prev, member],
      );
      return member;
    } catch {
      return null;
    }
  }

  const open = (tasks ?? []).filter((task) => task.state === "open");
  const completed = (tasks ?? []).filter((task) => task.state === "completed");

  return (
    <section className="person-section" aria-labelledby="person-tasks-heading">
      <div className="person-tasks-head">
        <h2 id="person-tasks-heading">Tasks</h2>
        <button
          type="button"
          className="task-btn-ghost"
          onClick={() => setModal({ mode: "create", fixedPerson: person })}
        >
          + Add task
        </button>
      </div>
      {tasks === null ? (
        <p role="status">Loading tasks…</p>
      ) : (
        <TaskGroups
          tasks={open}
          showPerson={false}
          completing={completing}
          emptyText="No open tasks for this person yet."
          onToggle={toggle}
          onEdit={(task) => setModal({ mode: "edit", task })}
        />
      )}
      {completed.length ? (
        <div className="tasks-completed">
          <button
            type="button"
            className="task-link"
            aria-expanded={showCompleted}
            onClick={() => setShowCompleted((value) => !value)}
          >
            {showCompleted ? "Hide" : "Show"} completed ({completed.length})
          </button>
          {showCompleted ? (
            <ul className="task-list">
              {completed.map((task) => (
                <TaskRow
                  key={task.task_id}
                  task={task}
                  showPerson={false}
                  completing={false}
                  onToggle={toggle}
                  onEdit={(t) => setModal({ mode: "edit", task: t })}
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {modal ? (
        <TaskModal
          target={modal}
          people={[person]}
          members={members}
          onClose={() => setModal(null)}
          onSaved={() => {
            setModal(null);
            reload();
          }}
          onAddMember={handleAddMember}
        />
      ) : null}

      {undoTarget ? <UndoToast title={undoTarget.title} onUndo={undo} /> : null}
    </section>
  );
}
