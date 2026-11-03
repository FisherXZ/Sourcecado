import { useEffect, useRef, useState } from "react";

import {
  classifyMemory,
  createMemory,
  forgetMemory,
  getMemories,
  updateMemory,
  type MemoryItem,
} from "../api";
import { Icon } from "../icons";

type MemoryState =
  | { status: "loading" }
  | { status: "loaded"; items: MemoryItem[]; maxChars: number }
  | { status: "failed" };

/** The rail badge counts memories waiting for Keep; tell it to look again. */
function announceChange() {
  window.dispatchEvent(new Event("sourcecado:memory-changed"));
}

function shortDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value.includes("T") ? value : `${value.replace(" ", "T")}Z`);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Memories waiting for Keep first, then newest first. */
function ordered(items: MemoryItem[]): MemoryItem[] {
  return [...items].sort((a, b) => Number(a.in_use) - Number(b.in_use) || b.id - a.id);
}

function MemoryField({
  initial,
  maxChars,
  label,
  onSave,
  onCancel,
}: {
  initial: string;
  maxChars: number;
  label: string;
  onSave: (content: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const trimmed = value.trim();
  const over = trimmed.length > maxChars;

  useEffect(() => {
    field.current?.focus();
  }, []);

  async function save() {
    if (!trimmed || over || busy || trimmed === initial) return;
    setBusy(true);
    setError(null);
    try {
      await onSave(trimmed);
    } catch (cause) {
      // A refused save keeps the text as typed.
      setError(cause instanceof Error ? cause.message : "Couldn’t save. Try again.");
      setBusy(false);
    }
  }

  return (
    <form
      className="mem-field"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <textarea
        ref={field}
        aria-label={label}
        placeholder="Something the assistant should always know"
        rows={2}
        value={value}
        onChange={(event) => {
          setValue(event.target.value);
          setError(null);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void save();
          } else if (event.key === "Escape") {
            onCancel();
          }
        }}
      />
      <div className="mem-field-actions">
        <span className={`mem-count${over ? " is-over" : ""}`}>
          {trimmed.length} / {maxChars}
        </span>
        <button
          type="submit"
          className="sm-btn sm-btn-primary"
          disabled={!trimmed || over || busy || trimmed === initial}
        >
          <Icon name="save" />
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="sm-btn" disabled={busy} onClick={onCancel}>
          <Icon name="close" />
          Cancel
        </button>
      </div>
      {error ? <p className="sm-error" role="alert">{error}</p> : null}
    </form>
  );
}

function MemoryRow({
  item,
  maxChars,
  onChanged,
}: {
  item: MemoryItem;
  maxChars: number;
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "confirm">("view");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      announceChange();
      await onChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That didn’t work. Try again.");
      setBusy(false);
    }
  }

  return (
    <li className="mem-row" aria-label={`Memory #${item.id}`}>
      <code className="mem-id" title={`sourcecado:memory/${item.id}`}>#{item.id}</code>
      {mode === "edit" ? (
        <MemoryField
          initial={item.content}
          maxChars={maxChars}
          label={`Edit memory #${item.id}`}
          onCancel={() => setMode("view")}
          onSave={async (content) => {
            await updateMemory(item.id, content);
            announceChange();
            await onChanged();
          }}
        />
      ) : (
        <>
          <p className="mem-text">{item.content}</p>
          <div className="mem-side">
            {item.in_use ? (
              <span className="mem-date">{shortDate(item.updated_at ?? item.created_at)}</span>
            ) : (
              <span className="sm-pill is-warn" title="The assistant doesn’t use this until you keep it">
                Review
              </span>
            )}
            {mode === "confirm" ? (
              <div className="mem-actions">
                <span className="mem-confirm-text">Delete #{item.id}?</span>
                <button
                  type="button"
                  className="sm-btn sm-btn-danger"
                  disabled={busy}
                  onClick={() => void act(() => forgetMemory(item.id))}
                >
                  <Icon name="trash" />
                  Delete
                </button>
                <button
                  type="button"
                  className="sm-btn"
                  disabled={busy}
                  onClick={() => setMode("view")}
                >
                  <Icon name="close" />
                  Cancel
                </button>
              </div>
            ) : (
              <div className="mem-actions">
                {item.in_use ? null : (
                  <button
                    type="button"
                    className="sm-btn"
                    disabled={busy}
                    aria-label={`Keep memory #${item.id}`}
                    onClick={() => void act(() => classifyMemory(item.id))}
                  >
                    <Icon name="check" />
                    Keep
                  </button>
                )}
                <button
                  type="button"
                  className="sm-icon-btn"
                  aria-label={`Edit memory #${item.id}`}
                  title="Edit"
                  disabled={busy}
                  onClick={() => setMode("edit")}
                >
                  <Icon name="edit" />
                </button>
                <button
                  type="button"
                  className="sm-icon-btn"
                  aria-label={`Delete memory #${item.id}`}
                  title="Delete"
                  disabled={busy}
                  onClick={() => setMode("confirm")}
                >
                  <Icon name="trash" />
                </button>
              </div>
            )}
          </div>
        </>
      )}
      {error ? <p className="sm-error mem-row-error" role="alert">{error}</p> : null}
    </li>
  );
}

export function MemoryPage() {
  const [state, setState] = useState<MemoryState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [creating, setCreating] = useState(false);

  async function refresh() {
    const { items, maxChars } = await getMemories();
    setState({ status: "loaded", items, maxChars });
  }

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    getMemories().then(
      ({ items, maxChars }) => {
        if (active) setState({ status: "loaded", items, maxChars });
      },
      () => {
        if (active) setState({ status: "failed" });
      },
    );
    return () => {
      active = false;
    };
  }, [attempt]);

  let body;
  if (state.status === "loading") {
    body = (
      <div className="mem-list" role="status" aria-label="Loading memory">
        <span className="sm-visually-hidden">Loading memory…</span>
        {[0, 1, 2].map((item) => (
          <div className="sm-skeleton mem-row-skeleton" key={item} aria-hidden="true" />
        ))}
      </div>
    );
  } else if (state.status === "failed") {
    body = (
      <div className="sm-empty" role="alert">
        <p>Memory didn’t load.</p>
        <button type="button" className="sm-btn" onClick={() => setAttempt((value) => value + 1)}>
          <Icon name="refresh" />
          Retry
        </button>
      </div>
    );
  } else if (state.items.length === 0 && !creating) {
    body = (
      <div className="sm-empty" role="status">
        <Icon name="memory" />
        <p>No memories yet</p>
        <button type="button" className="sm-btn sm-btn-primary" onClick={() => setCreating(true)}>
          <Icon name="add" />
          New memory
        </button>
      </div>
    );
  } else {
    const { items, maxChars } = state;
    body = (
      <ul className="mem-list" aria-label="Memories">
        {creating ? (
          <li className="mem-row is-new">
            <MemoryField
              initial=""
              maxChars={maxChars}
              label="New memory"
              onCancel={() => setCreating(false)}
              onSave={async (content) => {
                await createMemory(content);
                setCreating(false);
                announceChange();
                await refresh();
              }}
            />
          </li>
        ) : null}
        {ordered(items).map((item) => (
          <MemoryRow
            key={`${item.id}:${item.updated_at ?? ""}:${item.in_use}`}
            item={item}
            maxChars={maxChars}
            onChanged={refresh}
          />
        ))}
      </ul>
    );
  }

  const count = state.status === "loaded" ? state.items.length : null;
  return (
    <main className="route-page sm-page">
      <header className="sm-titlebar">
        <div className="sm-heading">
          <h1>Memory</h1>
          {count === null ? null : <span className="sm-count">{count}</span>}
        </div>
        {count ? (
          <button
            type="button"
            className="sm-btn sm-btn-primary"
            disabled={creating}
            onClick={() => setCreating(true)}
          >
            <Icon name="add" />
            New memory
          </button>
        ) : null}
      </header>
      {body}
    </main>
  );
}
