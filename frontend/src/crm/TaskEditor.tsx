import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import {
  CrmTaskError, createPersonTask, updatePersonTask,
  type PersonTask, type TaskFields, type TaskIntent,
} from "../api";
import { PersonPicker } from "./PersonPicker";
import { registerTaskNavigation } from "./taskNavigation";

export function TaskEditor({ task, personId, timezone, onSaved, onCancel }: {
  task: PersonTask | null; personId?: string; timezone: string;
  onSaved: () => void; onCancel: () => void;
}) {
  const id = useId();
  const initial: TaskFields = { title: task?.title ?? "", details: task?.details ?? "", due_date: task?.due_date ?? null };
  const [draft, setDraft] = useState(initial);
  const [person, setPerson] = useState(task?.person_id ?? personId ?? "");
  const [version, setVersion] = useState(task?.version);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState<PersonTask | null>(null);
  const [retry, setRetry] = useState<TaskIntent | null>(null);
  const [destination, setDestination] = useState<string | null>(null);
  const busyRef = useRef(false);
  const unregister = useRef<(() => void) | null>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const leaveRef = useRef<HTMLElement>(null);
  const dirty = busy || retry !== null || JSON.stringify(draft) !== JSON.stringify(initial) || person !== (task?.person_id ?? personId ?? "");

  useEffect(() => { titleRef.current?.focus(); }, []);
  useEffect(() => { if (destination) leaveRef.current?.focus(); }, [destination]);
  useEffect(() => {
    if (!dirty) return;
    const callback = (hash: string) => { setDestination(hash); return false; };
    unregister.current = registerTaskNavigation(callback);
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const click = (event: MouseEvent) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as HTMLElement)?.closest<HTMLAnchorElement>("a[href]");
      if (!anchor || anchor.target === "_blank") return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin === location.origin && url.pathname === location.pathname && url.hash && url.hash !== location.hash) {
        event.preventDefault(); event.stopPropagation(); setDestination(url.hash);
      }
    };
    document.addEventListener("click", click, true);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      unregister.current?.();
      document.removeEventListener("click", click, true);
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [dirty]);

  function leave(hash: string | null) {
    unregister.current?.();
    if (hash) window.location.hash = hash;
  }
  async function save(event?: FormEvent, leaveAfter = false) {
    event?.preventDefault();
    if (busyRef.current) return;
    if (!retry && formRef.current && !formRef.current.reportValidity()) return;
    if (!person || !draft.title.trim() || draft.title.length > 200 || draft.details.length > 8000) {
      setError("Choose a person and enter a title of 1–200 characters. Details can contain up to 8,000 characters.");
      return;
    }
    const intent: TaskIntent = retry ?? {
      ...draft, operation_id: crypto.randomUUID(), ...(version !== undefined ? { expected_version: version } : {}),
    };
    busyRef.current = true; setBusy(true); setError(null);
    try {
      if (task) await updatePersonTask(person, task.task_id, intent);
      else await createPersonTask(person, intent);
      unregister.current?.();
      window.dispatchEvent(new Event("sourcecado:tasks-changed"));
      onSaved();
      if (leaveAfter) leave(destination);
    } catch (failure) {
      if (failure instanceof CrmTaskError && failure.status >= 400 && failure.status < 500) {
        setRetry(null); setError(failure.message); setCurrent(failure.current);
      } else {
        setRetry(intent);
        setError("Couldn’t confirm this save. Your text is kept. Retry the saved request to check whether it was saved.");
      }
    } finally {
      busyRef.current = false; setBusy(false);
    }
  }
  return <form ref={formRef} className="task-editor" onSubmit={event => void save(event)} aria-labelledby={`${id}-heading`}>
    <h3 id={`${id}-heading`}>{task ? "Edit task" : "New task"}</h3>
    {!task && !personId ? <PersonPicker value={person} onChange={setPerson} disabled={busy || retry !== null} /> : null}
    <fieldset disabled={busy || retry !== null}>
      <label htmlFor={`${id}-title`}>Title</label>
      <input id={`${id}-title`} ref={titleRef} required maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} />
      <label htmlFor={`${id}-details`}>Details</label>
      <textarea id={`${id}-details`} maxLength={8000} rows={3} value={draft.details} onChange={e => setDraft({ ...draft, details: e.target.value })} />
      <label htmlFor={`${id}-date`}>Due date</label>
      <input id={`${id}-date`} type="date" value={draft.due_date ?? ""} onChange={e => setDraft({ ...draft, due_date: e.target.value || null })} aria-describedby={`${id}-date-note`} />
      <p id={`${id}-date-note`} className="task-meta">Optional · {task && draft.due_date === task.due_date ? task.due_timezone : timezone}</p>
    </fieldset>
    {error ? <p className="task-error" role="alert">{error}</p> : null}
    {current ? <section className="task-conflict" aria-label="Current saved task">
      <h4>Current saved version</h4><strong>{current.title}</strong>
      <p className="task-details">{current.details || "No details"}</p><p>{current.due_date ?? "No due date"} · {current.due_timezone}</p>
      <button type="button" disabled={busy} onClick={() => { setVersion(current.version); setCurrent(null); setError(null); }}>Keep my text and use latest version</button>
    </section> : null}
    <div className="task-actions">
      <button type="submit" disabled={busy || current !== null}>{busy ? "Saving…" : retry ? "Retry saved request" : "Save task"}</button>
      <button type="button" disabled={busy} onClick={() => { unregister.current?.(); onCancel(); }}>Cancel</button>
    </div>
    {destination ? <section ref={leaveRef} tabIndex={-1} className="task-leave" role="alertdialog" aria-labelledby={`${id}-leave-heading`}>
      <h4 id={`${id}-leave-heading`}>Save your task before leaving?</h4>
      <p>Your unsaved text is still here.</p>
      <div className="task-actions">
        <button type="button" disabled={busy || current !== null} onClick={() => void save(undefined, true)}>Save and leave</button>
        <button type="button" disabled={busy} onClick={() => { onCancel(); leave(destination); }}>Discard and leave</button>
        <button type="button" onClick={() => { setDestination(null); titleRef.current?.focus(); }}>Keep editing</button>
      </div>
    </section> : null}
  </form>;
}
