import { useEffect, useRef, useState } from "react";
import { decideApproval, getPersonEnrichment, newOperationId, preparePersonEnrichment, type PersonEnrichment } from "./api";
import { Icon } from "./icons";
import { Glyph } from "./generative/ResultCard";
import "./styles/enrichment.css";

const STATUS:Record<string,string>={
  not_enriched:"Not enriched",awaiting_approval:"Ready for review",running:"Lookup running",
  found:"Contact details found",partial:"Some details found",no_match:"No match found",
  failed:"Lookup failed",insufficient_credit:"Not enough credits",outcome_unknown:"Outcome unknown",denied:"Lookup cancelled",
};
const FIELD:Record<string,string>={name:"Name",email:"Email",phone:"Phone",linkedin_url:"LinkedIn",first_name:"First name",last_name:"Surname",title:"Role",company:"Company"};
function storageKey(pid:string) {return `sourcecado:enrichment-review:${pid}`;}
function savedOperation(pid:string):string|null {try{return localStorage.getItem(storageKey(pid));}catch{return null;}}
function rememberOperation(pid:string,id:string|null) {try{if(id)localStorage.setItem(storageKey(pid),id);else localStorage.removeItem(storageKey(pid));}catch{/* Server receipts remain authoritative. */}}

/** The same one-person review/result surface in preview and full Person File. */
export function EnrichmentPanel({personId}:{personId:string}) {
  const [context,setContext]=useState<PersonEnrichment|null>(null);
  const [busy,setBusy]=useState(false);
  const [problem,setProblem]=useState<string|null>(null);
  const [unconfirmed,setUnconfirmed]=useState(false);
  const [newReview,setNewReview]=useState(false);
  const [acknowledged,setAcknowledged]=useState(false);
  const owner=useRef(personId);owner.current=personId;
  const mounted=useRef(true);
  const operation=useRef<string|null>(savedOperation(personId));
  const sequence=useRef(0);
  const reviewHeading=useRef<HTMLHeadingElement>(null);
  const approving=context?.status==="awaiting_approval";

  async function refresh() {
    const pid=personId,request=++sequence.current;
    try {
      const next=await getPersonEnrichment(pid);
      if(!mounted.current || owner.current!==pid || sequence.current!==request)return false;
      setContext(next);setProblem(null);setUnconfirmed(false);return true;
    } catch {
      if(mounted.current && owner.current===pid && sequence.current===request)setProblem("Couldn’t read lookup status. Saved details are kept.");
      return false;
    }
  }
  useEffect(()=>{
    mounted.current=true;setContext(null);setProblem(null);setNewReview(false);setAcknowledged(false);
    setBusy(false);setUnconfirmed(false);operation.current=savedOperation(personId);
    void refresh();
    return()=>{mounted.current=false;++sequence.current;};
  },[personId]);
  useEffect(()=>{
    const update=()=>void refresh();
    window.addEventListener("sourcecado:board-changed",update);
    return()=>window.removeEventListener("sourcecado:board-changed",update);
  },[personId]);
  useEffect(()=>{if(approving)reviewHeading.current?.focus();},[approving]);
  useEffect(()=>{
    if(context?.status!=="running")return;
    const timer=window.setInterval(()=>void refresh(),5000);
    return()=>window.clearInterval(timer);
  },[context?.status,personId]);

  async function prepare(unknownId?:string) {
    if(!context || busy)return;
    const pid=personId;
    if(!operation.current) {operation.current=newOperationId();rememberOperation(pid,operation.current);}
    setBusy(true);setProblem(null);
    try {
      const next=await preparePersonEnrichment(pid,context,operation.current,unknownId);
      if(!mounted.current || owner.current!==pid)return;
      ++sequence.current;setContext(next);setUnconfirmed(false);setNewReview(false);setAcknowledged(false);
    } catch {
      if(mounted.current && owner.current===pid)setProblem("Couldn’t load this review. Check status or retry preparing the same review.");
    } finally {if(mounted.current && owner.current===pid)setBusy(false);}
  }
  async function decide(decision:"allow"|"deny") {
    const item=context?.pending;
    if(!item || busy || (decision==="allow" && (item.stale_identity || unconfirmed)))return;
    const pid=personId;
    setBusy(true);setProblem(null);
    try {
      await decideApproval(item.id,decision);
      if(!mounted.current || owner.current!==pid)return;
      operation.current=null;rememberOperation(pid,null);
      if(!await refresh())setUnconfirmed(true);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
    } catch {
      if(mounted.current && owner.current===pid){setUnconfirmed(true);setProblem("The outcome is not confirmed. Check status before another action.");}
    } finally {if(mounted.current && owner.current===pid)setBusy(false);}
  }
  const latest=context?.latest;
  const unknownId=context?.unresolved_unknown_id ?? (context?.status==="outcome_unknown" ? latest?.id : undefined);
  return <section className="person-enrichment" aria-label="Contact enrichment">
    <header><h3>Contact details</h3><span className={`enrichment-state enrichment-${context?.status??"loading"}`} role="status">
      <Glyph name={busy || context?.status==="running" ? "loading" : "info"}/>{context ? (busy ? (approving ? "Submitting decision…" : "Preparing review…") : STATUS[context.status]??"Lookup result") : problem ? "Status unavailable" : "Loading lookup status…"}
    </span></header>
    {context ? <>
      <dl className="enrichment-known">
        <div><dt><Glyph name="mail"/>Email</dt><dd>{context.known.email??"Missing email"}</dd></div>
        <div><dt><Glyph name="phone"/>Phone</dt><dd>{context.known.phone??"Missing phone"}</dd></div>
        <div><dt><Glyph name="link"/>LinkedIn</dt><dd>{context.known.linkedin_url ? "Saved on person file" : "Not recorded"}</dd></div>
      </dl>
      {approving && context.pending ? <div className="enrichment-review" aria-label="Review one contact lookup">
        <h4 ref={reviewHeading} tabIndex={-1}>{context.pending.person??context.person.name}</h4>
        <p>{[context.pending.title,context.pending.company].filter(Boolean).join(" · ")}</p>
        <p>{context.pending.matched_on==="apollo_id" ? "Match: saved Apollo identity" : context.pending.matched_on==="email" ? `Match: ${context.pending.match?.email??context.known.email??"known email"}` : "Match: full name and company"}</p>
        <div className="enrichment-cost"><span>Estimated credits · 1 person</span><strong>{context.pending.credits_estimated??"Unknown"}</strong></div>
        {context.pending.stale_identity ? <p role="alert">These person details changed. Cancel this review and prepare a new one.</p> : null}
        <div className="person-outreach-actions">
          <button type="button" disabled={busy||unconfirmed||context.pending.stale_identity||Boolean(context.problem)} onClick={()=>void decide("allow")}><Icon name="approve"/>Approve lookup once</button>
          <button type="button" disabled={busy} onClick={()=>void decide("deny")}><Icon name="close"/>Cancel lookup</button>
        </div>
      </div> : <>
        {latest && !["running","not_enriched"].includes(context.status) ? <div className="enrichment-result">
          <p>Apollo{latest.fetched_at ? ` · ${new Date(latest.fetched_at).toLocaleString()}` : ""}</p>
          <p>{latest.credits_actual===null ? "Actual credits not reported" : `Actual credits: ${latest.credits_actual}`}</p>
          {latest.fields_applied.length ? <p>Updated: {latest.fields_applied.map(field=>FIELD[field]??field).join(", ")}</p> : null}
          {latest.review_required ? <p role="alert">Apollo couldn’t confirm this is the same person. No details were saved. Check the person’s details before another lookup.</p> : null}
          {latest.conflicts.length ? <div><p>Human corrections were kept. Provider differences need review.</p><ul>{latest.conflicts.map(value=><li key={value.field}>{FIELD[value.field]??value.field}: {value.current??"blank"} → {value.proposed??"blank"}</li>)}</ul></div> : null}
        </div> : null}
        {context.status==="not_enriched" ? <div className="enrichment-cost"><span>Estimated credits · 1 person</span><strong>{context.credits_estimated??"Unknown"}</strong></div> : null}
        {context.problem ? <p role="status">{["no_match_key","ambiguous_match"].includes(context.problem??"") ? "Correct the person’s identity before a lookup." : "Connect Apollo in Connections before a lookup."}</p> : null}
        {context.can_request ? <button type="button" disabled={busy} onClick={()=>void prepare()}><Icon name="assistant"/>Review lookup</button> : null}
        {unknownId && context.status!=="outcome_unknown" ? <p role="status">An earlier lookup has an unknown outcome.</p> : null}
        {unknownId && !newReview ? <button type="button" disabled={busy} onClick={()=>{setNewReview(true);setAcknowledged(false);}}><Icon name="approve"/>Review a new lookup</button> : null}
        {newReview ? <div className="enrichment-review">
          <p>The previous outcome remains unknown. A new lookup is a separate paid attempt.</p>
          <label><input type="checkbox" checked={acknowledged} onChange={event=>setAcknowledged(event.target.checked)}/>I understand this is a new paid attempt</label>
          <button type="button" disabled={busy||!acknowledged} onClick={()=>{operation.current=null;rememberOperation(personId,null);void prepare(unknownId);}}><Icon name="assistant"/>Review new attempt</button>
        </div> : null}
      </>}
      {context.problem==="apollo_not_configured" ? <a href="#/connections/apollo"><Icon name="edit"/>Connect Apollo</a> : null}
      {(["no_match_key","ambiguous_match"].includes(context.problem??"") || context.pending?.stale_identity || latest?.stale_identity || latest?.conflicts.length) ? <a href={`#/people/${encodeURIComponent(personId)}`}><Icon name="edit"/>Review person details</a> : null}
    </> : null}
    {context?.attempts && context.attempts.length>1 ? <details className="enrichment-history"><summary>Lookup history</summary><ol>{context.attempts.map(attempt=><li key={attempt.id}>{STATUS[attempt.status]??"Lookup result"} · {attempt.fetched_at??attempt.created_at??"Time not recorded"} · {attempt.credits_actual===null ? "Actual cost not reported" : `${attempt.credits_actual} credits`}</li>)}</ol></details> : null}
    {problem ? <p role="alert">{problem}</p> : null}
    {problem || context?.status==="running" || context?.status==="outcome_unknown" ? <button type="button" disabled={busy} onClick={()=>void refresh()}><Icon name="refresh"/>Check lookup status</button> : null}
  </section>;
}
