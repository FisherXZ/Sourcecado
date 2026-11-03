import { useEffect, useRef, useState, type FormEvent } from "react";

import { getAfterSendReminder, saveAfterSendReminder, type AfterSendReminderContext, type PersonTask } from "./api";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";
import { TASKS_CHANGED } from "./Tasks";
import "./styles/afterSendReminder.css";

/** Optional local task work, separate from the completed send and its approval. */
export function AfterSendReminder({ personId, sent }: {
  personId: string;
  sent: Record<string, unknown>;
}) {
  const [context, setContext] = useState<AfterSendReminderContext | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [task, setTask] = useState<PersonTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [skipped, setSkipped] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const active = useRef(true);
  const heading = useRef<HTMLInputElement>(null);
  const skippedMessage = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => { if (context && !task) heading.current?.focus(); }, [context, task]);
  useEffect(() => { if (skipped) skippedMessage.current?.focus(); }, [skipped]);

  const messageId = typeof sent.message_id === "string" ? sent.message_id.trim() : "";
  if (sent.sent !== true || !messageId) return null;
  if (skipped) return <p ref={skippedMessage} tabIndex={-1} role="status">No follow-up added.</p>;

  async function open() {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const next = await getAfterSendReminder(personId, messageId);
      if (!active.current) return;
      setContext(next);
      setTitle(current => current || next.title);
      setTask(next.task);
    } catch (error) {
      if (active.current) setProblem(error instanceof Error ? error.message : "Couldn’t load follow-up options. Try again.");
    } finally {
      if (active.current) setBusy(false);
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!context || busy || !title.trim() || !date) return;
    setBusy(true);
    setProblem(null);
    try {
      const saved = await saveAfterSendReminder(personId, messageId, {
        title, dueDate: date, operationId: context.operation_id, timezone: context.timezone,
      });
      if (!active.current) return;
      setTask(saved.task);
      window.dispatchEvent(new Event(TASKS_CHANGED));
      window.dispatchEvent(new Event("sourcecado:board-changed"));
    } catch (error) {
      if (active.current) setProblem(error instanceof Error ? error.message : "Couldn’t save the follow-up. Retry only the task.");
    } finally {
      if (active.current) setBusy(false);
    }
  }

  return <section className="after-send-reminder" aria-label="Optional follow-up reminder">
    {task ? <div role="status">
      <p>Follow-up saved: {task.title} · {task.due_date} ({task.due_timezone}).</p>
      <a href={`#/people/${encodeURIComponent(personId)}`}>View person’s tasks</a>
    </div> : context ? <form onSubmit={event => void save(event)} aria-label="Set follow-up">
      <p className="after-send-hint"><Glyph name="clock" />{context.timezone} · Reminder only, never sends email</p>
      <fieldset disabled={busy}>
        <label className="task-field">Follow-up task<input ref={heading} value={title} maxLength={200} required onChange={event => setTitle(event.currentTarget.value)} /></label>
        <label className="task-field">Follow-up date<input type="date" value={date} required onChange={event => setDate(event.currentTarget.value)} /></label>
        <div className="person-outreach-actions">
          <button type="submit" disabled={!date || !title.trim()}><Icon name={problem ? "refresh" : "save"} />{busy ? "Saving follow-up…" : problem ? "Retry saving follow-up" : "Save follow-up"}</button>
          <button type="button" aria-label="Skip follow-up" onClick={() => setSkipped(true)}><Icon name="close" />Skip</button>
        </div>
      </fieldset>
    </form> : <div className="person-outreach-actions">
      <button type="button" disabled={busy} onClick={() => void open()}><Icon name="reply" />{busy ? "Loading follow-up…" : problem ? "Retry follow-up options" : "Set follow-up"}</button>
      <button type="button" disabled={busy} aria-label="Skip follow-up" onClick={() => setSkipped(true)}><Icon name="close" />Skip</button>
    </div>}
    {problem ? <div role="alert"><p>{problem}</p>
      {context ? <button type="button" disabled={busy} onClick={() => void open()}><Icon name="refresh" />Refresh follow-up options</button> : null}
    </div> : null}
  </section>;
}
