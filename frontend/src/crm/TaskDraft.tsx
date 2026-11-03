import { useRef, useState } from "react";
import { crmRequest, newOperationId, type PersonTask } from "../api";

export function TaskDraft({ task, proposalId }: { task: Pick<PersonTask, "task_id" | "person_id" | "title" | "version">; proposalId?: string }) {
  const [draft, setDraft] = useState<{ subject: string; body: string; thread: string | null; threadReview: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const operation = useRef<{ version: number; id: string } | null>(null);

  async function prepare() {
    if (busy) return;
    if (operation.current?.version !== task.version) operation.current = { version: task.version, id: newOperationId() };
    setBusy(true); setNotice(null);
    try {
      const path = proposalId ? `crm-proposals/${encodeURIComponent(proposalId)}` : `tasks/${encodeURIComponent(task.task_id)}`;
      const result = await crmRequest(`/v1/people/${encodeURIComponent(task.person_id)}/${path}/prepare`,
        { operation_id: operation.current.id, expected_version: task.version });
      const artifact = result.artifact;
      if (!artifact || typeof artifact !== "object" || !("subject" in artifact) || !("body" in artifact)
          || typeof artifact.subject !== "string" || typeof artifact.body !== "string") throw new Error("Invalid draft");
      setDraft({ subject: artifact.subject, body: artifact.body,
        threadReview: "thread_review_required" in artifact && artifact.thread_review_required === true,
        thread: "thread_id" in artifact && typeof artifact.thread_id === "string" ? artifact.thread_id : null });
      operation.current = null;
      window.dispatchEvent(new Event("sourcecado:board-changed"));
    } catch { setNotice("Couldn’t prepare draft text. Your task is kept; try again."); }
    finally { setBusy(false); }
  }

  return <div className="task-draft">
    <button type="button" className="task-row-action" disabled={busy} onClick={() => void prepare()}
      aria-label={`Prepare draft for ${task.title}`}>{busy ? "Preparing…" : "Prepare draft"}</button>
    {draft ? <div className="task-prepared" role="region" aria-label={`Prepared draft for ${task.title}`}>
      <strong>{draft.subject}</strong>
      <textarea aria-label="Prepared message text" value={draft.body} readOnly rows={5} />
      <p>Saved in Sourcecado. Nothing has been sent.</p>
      {draft.threadReview ? <p>The relevant conversation is unclear. Choose the correct conversation before using this text.</p>
        : draft.thread ? <p>Threaded draft creation is unavailable here. Review and copy this text into the original conversation.</p> : <a href={`#/people/${encodeURIComponent(task.person_id)}`}>Open person file to review outreach</a>}
      <button type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(draft.body); setNotice("Draft text copied."); }
        catch { setNotice("Select and copy the prepared text above."); }
      }}>Copy draft text</button>
    </div> : null}
    {notice ? <p role="status">{notice}</p> : null}
  </div>;
}
