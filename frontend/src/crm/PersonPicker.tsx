import { useEffect, useId, useState } from "react";
import { getTaskPeople, type TaskPerson } from "../api";

export function PersonPicker({ value, onChange, disabled }: {
  value: string; onChange: (id: string) => void; disabled: boolean;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [people, setPeople] = useState<TaskPerson[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    getTaskPeople(query, page).then(result => {
      if (!active) return;
      setPeople(result.people); setNext(result.next_offset); setLoading(false);
    }, () => { if (active) { setError(true); setLoading(false); } });
    return () => { active = false; };
  }, [query, page, attempt]);
  return <fieldset disabled={disabled} className="task-person-picker">
    <label htmlFor={`${id}-search`}>Find saved person</label>
    <input id={`${id}-search`} type="search" value={query} onChange={event => {
      setQuery(event.target.value); setPage(0); onChange("");
    }} />
    <label htmlFor={`${id}-person`}>Person</label>
    <select id={`${id}-person`} value={value} required disabled={loading || error} onChange={event => onChange(event.target.value)}>
      <option value="">{loading ? "Loading people…" : "Choose a saved person"}</option>
      {people.map(person => <option key={person.person_id} value={person.person_id}>
        {[person.first_name, person.last_name].filter(Boolean).join(" ") || "Unnamed person"}
        {person.company ? ` · ${person.company}` : ""}
      </option>)}
    </select>
    {error ? <p role="alert">Couldn’t load people. <button type="button" onClick={() => setAttempt(n => n + 1)}>Retry people</button></p> : null}
    <div className="task-actions">
      {page > 0 ? <button type="button" onClick={() => { setPage(Math.max(0, page - 50)); onChange(""); }}>Previous people</button> : null}
      {next !== null ? <button type="button" onClick={() => { setPage(next); onChange(""); }}>More people</button> : null}
    </div>
  </fieldset>;
}
