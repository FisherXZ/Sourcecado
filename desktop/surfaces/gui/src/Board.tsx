import { useEffect, useState } from "react";

import {
  getBoard,
  refreshReplies,
  type Board,
  type BoardPerson,
  type ReplyRefreshResult,
} from "./api";

function label(person: BoardPerson): { name: string; detail: string | null } {
  const name = [person.first_name, person.last_name].filter(Boolean).join(" ") || "Unknown person";
  const detail = [person.title, person.company].filter(Boolean).join(" · ");
  return { name, detail: detail || null };
}

function day(stamp: string | null | undefined): string | null {
  if (typeof stamp !== "string" || !stamp.trim()) return null;
  return stamp.slice(0, 10);
}

/** Last contact and replied state, in the operator's words. */
function contactLine(person: BoardPerson): string {
  const when = day(person.last_contact_at);
  if (!when) return "No contact yet";
  const who = person.last_contact_direction === "inbound" ? "They replied" : "We wrote";
  return `${who} · ${when}`;
}

const FOLLOW_UP_LABEL: Record<string, string> = {
  reply_unanswered: "Needs follow-up",
  reply_needs_review: "Reply needs review",
};

function FollowUpChip({ person }: { person: BoardPerson }) {
  const reason = person.follow_up?.needed ? person.follow_up.reason : null;
  if (!reason) return null;
  return (
    <span className="board-row-flag">{FOLLOW_UP_LABEL[reason] ?? "Needs follow-up"}</span>
  );
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
                <span className="board-row-contact">
                  {contactLine(person)}
                  <FollowUpChip person={person} />
                </span>
              </a>
            );
          })}
        </div>
      )}
    </section>
  );
}

/** What the refresh did, said plainly. Counts come from the server. */
function replyStatus(result: ReplyRefreshResult): string {
  if (result.status !== "ok") {
    return "Couldn’t reach Gmail. The last checked point is unchanged.";
  }
  const parts: string[] = [];
  if (result.filed > 0) {
    parts.push(`Filed ${result.filed} ${result.filed === 1 ? "reply" : "replies"}.`);
  }
  if (result.unassigned > 0) {
    parts.push(
      `${result.unassigned} ${result.unassigned === 1 ? "reply" : "replies"} need review.`,
    );
  }
  if (parts.length === 0) {
    return `Checked ${result.scanned} ${result.scanned === 1 ? "message" : "messages"}. No new replies.`;
  }
  return parts.join(" ");
}

export function BoardView() {
  const [board, setBoard] = useState<Board | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [checking, setChecking] = useState(false);
  const [replyStatusText, setReplyStatusText] = useState<string | null>(null);

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

  useEffect(() => {
    const refresh = () => setAttempt((value) => value + 1);
    window.addEventListener("sourcecado:board-changed", refresh);
    return () => window.removeEventListener("sourcecado:board-changed", refresh);
  }, []);

  async function checkReplies() {
    if (checking) return;
    setChecking(true);
    setReplyStatusText(null);
    try {
      const result = await refreshReplies();
      setReplyStatusText(replyStatus(result.refresh));
      setBoard(result.board);
    } catch {
      setReplyStatusText("Couldn’t check for replies. Try again.");
    } finally {
      setChecking(false);
    }
  }

  const backlog = board?.backlog ?? [];
  const empty =
    board !== null &&
    backlog.length === 0 &&
    board.open.length === 0 &&
    board.in_conversation.length === 0 &&
    board.done.length === 0;

  return (
    <main className="route-page board-page">
      <header className="board-page-header">
        <p className="eyebrow">Sourcing workspace</p>
        <h1>Board</h1>
        <p>Keep sourced people visible from backlog through completed follow-up.</p>
        <div className="board-page-actions">
          <button type="button" disabled={checking} onClick={() => void checkReplies()}>
            {checking ? "Checking for replies…" : "Check for replies"}
          </button>
          {replyStatusText ? <p role="status">{replyStatusText}</p> : null}
        </div>
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
          <Bucket id="backlog" title="Backlog" people={backlog} />
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
