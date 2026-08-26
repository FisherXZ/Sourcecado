import { useEffect, useState } from "react";

import {
  getBoard,
  getBoardRecord,
  getBoardRecords,
  revertBoardRecord,
  type Board,
  type BoardPerson,
  type SourcingRecord,
  type SourcingRecordDetail,
} from "./api";

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

function recordTitle(record: SourcingRecord): string {
  for (const key of ["name", "title", "question", "summary"]) {
    const value = record.fields[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return `${record.type} ${record.id}`;
}

export function BoardView() {
  const [board, setBoard] = useState<Board | null>(null);
  const [records, setRecords] = useState<SourcingRecord[] | null>(null);
  const [detail, setDetail] = useState<SourcingRecordDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setFailed(false);
    Promise.all([getBoard(), getBoardRecords()]).then(
      ([nextBoard, nextRecords]) => {
        if (!active) return;
        setBoard(nextBoard);
        setRecords(nextRecords.records);
      },
      () => {
        if (active) setFailed(true);
      },
    );
    return () => {
      active = false;
    };
  }, [attempt]);

  useEffect(() => {
    const refresh = () => setAttempt((value) => value + 1);
    window.addEventListener("sourcecado:board-changed", refresh);
    return () => window.removeEventListener("sourcecado:board-changed", refresh);
  }, []);

  async function inspectRecord(record: SourcingRecord) {
    setDetail(await getBoardRecord(record.id));
  }

  async function revertRecord(toVersion: number) {
    if (!detail) return;
    await revertBoardRecord(detail.record.id, {
      toVersion,
      expectedVersion: detail.record.version,
      rationaleSummary: `Restore version ${toVersion} from Board history.`,
    });
    setDetail(await getBoardRecord(detail.record.id));
    setAttempt((value) => value + 1);
  }

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
      <section className="board-index" aria-labelledby="board-index-heading">
        <header>
          <h2 id="board-index-heading">Sourcing index</h2>
          <p>{records?.length ?? 0} structured records</p>
        </header>
        {records === null ? <p role="status">Loading sourcing index…</p> : null}
        {records?.length === 0 ? <p>No structured records yet.</p> : null}
        {records && records.length > 0 ? (
          <ul>
            {records.map((record) => (
              <li key={record.id}>
                <button
                  type="button"
                  aria-label={`Inspect ${recordTitle(record)}`}
                  onClick={() => void inspectRecord(record)}
                >
                  <strong>{recordTitle(record)}</strong>
                  <span>{record.type} · v{record.version}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {detail ? (
          <section className="board-record-history" aria-labelledby="board-history-heading">
            <h3 id="board-history-heading">Version history</h3>
            <p>{recordTitle(detail.record)} · current version {detail.record.version}</p>
            <ol>
              {detail.receipts.map((receipt) => {
                const version = receipt.after?.version;
                return (
                  <li key={receipt.id}>
                    <span>{receipt.operation}</span>
                    {typeof version === "number" && version < detail.record.version ? (
                      <button type="button" onClick={() => void revertRecord(version)}>
                        Revert to version {version}
                      </button>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          </section>
        ) : null}
      </section>
    </main>
  );
}
