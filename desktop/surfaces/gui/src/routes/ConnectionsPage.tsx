import { useCallback, useEffect, useMemo, useState } from "react";

import {
  connectConnector,
  disconnectConnector,
  getConnectors,
  type Connector,
} from "../api";

type PageState =
  | { status: "loading" }
  | { status: "loaded"; connectors: Connector[] }
  | { status: "failed" };

type ConnectorInteraction =
  | { connectorId: string; kind: "connecting" | "awaiting_return" | "disconnecting" }
  | { connectorId: string; kind: "popup_blocked"; url: string }
  | { connectorId: string; kind: "authorization_failed" | "disconnect_failed" };

const CONNECTOR_MARKS: Record<string, string> = {
  gmail: "GM",
  drive: "DR",
  calendar: "CAL",
  apollo: "AP",
  granola: "GR",
};

function statusLabel(status: Connector["status"]): string {
  if (status === "connected") return "Connected";
  if (status === "available") return "Available";
  if (status === "authorizing") return "Authorizing";
  if (status === "missing_scopes") return "Missing permissions";
  if (status === "degraded") return "Degraded";
  if (status === "reconnect_required") return "Reconnect required";
  return "Connection failed";
}

function ConnectorMark({ connector }: { connector: Connector }) {
  return (
    <span className="connection-mark" aria-hidden="true">
      {CONNECTOR_MARKS[connector.id] || connector.title.slice(0, 2).toUpperCase()}
    </span>
  );
}

function ConnectorRow({ connector }: { connector: Connector }) {
  return (
    <li>
      <a
        className="connection-row"
        href={connector.repairRoute}
        aria-label={`${connector.title}, ${statusLabel(connector.status)}`}
      >
        <ConnectorMark connector={connector} />
        <span className="connection-row-copy">
          <strong>{connector.title}</strong>
          <span>{connector.email || connector.description}</span>
        </span>
        <span className={`connection-status status-${connector.status}`}>
          {statusLabel(connector.status)}
        </span>
      </a>
    </li>
  );
}

function ConnectorGroup({
  id,
  title,
  connectors,
}: {
  id: string;
  title: string;
  connectors: Connector[];
}) {
  if (!connectors.length) return null;
  const headingId = `${id}-connections-heading`;
  return (
    <section
      className="connection-group"
      role="region"
      aria-label={`${title} connections`}
    >
      <div className="connection-group-heading">
        <h2 id={headingId}>{title}</h2>
        <span>{connectors.length}</span>
      </div>
      <ul>
        {connectors.map((connector) => (
          <ConnectorRow key={connector.id} connector={connector} />
        ))}
      </ul>
    </section>
  );
}

function LoadingCatalog() {
  return (
    <div className="connections-loading" aria-busy="true">
      <p className="visually-hidden" role="status">
        Loading connections…
      </p>
      <section className="connection-group" aria-labelledby="loading-connected-heading">
        <div className="connection-group-heading">
          <h2 id="loading-connected-heading">Connected</h2>
        </div>
        <div className="connection-skeleton-list" aria-hidden="true">
          <span className="connection-skeleton-row" />
          <span className="connection-skeleton-row" />
        </div>
      </section>
      <section className="connection-group" aria-labelledby="loading-available-heading">
        <div className="connection-group-heading">
          <h2 id="loading-available-heading">Available</h2>
        </div>
        <div className="connection-skeleton-list" aria-hidden="true">
          <span className="connection-skeleton-row" />
          <span className="connection-skeleton-row" />
          <span className="connection-skeleton-row" />
        </div>
      </section>
    </div>
  );
}

function ConnectionsCatalog({ connectors }: { connectors: Connector[] }) {
  const [query, setQuery] = useState("");
  const matching = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return connectors;
    return connectors.filter((connector) =>
      [connector.title, connector.description, connector.email || ""]
        .join(" ")
        .toLocaleLowerCase()
        .includes(needle),
    );
  }, [connectors, query]);
  const connected = matching.filter((connector) => connector.catalogGroup === "connected");
  const available = matching.filter((connector) => connector.catalogGroup === "available");

  return (
    <div className="connections-catalog">
      <div className="connections-search" role="search">
        <label htmlFor="connections-search">Search connections</label>
        <input
          id="connections-search"
          type="search"
          value={query}
          placeholder="Search by product or account"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {matching.length === 0 ? (
        <section className="route-empty connection-empty">
          <h2>No connectors match</h2>
          <p>Try another product or account name.</p>
          <button type="button" onClick={() => setQuery("")}>
            Clear search
          </button>
        </section>
      ) : (
        <div className="connection-groups">
          <ConnectorGroup id="connected" title="Connected" connectors={connected} />
          <ConnectorGroup id="available" title="Available" connectors={available} />
        </div>
      )}
    </div>
  );
}

function ConnectorDetail({
  connector,
  interaction,
  confirmGoogleDisconnect,
  onConnect,
  onDisconnect,
  onCancelDisconnect,
  onConfirmGoogleDisconnect,
}: {
  connector: Connector;
  interaction: ConnectorInteraction | null;
  confirmGoogleDisconnect: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
  onCancelDisconnect: () => void;
  onConfirmGoogleDisconnect: () => void;
}) {
  const activeInteraction = interaction?.connectorId === connector.id ? interaction : null;
  const connecting =
    connector.status === "authorizing" ||
    activeInteraction?.kind === "connecting" ||
    activeInteraction?.kind === "awaiting_return";
  const disconnecting = activeInteraction?.kind === "disconnecting";
  const canConnect =
    connector.id !== "apollo" &&
    (connector.availableActions.includes("connect") ||
      connector.availableActions.includes("reconnect"));
  const canDisconnect =
    connector.id !== "apollo" && connector.availableActions.includes("disconnect");
  const connectLabel = connector.availableActions.includes("reconnect")
    ? connector.recovery?.category === "grant_scopes"
      ? connector.recovery.actionLabel
      : `Reconnect ${connector.title}`
    : `Connect ${connector.title}`;

  return (
    <section
      className="connection-detail"
      aria-label={`${connector.title} connection details`}
    >
      <a className="connection-back" href="#/connections">
        <span aria-hidden="true">←</span> Back to Connections
      </a>
      <header className="connection-detail-header">
        <ConnectorMark connector={connector} />
        <div>
          <p className="connection-eyebrow">Connection</p>
          <h2>{connector.title}</h2>
        </div>
        <span className={`connection-status status-${connector.status}`}>
          {statusLabel(connector.status)}
        </span>
      </header>
      <p className="connection-description">{connector.description}</p>

      <dl className="connection-facts">
        <div>
          <dt>Account</dt>
          <dd>{connector.email || "No account connected"}</dd>
        </div>
        <div>
          <dt>Health</dt>
          <dd>
            <strong>{connector.health.label}</strong>
            <span>{connector.health.message}</span>
          </dd>
        </div>
      </dl>

      {connector.requiredScopes.length > 0 && (
        <section className="connection-detail-section" aria-labelledby="connection-scopes-heading">
          <h3 id="connection-scopes-heading">Permissions</h3>
          <ul className="connection-permissions">
            {connector.requiredScopes.map((scope) => {
              const missing = connector.missingScopes.includes(scope);
              return (
                <li key={scope}>
                  <span>{scope}</span>
                  <strong>{missing ? "Missing" : "Granted"}</strong>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <section className="connection-detail-section" aria-labelledby="connection-actions-heading">
        <h3 id="connection-actions-heading">Supported actions</h3>
        {connector.supportedActions.length > 0 ? (
          <ul className="connection-supported-actions">
            {connector.supportedActions.map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        ) : (
          <p>No agent actions are currently advertised.</p>
        )}
      </section>

      {connector.recovery && connector.status !== "available" && (
        <aside
          className="connection-recovery"
          role={
            connector.status === "failed" || connector.status === "reconnect_required"
              ? "alert"
              : undefined
          }
        >
          <strong>{connector.recovery.actionLabel}</strong>
          <p>{connector.recovery.message}</p>
        </aside>
      )}

      {connector.id === "apollo" && connector.recovery?.category === "configure" && (
        <aside className="connection-recovery">
          <strong>How to configure Apollo</strong>
          <p>{connector.recovery.message}</p>
          <p>Sourcecado does not display or edit API keys here.</p>
        </aside>
      )}

      {activeInteraction?.kind === "popup_blocked" && (
        <aside className="connection-action-error" role="alert">
          <strong>Authorization window didn’t open</strong>
          <p>Open the authorization page, finish there, then return to Sourcecado.</p>
          <a href={activeInteraction.url} target="_blank" rel="noopener noreferrer">
            Open {connector.title} authorization
          </a>
        </aside>
      )}
      {activeInteraction?.kind === "authorization_failed" && (
        <aside className="connection-action-error" role="alert">
          <strong>Authorization didn’t complete</strong>
          <p>
            The provider or OAuth callback rejected the connection. Check the provider redirect
            URL, then try again.
          </p>
        </aside>
      )}
      {activeInteraction?.kind === "disconnect_failed" && (
        <aside className="connection-action-error" role="alert">
          <strong>Disconnect didn’t complete</strong>
          <p>Check that Sourcecado is available, then try disconnecting again.</p>
        </aside>
      )}

      <div className="connection-controls">
        {canConnect && (
          <button
            type="button"
            className="connection-primary-action"
            aria-label={connecting ? `Connecting ${connector.title}` : connectLabel}
            aria-busy={connecting || undefined}
            disabled={connecting || disconnecting}
            onClick={onConnect}
          >
            {connecting ? "Connecting…" : connectLabel}
          </button>
        )}
        {canDisconnect && (
          <button
            type="button"
            className="connection-secondary-action"
            disabled={connecting || disconnecting}
            onClick={onDisconnect}
          >
            {disconnecting ? "Disconnecting…" : `Disconnect ${connector.title}`}
          </button>
        )}
      </div>

      {confirmGoogleDisconnect && (
        <div
          className="connection-confirmation"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="google-disconnect-heading"
          aria-describedby="google-disconnect-description"
        >
          <h3 id="google-disconnect-heading">Disconnect Google account?</h3>
          <p id="google-disconnect-description">
            Gmail, Google Drive, and Google Calendar share this authorization. Disconnecting it
            removes access from all three connections.
          </p>
          <div>
            <button type="button" onClick={onCancelDisconnect}>
              Keep connected
            </button>
            <button type="button" onClick={onConfirmGoogleDisconnect}>
              Disconnect all three
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

export function ConnectionsPage({ connectorId }: { connectorId?: string }) {
  const [state, setState] = useState<PageState>({ status: "loading" });
  const [interaction, setInteraction] = useState<ConnectorInteraction | null>(null);
  const [confirmGoogleDisconnect, setConfirmGoogleDisconnect] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  const loadConnections = useCallback(async (options?: { initial?: boolean; oauthReturn?: boolean }) => {
    if (options?.initial) setState({ status: "loading" });
    try {
      const body = await getConnectors();
      setState({ status: "loaded", connectors: body.connectors });
      setInteraction((current) => {
        if (!current) return current;
        const refreshed = body.connectors.find((item) => item.id === current.connectorId);
        const authorized =
          refreshed &&
          (refreshed.status === "connected" || refreshed.status === "missing_scopes");
        // A window regaining focus is not evidence authorization failed --
        // but a connector actually reading connected IS evidence it
        // succeeded, no matter which interaction state this was left in.
        // Otherwise a stale "didn't complete" banner can outlive the
        // `awaiting_return` state that produced it and contradict a
        // catalog row that has since flipped to Connected.
        if (authorized) return null;
        if (options?.oauthReturn && current.kind === "awaiting_return") {
          return { connectorId: current.connectorId, kind: "authorization_failed" };
        }
        return current;
      });
      return true;
    } catch {
      if (options?.initial) setState({ status: "failed" });
      setInteraction((current) =>
        current?.kind === "awaiting_return"
          ? { connectorId: current.connectorId, kind: "authorization_failed" }
          : current,
      );
      return false;
    }
  }, []);

  useEffect(() => {
    void loadConnections({ initial: true });
    const onFocus = () => void loadConnections({ oauthReturn: true });
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadAttempt, loadConnections]);

  const selectedConnector =
    state.status === "loaded"
      ? state.connectors.find((connector) => connector.id === connectorId)
      : undefined;

  async function startAuthorization(connector: Connector) {
    if (connector.id === "apollo") return;
    setInteraction({ connectorId: connector.id, kind: "connecting" });
    try {
      const result = await connectConnector(connector.id);
      if (result.opened === false) {
        if (result.url) {
          setInteraction({ connectorId: connector.id, kind: "popup_blocked", url: result.url });
        } else {
          setInteraction({ connectorId: connector.id, kind: "authorization_failed" });
        }
        return;
      }
      setInteraction({ connectorId: connector.id, kind: "awaiting_return" });
    } catch {
      setInteraction({ connectorId: connector.id, kind: "authorization_failed" });
    }
  }

  async function performDisconnect(connector: Connector) {
    if (connector.id === "apollo") return;
    setConfirmGoogleDisconnect(false);
    setInteraction({ connectorId: connector.id, kind: "disconnecting" });
    try {
      await disconnectConnector(connector.id);
      await loadConnections();
      setInteraction(null);
    } catch {
      setInteraction({ connectorId: connector.id, kind: "disconnect_failed" });
    }
  }

  function requestDisconnect(connector: Connector) {
    if (connector.authorizationGroup === "google") {
      setConfirmGoogleDisconnect(true);
      return;
    }
    void performDisconnect(connector);
  }

  return (
    <main className={`route-page connections-page${connectorId ? " has-detail" : ""}`}>
      <header className="connections-page-header">
        <div>
          <h1>Connections</h1>
          <p>Manage the sources Sourcecado can use for research and review-ready work.</p>
        </div>
      </header>

      {state.status === "loading" && <LoadingCatalog />}
      {state.status === "failed" && (
        <section className="route-error" role="alert">
          <h2>Connections couldn’t be loaded</h2>
          <p>Check that Sourcecado is available, then try again.</p>
          <button type="button" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>
            Retry loading connections
          </button>
        </section>
      )}
      {state.status === "loaded" && (
        <div className="connections-layout">
          <ConnectionsCatalog connectors={state.connectors} />
          {connectorId && selectedConnector && (
            <ConnectorDetail
              connector={selectedConnector}
              interaction={interaction}
              confirmGoogleDisconnect={confirmGoogleDisconnect}
              onConnect={() => void startAuthorization(selectedConnector)}
              onDisconnect={() => requestDisconnect(selectedConnector)}
              onCancelDisconnect={() => setConfirmGoogleDisconnect(false)}
              onConfirmGoogleDisconnect={() => void performDisconnect(selectedConnector)}
            />
          )}
          {connectorId && !selectedConnector && (
            <section className="connection-detail connection-not-found">
              <a className="connection-back" href="#/connections">
                <span aria-hidden="true">←</span> Back to Connections
              </a>
              <h2>Connection not found</h2>
              <p>
                <code>{connectorId}</code> is not available in the current Sourcecado catalog.
              </p>
            </section>
          )}
        </div>
      )}
    </main>
  );
}
