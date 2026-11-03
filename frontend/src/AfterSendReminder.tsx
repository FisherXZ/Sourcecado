import { useEffect, useRef, useState, type FormEvent } from "react";

import { getAfterSendReminder, saveAfterSendReminder, type AfterSendReminderContext, type PersonTask } from "./api";
import { Glyph } from "./generative/ResultCard";
import { Icon } from "./icons";
import { dueText, TASKS_CHANGED, ZoneNote } from "./Tasks";

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
  if (skipped) {
    return (
      <p ref={skippedMessage} className="crm-line after-send-reminder" tabIndex={-1} role="status">
        No follow-up added.
      </p>
    );
  }

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
    {task ? <div className="after-send-saved" role="status">
      <p className="crm-line is-ok">
        <Icon name="check" />
        Follow-up saved: {task.title}{task.due_date ? ` · ${dueText(task.due_date)}` : ""}
      </p>
      <a className="crm-btn is-ghost" href={`#/people/${encodeURIComponent(personId)}`}><Glyph name="review" />View person’s tasks</a>
    </div> : context ? <form className="after-send-form" onSubmit={event => void save(event)} aria-label="Set follow-up"
      onKeyDown={event => {
        if (event.key !== "Escape" || busy) return;
        event.preventDefault();
        event.stopPropagation();
        setSkipped(true);
      }}>
      <fieldset disabled={busy}>
        <input ref={heading} className="after-send-title" aria-label="Follow-up task" placeholder="Follow-up task"
          value={title} maxLength={200} required onChange={event => setTitle(event.currentTarget.value)} />
        <input className="after-send-date" type="date" aria-label="Follow-up date" value={date} required
          onChange={event => setDate(event.currentTarget.value)} />
        <button type="submit" className="crm-btn is-primary" disabled={!date || !title.trim()}>
          <Icon name={problem ? "refresh" : "check"} />
          {busy ? "Saving follow-up…" : problem ? "Retry saving follow-up" : "Save follow-up"}
        </button>
        <button type="button" className="crm-icon-btn" aria-label="Skip follow-up" title="Skip" onClick={() => setSkipped(true)}>
          <Icon name="close" />
        </button>
      </fieldset>
      <ZoneNote timezone={context.timezone} />
    </form> : <div className="crm-actions">
      <button type="button" className="crm-btn" disabled={busy} onClick={() => void open()}>
        <Icon name="calendar" />
        {busy ? "Loading follow-up…" : problem ? "Retry follow-up options" : "Set follow-up"}
      </button>
      <button type="button" className="crm-btn is-ghost" disabled={busy} aria-label="Skip follow-up" onClick={() => setSkipped(true)}>
        <Icon name="close" />
        Skip
      </button>
    </div>}
    {problem ? <div className="crm-alert" role="alert"><Glyph name="info" /><span>{problem}</span>
      {context ? <button type="button" className="crm-btn" disabled={busy} onClick={() => void open()}><Icon name="refresh" />Refresh follow-up options</button> : null}
    </div> : null}
  </section>;
}
