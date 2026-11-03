import { useEffect, useState } from "react";
import { Icon } from "./icons";
import { monidRequest } from "./monidApi";
import { BackToConnections, ConnectorMark, StatusPill } from "./connectionParts";
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
      setProblem("Couldn’t update the Monid connection. Check the key and try again.");}
    finally {setKey("");setBusy(false);}
  }
  const pill = monidRowStatus({status,failed});
  return <section className="connection-detail monid-detail" aria-labelledby="monid-connection-heading">
    <BackToConnections/>
    <header className="connection-detail-header">
      <ConnectorMark id="monid"/>
      <h2 id="monid-connection-heading">Monid</h2>
      <StatusPill label={pill.label} tone={pill.tone} role="status"/>
    </header>
    {status?.workspace ? <p className="connection-attr"><Icon name="company"/><span>{status.workspace}</span></p>:null}
    {!status && failed ? <p className="connection-alert" role="alert"><Icon name="warn"/><span>Couldn’t read the saved Monid connection.</span></p>:null}
    {status?.spend_paused ? <p className="connection-alert" role="alert"><Icon name="warn"/><span>Spending stopped. Review the charge in Monid before reconnecting.</span><a href="#/quarantine"><Icon name="open"/>Review uncertain actions</a></p>:null}
    <form className="monid-key-form" onSubmit={event=>{event.preventDefault();void change("PUT","monid/connection",{api_key:key});}}>
      <label className="connection-field">
        <span>API key</span>
        <input type="password" aria-label="Monid API key" autoComplete="new-password" placeholder="Personal workspace key" value={key} disabled={busy} onChange={event=>setKey(event.currentTarget.value)} />
      </label>
      <div className="connection-controls">
        <button type="submit" className="connection-button is-primary" aria-label="Save Monid key" disabled={busy||!key.trim()}><Icon name="save"/>Save key</button>
        {status?.configured ? <><button type="button" className="connection-button" aria-label="Check Monid connection" disabled={busy} onClick={()=>void change("POST","monid/check")}><Icon name="refresh"/>Check</button>
          <button type="button" className="connection-button" aria-label="Disconnect Monid" disabled={busy} onClick={()=>void change("DELETE","monid/connection")}><Icon name="unlink"/>Disconnect</button></>:null}
      </div>
    </form>
    <a className="connection-link" href="https://app.monid.ai/access/api-keys" target="_blank" rel="noreferrer"><Icon name="external"/>Manage keys in Monid</a>
    {problem ? <p className="connection-alert" role="alert"><Icon name="warn"/><span>{problem}</span></p>:null}
  </section>;
}
