import { useEffect, useState } from "react";

import { getBoard, type Board, type BoardPerson } from "./api";

function label(person: BoardPerson): { name: string; detail: string | null } {
  const name = [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person";
  const detail = [person.title, person.company].filter(Boolean).join(" · ");
  return { name, detail: detail || null };
}

function Bucket({
  id,
  title,
  people,
}: {
  id: string;
  title: string;
  people: BoardPerson[];
}) {
  const headingId = `board-${id}-heading`;
  return (
    <section className="board-bucket" aria-labelledby={headingId}>
      <div className="board-bucket-heading">
        <h2 id={headingId}>{title}</h2>
        <span aria-label={`${people.length} people`}>{people.length}</span>
      </div>
      {people.length === 0 ? (
        <p className="board-bucket-empty">None</p>
      ) : (
        <div className="board-rows">
          {people.map((person) => {
            const copy = label(person);
            return (
              <a
                key={person.person_id}
                className="board-row"
                href={`#/people/${encodeURIComponent(person.person_id)}`}
              >
                <strong>{copy.name}</strong>
                {copy.detail ? <span>{copy.detail}</span> : null}
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function BoardView() {
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setFailed(false);
    getBoard().then(
      (next) => {
        if (active) setBoard(next);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt]);

  const empty =
    board !== null &&
    board.open.length === 0 &&
    board.in_conversation.length === 0 &&
    board.done.length === 0;

  return (
    <main className="route-page board-page">
      <header className="board-page-header">
        <p className="eyebrow">Sourcing workspace</p>
        <h1>Board</h1>
        <p>Keep active people moving from open research to completed follow-up.</p>
      </header>
      {board === null && !failed ? <p role="status">Loading board…</p> : null}
      {failed ? (
        <section className="route-error" role="alert">
          <p>Couldn’t load the board.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry
          </button>
        </section>
      ) : null}
      {empty ? (
        <section className="route-empty" role="status">
          <h2>No one in motion</h2>
          <p>Keep a person from sourcing results to add them here.</p>
        </section>
      ) : null}
      {board && !empty ? (
        <div className="board-grid">
          <Bucket id="open" title="Open" people={board.open} />
          <Bucket
            id="conversation"
            title="In conversation"
            people={board.in_conversation}
          />
          <Bucket id="done" title="Done" people={board.done} />
        </div>
      ) : null}
    </main>
  );
}
