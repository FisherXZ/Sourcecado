import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { DescriptionEditor } from "../DescriptionEditor";
import { clearDescriptionDraft } from "../descriptionDrafts";
import { TaskList, TASKS_CHANGED } from "../Tasks";
import { crmRequest, defaultPersonDescriptions, getPerson, newOperationId, setPersonSequence, type PersonFile } from "../api";
import { MailActivity } from "./MailActivity";
import { SavedTaskDrafts } from "./SavedTaskDrafts";
import { TaskDraft } from "./TaskDraft";

export type PreviewHandle = { navigate: (next: () => void) => void };

/** Non-modal inspector: table position and filters stay mounted. */
export const PersonPreview = forwardRef<PreviewHandle, { personId: string; onClose: () => void }>(function PersonPreview({ personId, onClose }, ref) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [flag, setFlag] = useState("");
  const [until, setUntil] = useState("");
  const [flagDirty, setFlagDirty] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const panel = useRef<HTMLElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const operation = useRef<{ key: string; id: string } | null>(null);
  const [editingProposal, setEditingProposal] = useState<string | null>(null);
  const [proposalText, setProposalText] = useState("");

  useEffect(() => { back.current?.focus(); }, []);
  useEffect(() => {
    if (pending) panel.current?.querySelector<HTMLElement>('[role="alertdialog"] button')?.focus();
  }, [pending]);
  useEffect(() => {
    let active = true;
    getPerson(personId).then(next => {
      if (!active) return;
      setFile(next); setFailed(false);
      if (!flagDirty) {
        setFlag(next.person.attention_reason ?? "");
        setUntil(next.person.attention_until ?? "");
      }
    }, () => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [personId, attempt]);
  useEffect(() => {
    const reload = () => setAttempt(n => n + 1);
    window.addEventListener(TASKS_CHANGED, reload);
    window.addEventListener("sourcecado:board-changed", reload);
    const timer = window.setInterval(reload, 15000);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(TASKS_CHANGED, reload);
      window.removeEventListener("sourcecado:board-changed", reload);
    };
  }, []);

  function navigate(next: () => void) {
    if (busy || flagDirty || editingProposal || panel.current?.querySelector('[data-unsaved="true"], .task-form')) {
      setPending(() => next);
    } else next();
  }
  useImperativeHandle(ref, () => ({ navigate }));

  async function mutate(path: string, payload: Record<string, unknown>, method = "POST") {
    if (busy) return;
    const key = JSON.stringify({ path, payload });
    if (operation.current?.key !== key) operation.current = { key, id: newOperationId() };
    setBusy(true); setNotice(null);
    try {
      await crmRequest(path, { ...payload, operation_id: operation.current.id }, method);
      operation.current = null;
      setFlagDirty(false); setEditingProposal(null);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
      window.dispatchEvent(new Event(TASKS_CHANGED));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Couldn’t save. Your work is kept.");
      setAttempt(n => n + 1);
    } finally { setBusy(false); }
  }

  const prefix = `/v1/people/${encodeURIComponent(personId)}`;
  const descriptions = file?.descriptions ?? defaultPersonDescriptions();
  const person = file?.person;
  return <aside ref={panel} className="person-preview" role="region" aria-label="Person preview"
    onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); navigate(onClose); } }}>
    <header className="preview-header">
      <button type="button" ref={back} onClick={() => navigate(onClose)}>Back to Contacts</button>
      <a href={`#/people/${encodeURIComponent(personId)}`} onClick={event => {
        event.preventDefault(); navigate(() => { window.location.hash = `#/people/${encodeURIComponent(personId)}`; });
      }}>Open person file</a>
    </header>
    {pending ? <div className="preview-unsaved" role="alertdialog" aria-label="Unsaved edits">
      <p>Your edits are still here. Save them before leaving, or discard them.</p>
      <button type="button" onClick={() => {
        setPending(null);
        panel.current?.querySelector<HTMLElement>('[data-unsaved="true"] textarea, .task-form input')?.focus();
      }}>Keep editing and save</button>
      <button type="button" disabled={busy} onClick={() => {
        clearDescriptionDraft(personId, "general"); clearDescriptionDraft(personId, "detailed");
        const next = pending; setPending(null); next();
      }}>Discard edits</button>
    </div> : null}
    {!file && !failed ? <p role="status" aria-busy="true">Loading person…</p> : null}
    {failed ? <div role="alert"><p>Couldn’t refresh this person. Previously loaded work is kept.</p>
      <button type="button" onClick={() => setAttempt(n => n + 1)}>Retry</button></div> : null}
    {file && person ? <>
      <h2>{[person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person"}</h2>
      <p className="preview-subtitle">{[person.title, person.company].filter(Boolean).join(" · ")}</p>
      <p className="person-timeline-meta">{person.last_contact_at
        ? `${person.last_contact_direction === "inbound" ? "They replied" : "We wrote"} · ${person.last_contact_at.slice(0, 10)}`
        : "No contact yet"}</p>
      <div className="preview-status">
        <label>Relationship status<select value={person.sequence_state ?? ""} disabled={busy} onChange={async event => {
          const state = event.target.value;
          setBusy(true); setNotice(null);
          try {
            await setPersonSequence(personId, state, "director", person.version);
            window.dispatchEvent(new Event("sourcecado:board-changed"));
          } catch { setNotice("Couldn’t change status. Refresh and try again."); }
          finally { setBusy(false); }
        }}><option value="" disabled>Not started</option><option value="open">Open</option>
          <option value="in_conversation">In conversation</option><option value="done">Done</option></select></label>
      </div>
      <form className="preview-flag" onSubmit={event => {
        event.preventDefault(); void mutate(`${prefix}/attention`, { reason: flag || null, until: until || null, expected_version: person.version }, "PATCH");
      }}>
        <label>Attention reason<input value={flag} maxLength={500} disabled={busy} onChange={e => { setFlag(e.target.value); setFlagDirty(true); }} /></label>
        <label>Flag until (optional)<input type="date" value={until} disabled={busy} onChange={e => { setUntil(e.target.value); setFlagDirty(true); }} /></label>
        <button type="submit" disabled={busy || !flagDirty}>Save flag</button>
      </form>
      <section className="preview-section"><h3>Short description</h3>
        <DescriptionEditor personId={personId} description={descriptions.general} rows={2}
          onSaved={next => { setFile(current => current ? { ...current, descriptions: next } : current); window.dispatchEvent(new Event("sourcecado:board-changed")); }} />
      </section>
      <details className="preview-section"><summary>Detailed description</summary>
        <DescriptionEditor personId={personId} description={descriptions.detailed}
          onSaved={next => { setFile(current => current ? { ...current, descriptions: next } : current); window.dispatchEvent(new Event("sourcecado:board-changed")); }} />
      </details>
      {person.sequence_state === "done" ? <p>Unfinished tasks stay open when a relationship is Done.</p> : null}
      <TaskList personId={personId} headingLevel={3} />
      <SavedTaskDrafts file={file} />
      <section className="preview-section"><h3>Suggestions to review</h3>
        {!file.crm_proposals?.length ? <p className="person-empty">No pending suggestions.</p> : file.crm_proposals.map(proposal => <article key={proposal.proposal_id} className="preview-suggestion">
          <strong>{String(proposal.value.title ?? proposal.value.text ?? proposal.value.state ?? "Suggested task update")}</strong>
          <p>{proposal.reason}</p>
          {proposal.source_refs.map((source, index) => source.url?.startsWith("https://mail.google.com/") ? <a key={index} href={source.url} target="_blank" rel="noreferrer">Source email</a> : null)}
          {editingProposal === proposal.proposal_id ? <label>Suggested value<textarea aria-label="Edit suggested text" value={proposalText} onChange={e => setProposalText(e.target.value)} /></label> : null}
          <div className="preview-actions">
            <button type="button" disabled={busy} onClick={() => {
              const value = editingProposal === proposal.proposal_id ? { ...proposal.value, [proposal.kind === "description" ? "text" : "title"]: proposalText } : undefined;
              void mutate(`${prefix}/crm-proposals/${proposal.proposal_id}/resolve`, { decision: "accepted", ...(value ? { value } : {}) });
            }}>Accept</button>
            {proposal.kind === "description" || proposal.kind === "task_create" ? <button type="button" onClick={() => { setEditingProposal(proposal.proposal_id); setProposalText(String(proposal.value.text ?? proposal.value.title ?? "")); }}>Edit suggestion</button> : null}
            <button type="button" disabled={busy} onClick={() => void mutate(`${prefix}/crm-proposals/${proposal.proposal_id}/resolve`, { decision: "dismissed" })}>Dismiss</button>
          </div>
          <TaskDraft proposalId={proposal.proposal_id} task={{ person_id: personId, task_id: proposal.proposal_id,
            title: String(proposal.value.title ?? "Suggested follow-up"), version: 0 }} />
        </article>)}
      </section>
      <MailActivity events={file.timeline} gaps={person.knowledge_gaps ?? []} sync={file.mail_sync} analysis={file.mail_analysis} followUp={person.follow_up} />
      {file.mail_analysis?.status === "failed" ? <button type="button" disabled={busy} onClick={() => void mutate(`${prefix}/crm-analysis/retry`, {})}>Retry analysis</button> : null}
      {person.unanswered_message_ids && Array.isArray(person.unanswered_message_ids) ? person.unanswered_message_ids.map((id) => <button type="button" key={String(id)} disabled={busy}
        onClick={() => void mutate(`${prefix}/crm-reviews/${encodeURIComponent(String(id))}/dismiss`, {})}>Dismiss reply review</button>) : null}
      <details className="preview-section"><summary>Recent changes</summary>
        {!file.crm_changes?.length ? <p>No CRM changes recorded yet.</p> : <ol className="preview-changes">{file.crm_changes.map(change => <li key={change.change_id}>
          <p>{change.reason} <span className="person-timeline-meta">· {change.actor === "assistant" ? "Assistant" : "You"}</span></p>
          {change.entity_type === "task" || change.entity_type === "description" || change.entity_type === "attention" ? <button type="button" disabled={busy}
            onClick={() => void mutate(`${prefix}/crm-changes/${change.change_id}/undo`, {})}>Undo</button> : null}
        </li>)}</ol>}
      </details>
    </> : null}
    {notice ? <p role="alert">{notice}</p> : null}
  </aside>;
});
