import { useEffect, useState } from "react";

import { getPerson, setPersonSequence, type PersonFile } from "./api";

const STATES = [
  { id: "open", label: "Open" },
  { id: "in_conversation", label: "In conversation" },
  { id: "done", label: "Done" },
];

export function PersonFileView({ personId }: { personId: string }) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPerson(personId)
      .then(setFile)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, [personId]);

  async function move(state: string) {
    await setPersonSequence(personId, state);
    setFile(await getPerson(personId));
  }

  if (error) {
    return (
      <div className="route-page">
        <h1>Person</h1>
        <p className="empty">{error}</p>
      </div>
    );
  }
  if (!file) {
    return (
      <div className="route-page">
        <h1>Person</h1>
        <p className="empty">Loading…</p>
      </div>
    );
  }

  return (
    <div className="route-page">
      <p className="eyebrow">
        <a href="#/board">Board</a>
      </p>
      <h1>{file.brief.who || "Person"}</h1>
      <p>{file.brief.why}</p>
      <p className="eyebrow">Missing: {file.brief.missing.join(", ") || "none"}</p>
      <p className="eyebrow">Sources: {file.brief.sources.join(", ") || "none"}</p>
      <div className="approval-actions">
        {STATES.map((state) => (
          <button
            key={state.id}
            type="button"
            className={file.person.sequence_state === state.id ? "allow" : "strip-btn"}
            onClick={() => move(state.id).catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))}
          >
            {state.label}
          </button>
        ))}
      </div>
      <h2>Learned</h2>
      {file.brief.learned.length === 0 ? (
        <p className="empty">Nothing filed yet.</p>
      ) : (
        <ul>
          {file.brief.learned.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      <h2>Timeline</h2>
      {file.timeline.map((event) => (
        <article key={event.event_id} className="tool-card">
          <p className="tool-name">
            {event.source} · {event.kind}
          </p>
          <p className="tool-result">{event.summary}</p>
        </article>
      ))}
    </div>
  );
}
