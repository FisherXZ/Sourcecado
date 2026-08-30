import { useState } from "react";

import type { SessionRow } from "../api";

const DESTINATIONS = [
  { label: "Contacts", hash: "#/board" },
  { label: "Scheduled", hash: "#/scheduled" },
  { label: "Connections", hash: "#/connections" },
  { label: "Skills", hash: "#/skills" },
  { label: "Memory", hash: "#/memory" },
  { label: "Settings", hash: "#/settings" },
];

export function CommandSearch({
  sessions,
  onClose,
  onNavigate,
}: {
  sessions: SessionRow[];
  onClose: () => void;
  onNavigate: (hash: string) => void;
}) {
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const destinations = DESTINATIONS.filter((item) => item.label.toLowerCase().includes(normalized));
  const threads = sessions.filter((session) =>
    (session.title || "New conversation").toLowerCase().includes(normalized)
  );

  return (
    <div className="command-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section
        className="command-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="command-search-title"
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onClose();
          }
        }}
      >
        <h2 id="command-search-title">Search Sourcecado</h2>
        <input
          type="search"
          aria-label="Search destinations and conversations"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          autoFocus
          placeholder="Search conversations and destinations"
        />
        <div className="command-results">
          {destinations.map((item) => (
            <button type="button" key={item.hash} onClick={() => onNavigate(item.hash)}>
              {item.label}
            </button>
          ))}
          {threads.map((session) => (
            <button
              type="button"
              key={session.session_id}
              onClick={() => onNavigate(`#/chat/${encodeURIComponent(session.session_id)}`)}
            >
              {session.title || "New conversation"}
            </button>
          ))}
          {destinations.length === 0 && threads.length === 0 && (
            <p>No matching destinations or conversations</p>
          )}
        </div>
      </section>
    </div>
  );
}
