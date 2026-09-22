import { useCallback, useEffect, useState } from "react";

import { classifyMemory, forgetMemory, getMemoryBacklog, type MemoryBacklog } from "../api";

type BacklogState =
  | { status: "loading" }
  | { status: "loaded"; backlog: MemoryBacklog }
  | { status: "failed" };

export function MemoryPage() {
  const [state, setState] = useState<BacklogState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    getMemoryBacklog()
      .then((backlog) => {
        if (active) setState({ status: "loaded", backlog });
      })
      .catch(() => {
        if (active) setState({ status: "failed" });
      });
    return () => {
      active = false;
    };
  }, [attempt]);

  const refresh = useCallback(async () => {
    const backlog = await getMemoryBacklog();
    setState({ status: "loaded", backlog });
  }, []);

  async function run(action: () => Promise<unknown>, failure: string) {
    setActionError(null);
    try {
      await action();
      await refresh();
    } catch {
      setActionError(failure);
    }
  }

  if (state.status === "loading") {
    return (
      <main className="route-page memory-page">
        <h1>Saved memory</h1>
        <p role="status">Loading saved memory…</p>
      </main>
    );
  }

  if (state.status === "failed") {
    return (
      <main className="route-page memory-page">
        <h1>Saved memory</h1>
        <section className="route-error" role="alert">
          <h2>Saved memory couldn’t be loaded</h2>
          <p>Check that Sourcecado is available, then try again.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry loading saved memory
          </button>
        </section>
      </main>
    );
  }

  const { backlog } = state;
  return (
    <main className="route-page memory-page">
      <h1>Saved memory</h1>
      <p className="memory-counts">
        <strong>{backlog.needs_review} waiting for review</strong>
        <span>{backlog.classified} in use</span>
      </p>
      <p className="memory-explainer">
        Sourcecado still has every one of these. They are not being used until you
        say what each one is. Keep the ones that are a standing preference of
        yours, whoever you are working. A fact about one person belongs on that
        Person File, so file it there and delete the note here.
      </p>
      {actionError && (
        <section className="route-error" role="alert">
          <h2>{actionError}</h2>
          <p>Nothing changed. Try again, or delete the note instead.</p>
        </section>
      )}
      {backlog.items.length === 0 ? (
        <section className="route-empty" role="status">
          <h2>Nothing waiting for review</h2>
          <p>New notes appear here until you say what they are.</p>
        </section>
      ) : (
        <ul className="memory-list" aria-label="Memory waiting for review">
          {backlog.items.map((item) => (
            <li key={item.id} aria-label={`Saved memory ${item.id}`}>
              <article className="memory-card">
                <p className="memory-content">{item.content}</p>
                <p className="memory-meta">
                  <span className="memory-id">#{item.id}</span>
                  <span>Saved {item.created_at.slice(0, 10)}</span>
                </p>
                <div className="memory-actions">
                  <button
                    type="button"
                    className="memory-keep"
                    aria-label={`Keep memory ${item.id} as a global preference`}
                    onClick={() =>
                      void run(
                        () => classifyMemory(item.id),
                        "That memory couldn’t be classified",
                      )
                    }
                  >
                    Keep as global preference
                  </button>
                  <button
                    type="button"
                    className="memory-delete"
                    aria-label={`Delete memory ${item.id}`}
                    onClick={() =>
                      void run(
                        () => forgetMemory(item.id),
                        "That memory couldn’t be deleted",
                      )
                    }
                  >
                    Delete
                  </button>
                </div>
              </article>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
