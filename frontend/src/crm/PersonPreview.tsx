import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { EnrichmentPanel } from "../EnrichmentPanel";
import { DescriptionEditor } from "../DescriptionEditor";
import { clearDescriptionDraft } from "../descriptionDrafts";
import { TaskList, TASKS_CHANGED } from "../Tasks";
import { crmRequest, defaultPersonDescriptions, getPerson, newOperationId, type PersonFile } from "../api";
import { MailActivity } from "./MailActivity";
import { SavedTaskDrafts } from "./SavedTaskDrafts";
import { TaskDraft } from "./TaskDraft";
import {Icon} from "../icons";
import { personInitials } from "../personName";
import {PersonDetailsEditor,clearPersonCorrectionDraft} from "./PersonDetailsEditor";
import { SequencePill } from "./SequencePill";

function reviewValue(value: Record<string, unknown> | null | undefined): string {
  if (!value) return "No saved value";
  const fields = typeof value.changes === "object" && value.changes !== null
    ? value.changes as Record<string, unknown> : value;
  const parts = [fields.text, fields.title, fields.state, fields.due_date]
    .filter((part): part is string => typeof part === "string" && part.length > 0);
  return parts.join(" · ") || "No saved value";
}

export type PreviewHandle = { navigate: (next: () => void) => void };

/** Where the previewed person sits in the table's current filtered order. */
type PreviewPosition = { index: number; total: number; onPrevious: () => void; onNext: () => void };

/** Non-modal inspector: table position and filters stay mounted. */
export const PersonPreview = forwardRef<PreviewHandle, {
  personId: string;
  onClose: () => void;
  position?: PreviewPosition;
  /** After moving with the pager, keep focus on the same pager button. */
  initialFocus?: "previous" | "next";
}>(function PersonPreview({ personId, onClose, position, initialFocus }, ref) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [detailsBusy,setDetailsBusy]=useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [flag, setFlag] = useState("");
  const [until, setUntil] = useState("");
  const [flagDirty, setFlagDirty] = useState(false);
  const [flagOpen, setFlagOpen] = useState(false);
  const [movingTo, setMovingTo] = useState<string | null>(null);
  const [sequenceUndo, setSequenceUndo] = useState<string | null>(null);
  const flagField = useRef<HTMLInputElement>(null);
  const flagFocus = useRef<"field" | "trigger" | null>(null);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const panel = useRef<HTMLElement>(null);
  const back = useRef<HTMLButtonElement>(null);
  const previousButton = useRef<HTMLButtonElement>(null);
  const nextButton = useRef<HTMLButtonElement>(null);
  const operation = useRef<{ key: string; id: string } | null>(null);
  const [editingProposal, setEditingProposal] = useState<string | null>(null);
  const [proposalText, setProposalText] = useState("");

  useEffect(() => {
    const step = initialFocus === "previous" ? previousButton.current : initialFocus === "next" ? nextButton.current : null;
    (step && !step.disabled ? step : back.current)?.focus();
  }, []);
  useEffect(() => {
    if (pending) panel.current?.querySelector<HTMLElement>('[role="alertdialog"] button')?.focus();
  }, [pending]);
  useEffect(() => {
    const target = flagFocus.current === "field" ? flagField.current
      : flagFocus.current === "trigger" ? panel.current?.querySelector<HTMLElement>(".preview-flag-trigger") : null;
    flagFocus.current = null;
    target?.focus();
  }, [flagOpen]);
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
    if(detailsBusy)return;
    if (busy || flagDirty || editingProposal || panel.current?.querySelector('[data-unsaved="true"], .task-form')) {
      setPending(() => next);
    } else next();
  }
  useImperativeHandle(ref, () => ({ navigate }));

  /** Saves one change with a durable operation receipt; null when it failed. */
  async function mutate(path: string, payload: Record<string, unknown>, method = "POST"): Promise<Record<string, unknown> | null> {
    if (busy) return null;
    const key = JSON.stringify({ path, payload });
    if (operation.current?.key !== key) operation.current = { key, id: newOperationId() };
    setBusy(true); setNotice(null);
    try {
      const result = await crmRequest(path, { ...payload, operation_id: operation.current.id }, method);
      operation.current = null;
      setFlagDirty(false); setEditingProposal(null);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
      window.dispatchEvent(new Event(TASKS_CHANGED));
      return result;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Couldn’t save. Your work is kept.");
      setAttempt(n => n + 1);
      return null;
    } finally { setBusy(false); }
  }

  async function moveSequence(state: string) {
    if (busy || !file) return;
    setMovingTo(state);
    const result = await mutate(`${prefix}/sequence`, { state, expected_version: file.person.version });
    setMovingTo(null);
    if (!result) return;
    const change = (result.change as { change_id?: unknown } | undefined)?.change_id;
    setSequenceUndo(typeof change === "string" ? change : null);
    const next = result.person as PersonFile["person"] | undefined;
    if (next?.person_id === personId) setFile(current => current ? { ...current, person: next } : current);
  }

  async function undoSequence() {
    if (!sequenceUndo) return;
    await mutate(`${prefix}/crm-changes/${encodeURIComponent(sequenceUndo)}/undo`, {});
    // A failed Undo means the state moved again; the reloaded pill shows where.
    setSequenceUndo(null);
  }

  function openFlag() {
    flagFocus.current = "field";
    setFlagOpen(true);
  }

  function cancelFlag() {
    setFlag(file?.person.attention_reason ?? "");
    setUntil(file?.person.attention_until ?? "");
    setFlagDirty(false);
    flagFocus.current = "trigger";
    setFlagOpen(false);
  }

  const prefix = `/v1/people/${encodeURIComponent(personId)}`;
  const descriptions = file?.descriptions ?? defaultPersonDescriptions();
  const person = file?.person;
  return <aside ref={panel} className="person-preview" role="region" aria-label="Person preview"
    onClick={event => {
      if (event.defaultPrevented || !(event.target instanceof Element)) return;
      const link = event.target.closest<HTMLAnchorElement>('a[href^="#/"]');
      if (!link) return;
      event.preventDefault();
      navigate(() => { window.location.hash = link.getAttribute("href")!; });
    }}
    onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); navigate(onClose); } }}>
    <header className="preview-titlebar">
      {person ? <div className="preview-identity">
        <span className="contacts-avatar" aria-hidden="true">{personInitials(person)}</span>
        <h2>{[person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person"}</h2>
      </div> : null}
      <div className="preview-titlebar-actions">
        {position ? <div className="preview-pager" role="group" aria-label="Move between people">
          <button type="button" ref={previousButton} aria-label="Previous person" title="Previous person"
            disabled={detailsBusy || position.index === 0} onClick={position.onPrevious}><Icon name="previous"/></button>
          <span>{position.index + 1} of {position.total}</span>
          <button type="button" ref={nextButton} aria-label="Next person" title="Next person"
            disabled={detailsBusy || position.index >= position.total - 1} onClick={position.onNext}><Icon name="next"/></button>
        </div> : null}
        <a className="preview-icon-link" aria-label="Open person file" title="Open person file" href={`#/people/${encodeURIComponent(personId)}`} onClick={event => {
          event.preventDefault(); navigate(() => { window.location.hash = `#/people/${encodeURIComponent(personId)}`; });
        }}><Icon name="open"/></a>
        <button type="button" ref={back} aria-label="Close preview" title="Close preview" disabled={detailsBusy} onClick={() => navigate(onClose)}><Icon name="close"/></button>
      </div>
    </header>
    {pending ? <div className="preview-unsaved" role="alertdialog" aria-label="Unsaved edits">
      <p>Your edits are still here. Save them before leaving, or discard them.</p>
      <button type="button" onClick={() => {
        setPending(null);
        panel.current?.querySelector<HTMLElement>('[data-unsaved="true"] textarea, .preview-flag[data-unsaved="true"] input, .task-form input')?.focus();
      }}><Icon name="edit"/>Keep editing and save</button>
      <button type="button" disabled={busy} onClick={() => {
        clearDescriptionDraft(personId, "general"); clearDescriptionDraft(personId, "detailed"); clearPersonCorrectionDraft(personId);
        const next = pending; setPending(null); next();
      }}><Icon name="deny"/>Discard edits</button>
    </div> : null}
    {!file && !failed ? <p role="status" aria-busy="true">Loading person…</p> : null}
    {failed ? <div role="alert"><p>Couldn’t refresh this person. Previously loaded work is kept.</p>
      <button type="button" onClick={() => setAttempt(n => n + 1)}><Icon name="refresh"/>Retry</button></div> : null}
    {file && person ? <>
      <div className="preview-status">
        <SequencePill label="Relationship status" value={movingTo ?? person.sequence_state ?? ""} disabled={busy}
          onChange={state => void moveSequence(state)} onUndo={sequenceUndo ? () => void undoSequence() : undefined} />
        {flagOpen || flagDirty ? null : person.attention_reason ? <span className="preview-flag-set">
          <span className="contacts-pill is-warn" title={person.attention_reason}>
            <Icon name="flag"/>{person.attention_reason}{person.attention_until ? ` · until ${person.attention_until}` : ""}
          </span>
          <button type="button" className="preview-icon-button preview-flag-trigger" aria-label="Edit flag" title="Edit flag"
            disabled={busy} onClick={openFlag}><Icon name="edit"/></button>
          <button type="button" className="preview-icon-button" aria-label="Clear flag" title="Clear flag" disabled={busy}
            onClick={async () => {
              if (await mutate(`${prefix}/attention`, { reason: null, until: null, expected_version: person.version }, "PATCH")) {
                setFlag(""); setUntil("");
              }
            }}><Icon name="close"/></button>
        </span> : <button type="button" className="preview-flag-trigger" disabled={busy} onClick={openFlag}><Icon name="flag"/>Flag</button>}
      </div>
      {flagOpen || flagDirty ? <form className="preview-flag" data-unsaved={flagDirty ? "true" : undefined} onSubmit={async event => {
        event.preventDefault();
        if (await mutate(`${prefix}/attention`, { reason: flag || null, until: until || null, expected_version: person.version }, "PATCH")) {
          flagFocus.current = "trigger"; setFlagOpen(false);
        }
      }}>
        <label>Attention reason<input ref={flagField} value={flag} maxLength={500} disabled={busy} onChange={e => { setFlag(e.target.value); setFlagDirty(true); }} /></label>
        <label>Flag until (optional)<input type="date" value={until} disabled={busy} onChange={e => { setUntil(e.target.value); setFlagDirty(true); }} /></label>
        <div className="preview-actions">
          <button type="submit" disabled={busy || !flagDirty}><Icon name="save"/>Save flag</button>
          <button type="button" disabled={busy} onClick={cancelFlag}><Icon name="close"/>Cancel</button>
        </div>
      </form> : null}
      <p className="person-timeline-meta">{person.last_contact_at
        ? `${person.last_contact_direction === "inbound" ? "They replied" : "We wrote"} · ${person.last_contact_at.slice(0, 10)}`
        : "No contact yet"}</p>
      <PersonDetailsEditor key={personId} person={person} history={file.crm_changes} onBusyChange={setDetailsBusy} onSaved={next=>setFile(current=>current?.person.person_id===next.person_id?{...current,person:next}:current)}/>
      <EnrichmentPanel personId={personId} />
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
          <div className="suggestion-comparison">
            <p>Current</p><p>{reviewValue(proposal.current_value)}</p>
            <p>Suggested</p><strong>{reviewValue(proposal.value)}</strong>
          </div>
          <p>{proposal.reason}</p>
          {proposal.source_refs.map((source, index) => source.url?.startsWith("https://mail.google.com/") ? <a key={index} href={source.url} target="_blank" rel="noreferrer">Source email</a> : null)}
          {editingProposal === proposal.proposal_id ? <label>Suggested value<textarea aria-label="Edit suggested text" value={proposalText} onChange={e => setProposalText(e.target.value)} /></label> : null}
          <div className="preview-actions">
            <button type="button" disabled={busy} onClick={() => {
              const value = editingProposal === proposal.proposal_id ? { ...proposal.value, [proposal.kind === "description" ? "text" : "title"]: proposalText } : undefined;
              void mutate(`${prefix}/crm-proposals/${proposal.proposal_id}/resolve`, { decision: "accepted", ...(value ? { value } : {}) });
            }}><Icon name="check"/>Accept</button>
            {proposal.kind === "description" || proposal.kind === "task_create" ? <button type="button" onClick={() => { setEditingProposal(proposal.proposal_id); setProposalText(String(proposal.value.text ?? proposal.value.title ?? "")); }}><Icon name="edit"/>Edit suggestion</button> : null}
            <button type="button" disabled={busy} onClick={() => void mutate(`${prefix}/crm-proposals/${proposal.proposal_id}/resolve`, { decision: "dismissed" })}><Icon name="deny"/>Dismiss</button>
          </div>
          <TaskDraft proposalId={proposal.proposal_id} task={{ person_id: personId, task_id: proposal.proposal_id,
            title: String(proposal.value.title ?? "Suggested follow-up"), version: 0 }} />
        </article>)}
      </section>
      <MailActivity events={file.timeline} gaps={person.knowledge_gaps ?? []} sync={file.mail_sync} analysis={file.mail_analysis} followUp={person.follow_up} />
      {file.mail_analysis?.status === "failed" ? <button type="button" disabled={busy} onClick={() => void mutate(`${prefix}/crm-analysis/retry`, {})}><Icon name="refresh"/>Retry analysis</button> : null}
      {person.unanswered_message_ids && Array.isArray(person.unanswered_message_ids) ? person.unanswered_message_ids.map((id) => <button type="button" key={String(id)} disabled={busy}
        onClick={() => void mutate(`${prefix}/crm-reviews/${encodeURIComponent(String(id))}/dismiss`, {})}><Icon name="deny"/>Dismiss reply review</button>) : null}
      <details className="preview-section"><summary>Recent changes</summary>
        {!file.crm_changes?.length ? <p>No CRM changes recorded yet.</p> : <ol className="preview-changes">{file.crm_changes.map(change => <li key={change.change_id}>
          <p>{change.reason} <span className="person-timeline-meta">· {change.actor === "assistant" ? "Assistant" : "You"}</span></p>
          {change.can_undo !== false && (change.entity_type === "task" || change.entity_type === "description" || change.entity_type === "attention" || change.entity_type === "status" || (change.entity_type === "proposal" && change.action === "accept")) ? <button type="button" disabled={busy}
            onClick={() => void mutate(`${prefix}/crm-changes/${change.change_id}/undo`, {})}><Icon name="reply"/>Undo</button> : null}
        </li>)}</ol>}
      </details>
    </> : null}
    {notice ? <p role="alert">{notice}</p> : null}
  </aside>;
});
