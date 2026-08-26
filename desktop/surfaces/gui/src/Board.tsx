import { useEffect, useState } from "react";

import { getBoard, type Board, type BoardPerson } from "./api";

function label(person: BoardPerson): string {
  const name = [person.first_name, person.last_name].filter(Boolean).join(" ");
  const job = [person.title, person.company].filter(Boolean).join(" · ");
  return job ? `${name || "Unknown"} · ${job}` : name || "Unknown";
}

function Bucket({
  title,
  people,
}: {
  title: string;
  people: BoardPerson[];
}) {
  return (
    <section className="board-bucket">
      <h2>{title}</h2>
      {people.length === 0 ? (
        <p className="empty">None</p>
      ) : (
        people.map((person) => (
          <a key={person.person_id} className="board-row" href={`#/people/${person.person_id}`}>
            {label(person)}
          </a>
        ))
      )}
    </section>
  );
}

export function BoardView() {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBoard()
      .then(setBoard)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const empty =
    board &&
    board.open.length === 0 &&
    board.in_conversation.length === 0 &&
    board.done.length === 0;

  return (
    <div className="route-page">
      <h1>Board</h1>
      {error ? <p className="empty">{error}</p> : null}
      {empty ? <p className="empty">No one in motion.</p> : null}
      {board && !empty ? (
        <div className="board-grid">
          <Bucket title="Open" people={board.open} />
          <Bucket title="In conversation" people={board.in_conversation} />
          <Bucket title="Done" people={board.done} />
        </div>
      ) : null}
    </div>
  );
}
