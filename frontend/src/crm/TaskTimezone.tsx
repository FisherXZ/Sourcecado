import { useEffect, useId, useState, type FormEvent } from "react";
import { getTaskPreferences, saveTaskTimezone } from "../api";

export function TaskTimezone() {
  const id = useId();
  const [timezone, setTimezone] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    getTaskPreferences().then(result => { if (active) { setTimezone(result.timezone); setLoaded(true); setFailed(false); } },
      () => { if (active) { setFailed(true); setMessage("Couldn’t load your task timezone."); } });
    return () => { active = false; };
  }, [attempt]);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(null);
    try {
      await saveTaskTimezone(timezone); setFailed(false);
      setMessage("Timezone saved. Existing task dates stay unchanged.");
      window.dispatchEvent(new Event("sourcecado:tasks-changed"));
    } catch {
      setFailed(true); setMessage("Couldn’t save timezone. Enter a valid IANA timezone, such as America/Los_Angeles.");
    } finally { setBusy(false); }
  }
  return <section className="settings-section" aria-labelledby={`${id}-heading`}>
    <h2 id={`${id}-heading`}>Task dates</h2><p>Dates use your saved timezone. Existing dates keep their timezone.</p>
    <form className="task-timezone" onSubmit={event => void save(event)}>
      <label htmlFor={id}>Timezone</label><input id={id} required value={timezone} disabled={!loaded || busy} onChange={e => setTimezone(e.target.value)} />
      <button disabled={!loaded || busy}>{busy ? "Saving…" : "Save timezone"}</button>
    </form>
    {message ? <p role={failed ? "alert" : "status"}>{message}</p> : null}
    {!loaded && failed ? <button type="button" onClick={() => setAttempt(n => n + 1)}>Retry timezone</button> : null}
  </section>;
}
