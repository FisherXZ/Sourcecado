import { useEffect, useState } from "react";
import { Icon } from "./icons";
import { monidRequest } from "./monidApi";
import "./styles/monid.css";

type Status = {configured:boolean; verified:boolean; workspace:string|null; spend_paused:boolean};
export function MonidConnection() {
  const [status,setStatus] = useState<Status|null>(null);
  const [key,setKey] = useState("");
  const [busy,setBusy] = useState(false);
  const [problem,setProblem] = useState<string|null>(null);
  useEffect(()=>{
    let active=true;
    monidRequest("monid/connection").then(next=>{if(active)setStatus(next);},()=>{if(active)setProblem("Couldn’t read the saved Monid connection.");});
    return()=>{active=false;};
  },[]);
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
  return <section className="monid-card" aria-labelledby="monid-connection-heading">
    <div className="monid-heading"><h2 id="monid-connection-heading">Monid sources</h2><span role="status">{label}</span></div>
    <p>Find additional research sources. Each paid lookup needs its own reviewed, once-only approval.</p>
    {status?.workspace ? <p>{status.workspace}</p>:null}
    <form onSubmit={event=>{event.preventDefault();void change("PUT","monid/connection",{api_key:key});}}>
      <label className="task-field">Monid API key<input type="password" autoComplete="new-password" value={key} disabled={busy} onChange={event=>setKey(event.currentTarget.value)} /></label>
      <div className="monid-actions">
        <button type="submit" disabled={busy||!key.trim()}><Icon name="save"/>Save Monid key</button>
        {status?.configured ? <><button type="button" disabled={busy} onClick={()=>void change("POST","monid/check")}><Icon name="refresh"/>Check Monid connection</button>
          <button type="button" disabled={busy} onClick={()=>void change("DELETE","monid/connection")}><Icon name="close"/>Disconnect Monid</button></>:null}
      </div>
    </form>
    <p>Use a key for your personal workspace. Saving and checking it does not start research. The key stays in Sourcecado’s local secret store.</p>
    <a href="https://app.monid.ai/access/api-keys" target="_blank" rel="noreferrer">Manage keys in Monid</a>
    {status?.spend_paused ? <p role="alert">Review the uncertain outcome or reported charge in Monid before reconnecting. No further lookup will start automatically. <a href="#/quarantine">Review uncertain actions</a></p>:null}
    {problem ? <p role="alert">{problem}</p>:null}
  </section>;
}
