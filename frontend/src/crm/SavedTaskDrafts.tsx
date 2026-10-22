import type { PersonFile } from "../api";

export type PreparedText = { subject: string; body: string; thread_id: string | null };

export function savedTaskDrafts(file: PersonFile): PreparedText[] {
  const artifacts = file.person.artifacts;
  if (!Array.isArray(artifacts)) return [];
  return artifacts.flatMap(item => {
    const value = item?.fields;
    if (!value || value.artifact_type !== "follow_up_draft" || typeof value.subject !== "string" || typeof value.body !== "string") return [];
    return [{ subject: value.subject, body: value.body, thread_id: typeof value.thread_id === "string" ? value.thread_id : null }];
  }).slice(-5).reverse();
}

export function SavedTaskDrafts({ file }: { file: PersonFile }) {
  const drafts = savedTaskDrafts(file);
  if (!drafts.length) return null;
  return <section className="preview-section" aria-label="Saved follow-up drafts">
    <h3>Prepared follow-ups</h3>
    {drafts.map((draft, index) => <details key={index}>
      <summary>{draft.subject}</summary>
      <textarea readOnly rows={6} aria-label={`Saved draft ${draft.subject}`} value={draft.body} />
      <p>Saved locally for review. Nothing has been sent.</p>
      {draft.thread_id ? <p>Threaded draft creation is unavailable here. Review and copy the text into the original conversation.</p> : null}
    </details>)}
  </section>;
}
