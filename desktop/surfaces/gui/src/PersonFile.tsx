import { useEffect, useState } from "react";

import { getPerson, setPersonSequence, type PersonFile } from "./api";

const STATES = [
  { id: "open", label: "Open" },
  { id: "in_conversation", label: "In conversation" },
  { id: "done", label: "Done" },
] as const;

export function PersonFileView({ personId }: { personId: string }) {
  const [file, setFile] = useState<PersonFile | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [moving, setMoving] = useState<string | null>(null);
  const [moveFailed, setMoveFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setFailed(false);
    setFile(null);
    getPerson(personId).then(
      (next) => {
        if (active) setFile(next);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt, personId]);

  async function move(state: string) {
    if (moving) return;
    setMoving(state);
    setMoveFailed(false);
    try {
      await setPersonSequence(personId, state);
      setFile(await getPerson(personId));
    } catch {
      setMoveFailed(true);
    } finally {
      setMoving(null);
    }
  }

  if (failed) {
    return (
      <main className="route-page person-page">
        <h1>Person</h1>
        <section className="route-error" role="alert">
          <p>Couldn’t load this person file.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
          <a href="#/board">Back to Board</a>
        </section>
      </main>
    );
  }

  if (!file) {
    return (
      <main className="route-page person-page" aria-busy="true">
        <h1>Person</h1>
        <p role="status">Loading person file…</p>
      </main>
    );
  }

  return (
    <main className="route-page person-page">
      <header className="person-page-header">
        <a className="person-back-link" href="#/board">← Board</a>
        <p className="eyebrow">Person file</p>
        <h1>{file.brief.who || "Person"}</h1>
        {file.brief.why ? <p>{file.brief.why}</p> : null}
      </header>

      <section className="person-sequence" aria-labelledby="person-sequence-heading">
        <h2 id="person-sequence-heading">Sequence state</h2>
        <div className="person-sequence-actions">
          {STATES.map((state) => {
            const active = file.person.sequence_state === state.id;
            return (
              <button
                key={state.id}
                type="button"
                className={`person-sequence-action ${active ? "is-active" : ""}`}
                aria-pressed={active}
                disabled={moving !== null}
                onClick={() => void move(state.id)}
              >
                {moving === state.id ? "Updating…" : state.label}
              </button>
            );
          })}
        </div>
        {moveFailed ? (
          <p className="person-sequence-error" role="alert">
            Couldn’t update the sequence state. Try again.
          </p>
        ) : null}
      </section>

      <div className="person-summary-grid">
        <section className="person-summary-card">
          <h2>Knowledge gaps</h2>
          <p>{file.brief.missing.join(", ") || "None recorded"}</p>
        </section>
        <section className="person-summary-card">
          <h2>Sources</h2>
          <p>{file.brief.sources.join(", ") || "None recorded"}</p>
        </section>
      </div>

      <section className="person-section" aria-labelledby="person-learned-heading">
        <h2 id="person-learned-heading">Learned</h2>
        {file.brief.learned.length === 0 ? (
          <p className="person-empty">Nothing filed yet.</p>
        ) : (
          <ul>
            {file.brief.learned.map((line) => <li key={line}>{line}</li>)}
          </ul>
        )}
      </section>

      <section className="person-section" aria-labelledby="person-timeline-heading">
        <h2 id="person-timeline-heading">Timeline</h2>
        {file.timeline.length === 0 ? (
          <p className="person-empty">No sourcing activity yet.</p>
        ) : (
          <div className="person-timeline">
            {file.timeline.map((event) => (
              <article key={event.event_id} className="person-timeline-entry">
                <p className="person-timeline-meta">{event.source} · {event.kind}</p>
                <p>{event.summary}</p>
              </article>
            ))}
          </div>
        )}
      </section>
    </main>
  );
}
