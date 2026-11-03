import { useEffect, useRef, useState } from "react";
import { Icon } from "./icons";
import { monidRequest } from "./monidApi";
import "./styles/monid.css";

type Endpoint = {provider:string;endpoint:string;displayName?:string;supported:boolean};
export function MonidResearch({personId,sessionId}:{personId:string;sessionId:string|null}) {
  const [catalogQuery,setCatalogQuery]=useState("");
  const [items,setItems]=useState<Endpoint[]|null>(null);
  const [selected,setSelected]=useState<Endpoint|null>(null);
  const [query,setQuery]=useState("");
  const [count,setCount]=useState(3);
  const [maximum,setMaximum]=useState("");
  const [quote,setQuote]=useState<any>(null);
  const [approval,setApproval]=useState<string|null>(null);
  const [result,setResult]=useState<any>(null);
  const [busy,setBusy]=useState(false);
  const [problem,setProblem]=useState<string|null>(null);
  const queryInput=useRef<HTMLInputElement>(null);
  const reviewHeading=useRef<HTMLHeadingElement>(null);
  const resultHeading=useRef<HTMLHeadingElement>(null);
  const [savedAt,setSavedAt]=useState<string|null>(null);
  useEffect(()=>{if(result&&!savedAt)resultHeading.current?.focus();else if(quote)reviewHeading.current?.focus();else if(selected)queryInput.current?.focus();},[quote,approval,result,selected]);
  useEffect(()=>{
    let live=true;setResult(null);setSavedAt(null);
    // Saved research is a local read; it never contacts Monid.
    monidRequest(`people/${encodeURIComponent(personId)}/monid/results`).then(found=>{
      const latest=Array.isArray(found?.results)?found.results[0]:null;
      if(live&&latest){setResult((current:any)=>current??latest);setSavedAt(latest.saved_at??"");}
    }).catch(()=>{/* The live flow still works without saved history. */});
    return()=>{live=false;};
  },[personId]);
  async function run(work:()=>Promise<void>) {
    if(busy)return;setBusy(true);setProblem(null);
    try {await work();}catch(error){setProblem(error instanceof Error?error.message:"The lookup could not be completed.");}
    finally {setBusy(false);}
  }
  async function discard() {
    if(approval)await monidRequest(`inbox/${encodeURIComponent(approval)}`,"POST",{decision:"deny",scope:"once",actor:"director"});
    setQuote(null);setApproval(null);setResult(null);
  }
  const sources = result?.output?.results ?? (result?.output?.title ? [result.output] : []);
  return <section className="person-section monid-card" aria-labelledby="monid-research-heading">
    <h2 id="monid-research-heading">Monid research</h2>
    <p>Research stays attached to this person as untrusted source material. It does not correct identity or send outreach.</p>
    <a href="#/connections/monid">Configure Monid sources</a>
    <form onSubmit={event=>{event.preventDefault();void run(async()=>{const found=await monidRequest(`monid/catalog?q=${encodeURIComponent(catalogQuery)}&limit=5`);setItems(found.items);});}}>
      <label className="task-field">Search Monid catalog<input value={catalogQuery} maxLength={200} disabled={busy} onChange={event=>setCatalogQuery(event.currentTarget.value)}/></label>
      <button type="submit" disabled={busy}><Icon name="draft"/>Find Monid sources</button>
    </form>
    {items?.length===0?<p role="status">No catalog sources match.</p>:null}
    {items?.map((item,index)=><div className="monid-endpoint" key={`${item.provider}:${item.endpoint}:${index}`}>
      <strong>{item.displayName||`${item.provider} ${item.endpoint}`}</strong><span>{item.provider} · {item.endpoint}</span>
      {item.supported?<button type="button" disabled={busy||Boolean(quote)} onClick={()=>{setSelected(item);setResult(null);setSavedAt(null);}}><Icon name="reply"/>Use {item.provider} {item.endpoint}</button>:<span>Catalog only — not executable in this version</span>}
    </div>)}
    {selected&&!result?<>
      {!sessionId?<p>Open this person’s sourcing chat before reviewing a lookup.</p>:<form onSubmit={event=>{event.preventDefault();void run(async()=>{
        const prepared=await monidRequest(`people/${encodeURIComponent(personId)}/monid/quote`,"POST",{session_id:sessionId,provider:selected.provider,endpoint:selected.endpoint,
          input:{body:{query,numResults:count}},max_cost_usd:maximum});setQuote(prepared.quote);
      });}}>
        <fieldset disabled={busy||Boolean(quote)}>
          <label className="task-field">Research query<input ref={queryInput} value={query} maxLength={1000} required onChange={event=>setQuery(event.currentTarget.value)}/></label>
          <label className="task-field">Maximum results<input type="number" min={1} max={10} value={count} required onChange={event=>setCount(Number(event.currentTarget.value))}/></label>
          <label className="task-field">Maximum USD for this lookup<input type="number" min={0} max={10} step="0.001" value={maximum} required onChange={event=>setMaximum(event.currentTarget.value)}/></label>
          <button type="submit" disabled={!query.trim()||maximum===""}><Icon name="approve"/>Review Monid lookup</button>
        </fieldset>
      </form>}
    </>:null}
    {quote&&!result?<div className="monid-quote" role="region" aria-label="Review exact Monid lookup">
      <h3 ref={reviewHeading} tabIndex={-1}>Review one Monid lookup</h3>
      <dl><dt>Provider</dt><dd>{quote.provider}</dd><dt>Endpoint</dt><dd>{quote.endpoint}</dd><dt>Quoted cost</dt><dd>USD {quote.quoted_cost_usd}</dd><dt>Maximum</dt><dd>USD {quote.max_cost_usd}</dd></dl>
      <pre aria-label="Exact request data">{JSON.stringify(quote.input,null,2)}</pre>
      <p>The current fixed quote is checked again before dispatch. Monid does not offer an atomic per-run charge ceiling.</p>
      <div className="monid-actions">{!approval?<button type="button" disabled={busy} onClick={()=>void run(async()=>{
        const parked=await monidRequest("monid/approvals","POST",{quote_id:quote.quote_id,session_id:sessionId});setApproval(parked.item.id);
      })}><Icon name="approve"/>Request lookup approval</button>:<button type="button" disabled={busy} onClick={()=>void run(async()=>{
        const outcome=await monidRequest(`inbox/${encodeURIComponent(approval)}`,"POST",{decision:"allow",scope:"once",actor:"director"});
        setApproval(null);setSavedAt(null);setResult(outcome.result);if(!outcome.ok)setProblem(outcome.result.error||"The result is unavailable; no automatic retry will run.");
      })}><Icon name="approve"/>Allow one lookup</button>}
      <button type="button" disabled={busy} onClick={()=>void run(discard)}><Icon name="close"/>{approval?"Deny lookup":"Discard quote"}</button></div>
    </div>:null}
    {result?<div className="monid-result" role="region" aria-label="Monid research result">
      <h3 ref={resultHeading} tabIndex={-1}>Monid research result</h3>
      {savedAt!==null?<p>Saved research{savedAt?` · ${new Date(savedAt).toLocaleString()}`:""}</p>:null}
      <p>Status: {{completed:"Completed",running:"Running",failed:"Failed",outcome_unknown:"Outcome unknown — review required"}[String(result.status||result.code)]||"Unknown"}</p><p>Actual cost: {result.actual_cost_usd==null?"Unknown":`USD ${result.actual_cost_usd}`}</p>
      <p>Untrusted research source — verify before using.</p>
      {result.code==="outcome_unknown"?<a href="#/quarantine">Review uncertain action</a>:null}
      {Array.isArray(sources)?sources.filter((source:unknown)=>source!==null&&typeof source==="object"&&!Array.isArray(source)).slice(0,10).map((source:any,index:number)=><article className="monid-endpoint" key={index}>
        <strong>{typeof source.title==="string"?source.title:"Research source"}</strong>
        {typeof source.url==="string"&&/^https:\/\/[^\s]+$/.test(source.url)?<a href={source.url} target="_blank" rel="noreferrer">Open source</a>:null}
        {typeof source.text==="string"?<p>{source.text.slice(0,500)}</p>:null}
      </article>):null}
      <details><summary>Inspect source data</summary><pre>{JSON.stringify(result.output??{},null,2)}</pre></details>
      {result.over_budget?<p role="alert">The reported charge exceeded the approved maximum. Further spending is stopped.</p>:null}
      {result.status==="running"?<button type="button" disabled={busy} onClick={()=>void run(async()=>setResult(await monidRequest(`people/${encodeURIComponent(personId)}/monid/results/${encodeURIComponent(result.approval_id)}`,"POST")))}><Icon name="refresh"/>Check saved run</button>:null}
    </div>:null}
    {busy?<p role="status">Working…</p>:null}{problem?<p role="alert">{problem}</p>:null}
  </section>;
}
