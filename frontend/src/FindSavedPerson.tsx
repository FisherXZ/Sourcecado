import { useEffect, useRef, useState } from "react";

import { searchSavedPeople, type SavedPersonMatch } from "./api";

type FinderState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ok"; results: SavedPersonMatch[] }
  | { status: "error" };

const SEQUENCE_CONTEXT: Record<string, string> = {
  open: "Open",
  in_conversation: "In conversation",
  done: "Done",
};

function sequenceContext(person: SavedPersonMatch): string {
  if (person.board_lane === "backlog") return "Saved — not in a sequence";
  return SEQUENCE_CONTEXT[person.board_lane] ?? "Saved";
}

function personName(person: SavedPersonMatch): string {
  return (
    [person.first_name, person.last_name].filter(Boolean).join(" ") ||
    "Unknown person"
  );
}

function initials(person: SavedPersonMatch): string {
  const letters = [person.first_name, person.last_name]
    .filter((value): value is string => typeof value === "string" && value.length > 0)
    .map((value) => Array.from(value)[0])
    .join("")
    .slice(0, 2);
  return letters.toLocaleUpperCase() || "?";
}

/**
 * Contacts-scoped finder for any saved Person File, including people kept
 * before outreach. Opening a result is pure navigation — it never starts a
 * sequence — and closing it leaves the Contacts view untouched.
 */
export function FindSavedPerson({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<FinderState>({ status: "idle" });
  const [selected, setSelected] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  // Return focus to whatever opened the finder when it closes.
  const openerRef = useRef<HTMLElement | null>(
    typeof document !== "undefined" ? (document.activeElement as HTMLElement) : null,
  );

  useEffect(() => {
    inputRef.current?.focus();
    const opener = openerRef.current;
    return () => opener?.focus?.();
  }, []);

  const trimmed = query.trim();

  useEffect(() => {
    if (!trimmed) {
      setState({ status: "idle" });
      return;
    }
    let active = true;
    setState({ status: "loading" });
    const timer = window.setTimeout(() => {
      searchSavedPeople(trimmed).then(
        (payload) => {
          // A newer keystroke (or retry) supersedes this response.
          if (active) setState({ status: "ok", results: payload.results });
        },
        () => {
          if (active) setState({ status: "error" });
        },
      );
    }, 200);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [trimmed, attempt]);

  const results = state.status === "ok" ? state.results : [];

  useEffect(() => {
    setSelected(0);
  }, [query, state.status]);

  function openPerson(person: SavedPersonMatch) {
    window.location.hash = `#/people/${encodeURIComponent(person.person_id)}`;
    onClose();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (results.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelected((index) => Math.min(index + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelected((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const person = results[selected];
      if (person) openPerson(person);
    }
  }

  return (
    <div
      className="command-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="command-dialog find-saved-person"
        role="dialog"
        aria-modal="true"
        aria-labelledby="find-saved-person-title"
        onKeyDown={onKeyDown}
      >
        <h2 id="find-saved-person-title">Find saved person</h2>
        <input
          ref={inputRef}
          type="search"
          role="combobox"
          aria-expanded={results.length > 0}
          aria-controls="find-saved-person-results"
          aria-activedescendant={
            results.length > 0 ? `find-saved-person-option-${selected}` : undefined
          }
          aria-label="Search saved people by name, company, or known email"
          placeholder="Search by name, company, or known email"
          value={query}
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
        {state.status === "idle" ? (
          <p className="find-saved-person-hint">
            Search any saved person, including people kept before outreach.
          </p>
        ) : null}
        {state.status === "loading" ? (
          <p className="find-saved-person-hint" role="status">
            Searching…
          </p>
        ) : null}
        {state.status === "error" ? (
          <div className="find-saved-person-error" role="alert">
            <p>Couldn’t search saved people.</p>
            <button type="button" onClick={() => setAttempt((value) => value + 1)}>
              Retry
            </button>
          </div>
        ) : null}
        {state.status === "ok" && results.length === 0 ? (
          <p className="find-saved-person-hint" role="status">
            No saved person matches “{trimmed}”.
          </p>
        ) : null}
        {results.length > 0 ? (
          <ul
            className="command-results find-saved-person-results"
            id="find-saved-person-results"
            role="listbox"
            aria-label="Saved people"
          >
            {results.map((person, index) => (
              <li key={person.person_id} role="presentation">
                <button
                  type="button"
                  id={`find-saved-person-option-${index}`}
                  role="option"
                  aria-selected={index === selected}
                  className={`find-saved-person-row${index === selected ? " is-active" : ""}`}
                  onMouseEnter={() => setSelected(index)}
                  onClick={() => openPerson(person)}
                >
                  <span className="find-saved-person-avatar" aria-hidden="true">
                    {initials(person)}
                  </span>
                  <span className="find-saved-person-copy">
                    <strong>{personName(person)}</strong>
                    {person.last_name_status === "hidden_by_apollo" ? (
                      <span className="find-saved-person-muted">
                        Surname hidden by Apollo
                      </span>
                    ) : null}
                    <span className="find-saved-person-meta">
                      {person.title || "No title recorded"}
                      {person.company ? ` · ${person.company}` : ""}
                    </span>
                    <span className="find-saved-person-meta">
                      {sequenceContext(person)}
                      {" · "}
                      {person.has_email ? "Email on file" : "No email on file"}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
