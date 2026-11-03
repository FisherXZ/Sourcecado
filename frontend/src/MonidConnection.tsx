import { useEffect, useState } from "react";
import { Icon } from "./icons";
import { monidRequest } from "./monidApi";
import "./styles/monid.css";

export type MonidStatus = {configured:boolean; verified:boolean; workspace:string|null; spend_paused:boolean};
export type MonidState = {status:MonidStatus|null; failed:boolean; setStatus:(next:MonidStatus|((current:MonidStatus|null)=>MonidStatus|null))=>void};

/** The saved Monid connection. A local read; it never contacts Monid. */
export function useMonidStatus():MonidState {
  const [status,setStatus] = useState<MonidStatus|null>(null);
  const [failed,setFailed] = useState(false);
  useEffect(()=>{
    let active=true;
    monidRequest("monid/connection").then(next=>{if(active)setStatus(next);},()=>{if(active)setFailed(true);});
    return()=>{active=false;};
  },[]);
  return {status,failed,setStatus};
}

/** The same status vocabulary and tones as the other connector rows. */
export function monidRowStatus({status,failed}:Pick<MonidState,"status"|"failed">):{label:string;tone:"neutral"|"ok"|"warn"|"error";connected:boolean} {
  if(!status) return {label:failed?"Status unavailable":"Checking",tone:"neutral",connected:false};
  if(status.spend_paused) return {label:"Spending stopped",tone:"error",connected:true};
  if(status.verified) return {label:"Connected",tone:"ok",connected:true};
  if(status.configured) return {label:"Configured",tone:"neutral",connected:true};
  return {label:"Not connected",tone:"neutral",connected:false};
}

export function MonidConnection({status,failed,setStatus}:MonidState) {
  const [key,setKey] = useState("");
  const [busy,setBusy] = useState(false);
  const [problem,setProblem] = useState<string|null>(null);
  async function change(method:string,path:string,body?:unknown) {
    if(busy)return;
    setBusy(true);setProblem(null);
    try {setStatus(await monidRequest(path,method,body));}
    catch {if(path.endsWith("/check"))setStatus(current=>current?{...current,verified:false}:current);
      setProblem("Couldn’t update the Monid connection. Check the personal workspace key and try again.");}
    finally {setKey("");setBusy(false);}
  }
  const label = !status ? "Connection status unavailable" : status.spend_paused ? "Spending stopped — review required"
    : status.verified ? "Connected" : status.configured ? "Key saved — check connection" : "Not connected";
  const tone = monidRowStatus({status,failed}).tone;
  return <section className="connection-detail monid-detail" aria-labelledby="monid-connection-heading">
    <a className="connection-back" href="#/connections"><Icon name="back"/>Back to Connections</a>
    <header className="connection-detail-header">
      <span className="connection-mark" aria-hidden="true">MO</span>
      <h2 id="monid-connection-heading">Monid sources</h2>
      <span className={`connection-status tone-${tone}`} role="status">{label}</span>
    </header>
    <p className="connection-description">Paid research sources, approved one lookup at a time.</p>
    {status?.workspace ? <dl className="connection-facts"><div><dt>Workspace</dt><dd>{status.workspace}</dd></div></dl>:null}
    {!status && failed ? <p role="alert">Couldn’t read the saved Monid connection.</p>:null}
    <form className="monid-key-form" onSubmit={event=>{event.preventDefault();void change("PUT","monid/connection",{api_key:key});}}>
      <label className="task-field">Monid API key<input type="password" autoComplete="new-password" value={key} disabled={busy} onChange={event=>setKey(event.currentTarget.value)} /></label>
      <p className="monid-hint">Personal workspace key, kept in the local secret store.</p>
      <div className="connection-controls">
        <button type="submit" className="connection-primary-action" disabled={busy||!key.trim()}><Icon name="save"/>Save Monid key</button>
        {status?.configured ? <><button type="button" className="connection-secondary-action" disabled={busy} onClick={()=>void change("POST","monid/check")}><Icon name="refresh"/>Check Monid connection</button>
          <button type="button" className="connection-secondary-action" disabled={busy} onClick={()=>void change("DELETE","monid/connection")}><Icon name="close"/>Disconnect Monid</button></>:null}
      </div>
    </form>
    <a className="monid-manage" href="https://app.monid.ai/access/api-keys" target="_blank" rel="noreferrer"><Icon name="external"/>Manage keys in Monid</a>
    {status?.spend_paused ? <p role="alert">Review the uncertain outcome or reported charge in Monid before reconnecting. No further lookup will start automatically. <a href="#/quarantine">Review uncertain actions</a></p>:null}
    {problem ? <p role="alert">{problem}</p>:null}
  </section>;
}
