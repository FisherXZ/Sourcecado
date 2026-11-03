import { useEffect, useState } from "react";

import { Glyph } from "../generative/ResultCard";
import { UndoToast, useUndoToast } from "../crm/UndoToast";
import { DueChip, stateToast, TaskCheck, TASKS_CHANGED, useOperationId } from "../Tasks";
import { listTasks, TaskSaveError, updateTask, type PersonTask } from "../api";

/** This person's open tasks, soonest first. Null until the first read lands. */
export function useOpenTasks(personId: string): PersonTask[] | null {
  const [tasks, setTasks] = useState<PersonTask[] | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    // The same read the Tasks tab makes, so the badge and list agree.
    listTasks({ personId, state: "all", offset: 0, limit: 200 })
      .then((listing) => {
        if (!active) return;
        const open = (Array.isArray(listing?.tasks) ? listing.tasks : []).filter(
          (task) => task.state === "open",
        );
        open.sort(
          (a, b) =>
            (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999") ||
            (a.created_at ?? "").localeCompare(b.created_at ?? ""),
        );
        setTasks(open);
      })
      .catch(() => {
        /* The Tasks tab shows its own load error and retry. */
      });
    return () => {
      active = false;
    };
  }, [attempt, personId]);
  useEffect(() => {
    setTasks(null);
  }, [personId]);
  useEffect(() => {
    const reload = () => setAttempt((value) => value + 1);
    window.addEventListener(TASKS_CHANGED, reload);
    window.addEventListener("sourcecado:board-changed", reload);
    return () => {
      window.removeEventListener(TASKS_CHANGED, reload);
      window.removeEventListener("sourcecado:board-changed", reload);
    };
  }, []);
  return tasks;
}

/** The next three open tasks: tick one to complete it, with Undo. */
export function NextTasks({
  tasks,
  onShowAll,
}: {
  tasks: PersonTask[] | null;
  onShowAll: () => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const { toast, show, dismiss } = useUndoToast();
  const operation = useOperationId();

  // The tick stays until the reload drops the completed task.
  useEffect(() => setPending(null), [tasks]);

  async function complete(task: PersonTask) {
    if (pending) return;
    setPending(task.task_id);
    setProblem(null);
    try {
      const result = await updateTask(task.person_id, task.task_id, {
        changes: { state: "completed" },
        expectedVersion: task.version,
        operationId: operation.forPayload({ task: task.task_id, state: "completed", version: task.version }),
      });
      operation.settle();
      show(stateToast(task, "completed", result?.change));
    } catch (failure) {
      setPending(null);
      if (failure instanceof TaskSaveError && failure.code === "task_conflict") operation.settle();
      setProblem("Couldn’t complete the task. It changed or the save failed; try again.");
    } finally {
      window.dispatchEvent(new Event(TASKS_CHANGED));
    }
  }

  return (
    <section className="person-section" aria-labelledby="person-next-tasks-heading">
      <div className="person-section-heading-row">
        <h2 id="person-next-tasks-heading">Next tasks</h2>
        <button type="button" className="pf-btn pf-btn-quiet" onClick={onShowAll}>
          <Glyph name="review" />
          All tasks
        </button>
      </div>
      {tasks === null ? null : tasks.length === 0 ? (
        <p className="person-empty">No open tasks.</p>
      ) : (
        <ul className="pf-next-tasks">
          {tasks.slice(0, 3).map((task) => (
            <li key={task.task_id} className={pending === task.task_id ? "is-done" : undefined}>
              <TaskCheck
                task={task}
                pending={pending === task.task_id ? "completed" : null}
                disabled={pending !== null}
                onToggle={() => void complete(task)}
              />
              <span className="pf-next-task-title">{task.title}</span>
              <DueChip date={task.due_date} status={task.due_status} />
            </li>
          ))}
        </ul>
      )}
      {problem ? (
        <p className="crm-alert" role="alert">
          <Glyph name="info" />
          {problem}
        </p>
      ) : null}
      <UndoToast toast={toast} onDismiss={dismiss} />
    </section>
  );
}
