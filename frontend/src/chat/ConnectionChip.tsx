import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ActionIcon, StatusIcon } from "./ProcessIcons";
import type { ConnectionChangeEvent } from "./protocol";

const ConnectionContext = createContext<ConnectionChangeEvent | null>(null);

/** The newest connection change the chat socket reported, or null before any. */
export function ConnectionProvider({
  value,
  children,
}: {
  readonly value: ConnectionChangeEvent | null;
  readonly children: ReactNode;
}) {
  return (
    <ConnectionContext.Provider value={value}>{children}</ConnectionContext.Provider>
  );
}

/** How long "Reconnected" stays after the socket returns. */
const RESTORED_MS = 3000;

/**
 * One chip above the composer while the chat socket is down. It counts the
 * reconnect attempts, then says "Reconnected" for a moment and leaves.
 *
 * It sits in the normal flow of the composer zone, under the usage line and
 * above the message box. Nothing is positioned over the line, so the two
 * never overlap. The region is `aria-live` so each change is announced once;
 * it carries no `role="status"` because the thread already has the one status
 * region for run announcements.
 */
export function ConnectionChip() {
  const connection = useContext(ConnectionContext);
  const status = connection?.status ?? "connected";
  const [restored, setRestored] = useState(false);
  const wasDown = useRef(false);

  useEffect(() => {
    if (status !== "connected") {
      wasDown.current = true;
      setRestored(false);
      return;
    }
    if (!wasDown.current) return;
    wasDown.current = false;
    setRestored(true);
    const timer = window.setTimeout(() => setRestored(false), RESTORED_MS);
    return () => window.clearTimeout(timer);
  }, [status]);

  return (
    <div className="sourcecado-connection-region" aria-live="polite">
      {status === "reconnecting" ? (
        <p className="sourcecado-connection" data-state="reconnecting">
          <StatusIcon state="running" />
          <span>Reconnecting</span>
          <span className="sourcecado-connection-attempt" aria-hidden="true">
            attempt {connection?.attempt ?? 0}
          </span>
        </p>
      ) : status === "offline" ? (
        <p className="sourcecado-connection" data-state="offline">
          <StatusIcon state="failed" />
          <span>Disconnected</span>
          {connection?.reason ? (
            <span className="sourcecado-connection-reason">{connection.reason}</span>
          ) : null}
          <button
            type="button"
            className="sourcecado-step-icon-button"
            aria-label="Reload Sourcecado"
            title="Reload"
            onClick={() => window.location.reload()}
          >
            <ActionIcon name="retry" />
          </button>
        </p>
      ) : restored ? (
        <p className="sourcecado-connection" data-state="restored">
          <StatusIcon state="done" />
          <span>Reconnected</span>
        </p>
      ) : null}
    </div>
  );
}
