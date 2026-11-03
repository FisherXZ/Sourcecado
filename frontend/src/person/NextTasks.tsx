import { useEffect, useState } from "react";

import { Glyph } from "../generative/ResultCard";
import { TASKS_CHANGED } from "../Tasks";
import { listTasks, type PersonTask } from "../api";

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

function dueLabel(task: PersonTask): string | null {
  if (!task.due_date) return null;
  const date = new Date(`${task.due_date}T12:00:00`);
  const text = Number.isNaN(date.getTime())
    ? task.due_date
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return task.due_status === "overdue" ? `Overdue · ${text}` : text;
}

/** The next three open tasks, with a jump to the full list. */
export function NextTasks({
  tasks,
  onShowAll,
}: {
  tasks: PersonTask[] | null;
  onShowAll: () => void;
}) {
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
          {tasks.slice(0, 3).map((task) => {
            const due = dueLabel(task);
            return (
              <li key={task.task_id}>
                <Glyph name="review" />
                <span className="pf-next-task-title">{task.title}</span>
                {due ? (
                  <span
                    className={`pf-badge${task.due_status === "overdue" ? " is-warn" : ""}`}
                  >
                    {due}
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
