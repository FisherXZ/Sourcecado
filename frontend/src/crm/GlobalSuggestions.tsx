import { useEffect, useState } from "react";
import { getCrmProposals, type CrmProposal } from "../api";

export function GlobalSuggestions({ query, status, onOpen }: { query: string; status: string; onOpen: (id: string) => void }) {
  const [result, setResult] = useState<{ proposals: CrmProposal[]; truncated: boolean } | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    getCrmProposals().then(next => { if (active) { setResult(next); setFailed(false); } }, () => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [attempt]);
  useEffect(() => {
    const reload = () => setAttempt(n => n + 1);
    window.addEventListener("sourcecado:board-changed", reload);
    return () => window.removeEventListener("sourcecado:board-changed", reload);
  }, []);
  const proposals = result?.proposals.filter(p => (status === "all" || p.person?.sequence_state === status)
    && [p.person?.first_name, p.person?.last_name, p.person?.company, p.value.title, p.value.text].filter(Boolean).join(" ").toLowerCase().includes(query.trim().toLowerCase())) ?? [];
  return <section className="preview-section" aria-label="Global suggestions to review">
    <h2>Suggestions to review</h2>
    <p className="person-empty">Suggestions need review before they become commitments.</p>
    {!result && !failed ? <p role="status">Loading suggestions…</p> : null}
    {failed ? <div role="alert"><p>Couldn’t refresh suggestions. Saved work is kept.</p><button type="button" onClick={() => setAttempt(n => n + 1)}>Retry suggestions</button></div> : null}
    {result && !proposals.length ? <p>No pending suggestions match these filters.</p> : null}
    {proposals.map(p => <article key={p.proposal_id} className="preview-suggestion">
      <strong>{String(p.value.title ?? p.value.text ?? p.value.state ?? "Suggested task update")}</strong>
      <p>{[p.person?.first_name, p.person?.last_name].filter(Boolean).join(" ")} · {p.reason}</p>
      <button type="button" onClick={() => onOpen(p.person_id)}>Review person</button>
    </article>)}
    {result?.truncated ? <p>Showing the first {result.proposals.length} suggestions.</p> : null}
  </section>;
}
