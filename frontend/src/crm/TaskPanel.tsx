import { useEffect, useId, useRef, useState } from "react";
import { getTasks, getTaskPreferences, type PersonTask } from "../api";
import { TaskEditor } from "./TaskEditor";

function dateLabel(value: string | null) {
  if (!value) return "No date";
  return new Intl.DateTimeFormat(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" })
    .format(new Date(`${value}T12:00:00Z`));
}

export function TaskPanel({ personId }: { personId?: string }) {
  const id = useId();
  const [tasks, setTasks] = useState<PersonTask[] | null>(null);
  const [timezone, setTimezone] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [next, setNext] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [editor, setEditor] = useState<{ task: PersonTask | null } | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    let active = true;
    setLoading(true); setFailed(false);
    Promise.all([getTasks(personId, offset), getTaskPreferences()]).then(([result, preferences]) => {
      if (!active) return;
      setTasks(result.tasks); setNext(result.next_offset); setTimezone(preferences.timezone); setLoading(false);
    }, () => { if (active) { setFailed(true); setLoading(false); } });
    return () => { active = false; };
  }, [personId, offset, attempt]);
  useEffect(() => {
    const refresh = () => setAttempt(n => n + 1);
    window.addEventListener("sourcecado:tasks-changed", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.removeEventListener("sourcecado:tasks-changed", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, []);
  function open(task: PersonTask | null) {
    returnFocus.current = document.activeElement as HTMLElement;
    setEditor({ task }); setStatus(null);
  }
  function close(saved: boolean) {
    setEditor(null);
    if (saved) setStatus("Task saved.");
    window.setTimeout(() => returnFocus.current?.focus(), 0);
  }
  return <section className="task-panel" aria-labelledby={`${id}-heading`}>
    <header className="task-panel-header">
      <div><h2 id={`${id}-heading`}>Tasks</h2>
        {!personId ? <p>Promises and next steps for all saved people.</p> : null}</div>
      <div className="task-actions">
        <button type="button" disabled={editor !== null || timezone === null} onClick={() => open(null)}>Add task</button>
        <button type="button" disabled={loading} onClick={() => setAttempt(n => n + 1)}>Refresh tasks</button>
      </div>
    </header>
    {status ? <p role="status">{status}</p> : null}
    {failed ? <p className="task-error" role="alert">Couldn’t load tasks. Your previously loaded tasks are kept. <button type="button" onClick={() => setAttempt(n => n + 1)}>Retry tasks</button></p> : null}
    {loading ? <p role="status">Loading tasks…</p> : null}
    {editor && timezone ? <TaskEditor key={editor.task?.task_id ?? "new"} task={editor.task} personId={personId} timezone={timezone} onSaved={() => close(true)} onCancel={() => close(false)} /> : null}
    <div className="task-table-scroll" tabIndex={0} role="region" aria-label="Scrollable task list" aria-busy={loading}>
      <table className="task-table" aria-label={personId ? "Person tasks" : "Tasks for all saved people"}>
        <thead><tr>{!personId ? <th scope="col">Person</th> : null}<th scope="col">Task</th><th scope="col">Due date</th><th scope="col">Actions</th></tr></thead>
        <tbody>{tasks?.map(task => <tr key={task.task_id}>
          {!personId ? <td><a href={`#/people/${encodeURIComponent(task.person_id)}`}>{task.person_name || "Unnamed person"}</a><span className="task-meta">{task.person_company}</span></td> : null}
          <td><strong>{task.title}</strong>{task.details ? <span className="task-row-details">{task.details}</span> : null}</td>
          <td>{task.due_date ? <time dateTime={task.due_date} title={task.due_timezone}>{dateLabel(task.due_date)}</time> : "No date"}</td>
          <td><button type="button" disabled={editor !== null} aria-label={`Edit ${task.title}`} onClick={() => open(task)}>Edit</button></td>
        </tr>)}</tbody>
      </table>
      {tasks?.length === 0 && !failed ? <p className="task-empty">{offset ? "No more tasks." : "No tasks yet. Save a promise or next step for a person."}</p> : null}
    </div>
    <div className="task-actions task-pagination">
      {offset > 0 ? <button type="button" disabled={loading} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous tasks</button> : null}
      {next !== null ? <button type="button" disabled={loading} onClick={() => setOffset(next)}>Next tasks</button> : null}
    </div>
  </section>;
}
