import { useCallback, useEffect, useState } from "react";

import { Icon, type IconName } from "../icons";

/** Fired when any toast shows, so every other toast on screen closes. */
const SHOWN = "sourcecado:undo-toast";
let nextId = 1;

export type UndoToastData = {
  readonly message: string;
  readonly icon?: IconName;
  /** Reverses the change. Without it the toast only confirms. Throw to report a failed undo. */
  readonly onUndo?: () => Promise<void>;
  /** Screen-reader name for the Undo button, such as "Undo completing Send deck". */
  readonly undoLabel?: string;
};

export type ShownToast = UndoToastData & { readonly id: number };

/** One toast at a time across the app: a newer toast anywhere replaces this one. */
export function useUndoToast() {
  const [toast, setToast] = useState<ShownToast | null>(null);
  const show = useCallback((data: UndoToastData) => {
    const id = nextId++;
    setToast({ ...data, id });
    window.dispatchEvent(new CustomEvent<number>(SHOWN, { detail: id }));
  }, []);
  const dismiss = useCallback(() => setToast(null), []);
  useEffect(() => {
    const replaced = (event: Event) => {
      const id = (event as CustomEvent<number>).detail;
      setToast((current) => (current && current.id < id ? null : current));
    };
    window.addEventListener(SHOWN, replaced);
    return () => window.removeEventListener(SHOWN, replaced);
  }, []);
  return { toast, show, dismiss };
}

/**
 * The result of a change, with Undo, at the bottom of the window. It closes
 * itself after `duration` ms, but waits while the pointer or focus is on it.
 */
export function UndoToast({
  toast,
  onDismiss,
  duration = 6000,
}: {
  toast: ShownToast | null;
  onDismiss: () => void;
  duration?: number;
}) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [held, setHeld] = useState(false);
  const id = toast?.id;

  useEffect(() => {
    setBusy(false);
    setFailure(null);
    setHeld(false);
  }, [id]);

  useEffect(() => {
    if (id === undefined || held || busy) return;
    const timer = window.setTimeout(onDismiss, duration);
    return () => window.clearTimeout(timer);
  }, [id, held, busy, failure, duration, onDismiss]);

  async function undo() {
    if (!toast?.onUndo || busy) return;
    setBusy(true);
    setFailure(null);
    try {
      await toast.onUndo();
      onDismiss();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "Couldn’t undo. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="undo-toast-region" role="status" aria-live="polite">
      {toast ? (
        <div
          key={toast.id}
          className={`undo-toast${failure ? " is-failed" : ""}`}
          onMouseEnter={() => setHeld(true)}
          onMouseLeave={() => setHeld(false)}
          onFocus={() => setHeld(true)}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setHeld(false);
          }}
        >
          <Icon name={failure ? "deny" : (toast.icon ?? "check")} />
          <span className="undo-toast-message">{failure ?? toast.message}</span>
          {toast.onUndo && !failure ? (
            <button
              type="button"
              className="undo-toast-undo"
              aria-label={toast.undoLabel}
              disabled={busy}
              onClick={() => void undo()}
            >
              <Icon name="reply" />
              {busy ? "Undoing…" : "Undo"}
            </button>
          ) : null}
          <button
            type="button"
            className="undo-toast-close"
            aria-label="Dismiss"
            title="Dismiss"
            onClick={onDismiss}
          >
            <Icon name="close" />
          </button>
        </div>
      ) : null}
    </div>
  );
}
