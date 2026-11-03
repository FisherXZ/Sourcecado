import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  checkConnectors,
  connectConnector,
  disconnectConnector,
  getConnectors,
  type Connector,
} from "../api";
import { BackToConnections, ConnectorMark, StatusPill } from "../connectionParts";
import { connectorStatus } from "../connectorStatus";
import { Icon } from "../icons";
import {
  MonidConnection,
  monidRowStatus,
  useMonidStatus,
  type MonidState,
} from "../MonidConnection";

type PageState =
  | { status: "loading" }
  | { status: "loaded"; connectors: Connector[] }
  | { status: "failed" };

type ConnectorInteraction =
  | { connectorId: string; kind: "connecting" | "awaiting_return" | "disconnecting" }
  | { connectorId: string; kind: "popup_blocked"; url: string }
  | { connectorId: string; kind: "authorization_failed"; message?: string }
  | { connectorId: string; kind: "disconnect_failed" };

function CatalogRow({
  id,
  title,
  account,
  href,
  status,
  selected,
}: {
  id: string;
  title: string;
  account: string | null;
  href: string;
  status: { label: string; tone: string };
  selected: boolean;
}) {
  return (
    <li>
      <a
        className={`connection-row${account ? "" : " has-no-account"}`}
        href={href}
        aria-label={`${title}, ${status.label}`}
        aria-current={selected ? "page" : undefined}
      >
        <ConnectorMark id={id} />
        <strong>{title}</strong>
        <span className="connection-row-account">{account}</span>
        <StatusPill label={status.label} tone={status.tone} />
        <Icon name="next" />
      </a>
    </li>
  );
}

function ConnectorRow({ connector, selectedId }: { connector: Connector; selectedId?: string }) {
  return (
    <CatalogRow
      selected={connector.id === selectedId}
      id={connector.id}
      title={connector.title}
      account={connector.email}
      href={connector.repairRoute}
      status={connectorStatus(connector)}
    />
  );
}

/** Monid is not a backend connector, so its row is built here with the same parts. */
function MonidRow({ monid, selectedId }: { monid: MonidState; selectedId?: string }) {
  return (
    <CatalogRow
      selected={selectedId === "monid"}
      id="monid"
      title="Monid"
      account={monid.status?.workspace || null}
      href="#/connections/monid"
      status={monidRowStatus(monid)}
    />
  );
}

function ConnectorGroup({
  id,
  title,
  connectors,
  selectedId,
  extra,
}: {
  id: string;
  title: string;
  connectors: Connector[];
  selectedId?: string;
  extra?: ReactNode;
}) {
  const count = connectors.length + (extra ? 1 : 0);
  if (!count) return null;
  return (
    <section className="connection-group" role="region" aria-label={`${title} connections`}>
      <h2 id={`${id}-connections-heading`}>
        {title}
        <span className="connection-count">{count}</span>
      </h2>
      <ul>
        {connectors.map((connector) => (
          <ConnectorRow key={connector.id} connector={connector} selectedId={selectedId} />
        ))}
        {extra}
      </ul>
    </section>
  );
}

function LoadingCatalog() {
  return (
    <div className="connections-catalog" aria-busy="true">
      <p className="sr-only" role="status">
        Loading connections…
      </p>
      {(["Connected", "Available"] as const).map((title, index) => (
        <section className="connection-group" key={title}>
          <h2>{title}</h2>
          <div className="connection-skeleton-list" aria-hidden="true">
            {Array.from({ length: index ? 3 : 2 }, (_, row) => (
              <span className="connection-skeleton-row" key={row} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function ConnectionsCatalog({
  connectors,
  monid,
  query,
  selectedId,
  onClearQuery,
}: {
  connectors: Connector[];
  monid: MonidState;
  query: string;
  selectedId?: string;
  onClearQuery: () => void;
}) {
  const needle = query.trim().toLocaleLowerCase();
  const matching = useMemo(() => {
    if (!needle) return connectors;
    return connectors.filter((connector) =>
      [connector.title, connector.email || ""].join(" ").toLocaleLowerCase().includes(needle),
    );
  }, [connectors, needle]);
  const connected = matching.filter((connector) => connector.catalogGroup === "connected");
  const available = matching.filter((connector) => connector.catalogGroup === "available");
  const monidShown =
    !needle ||
    ["Monid", monid.status?.workspace ?? ""].join(" ").toLocaleLowerCase().includes(needle);
  const monidRow = monidShown ? <MonidRow monid={monid} selectedId={selectedId} /> : null;
  const monidConnected = monidRowStatus(monid).connected;

  if (matching.length === 0 && !monidShown) {
    return (
      <div className="connections-catalog">
        <section className="connection-empty">
          <Icon name="search" />
          <h2>No connections match</h2>
          <button type="button" className="connection-button" onClick={onClearQuery}>
            <Icon name="close" />
            Clear search
          </button>
        </section>
      </div>
    );
  }
  return (
    <div className="connections-catalog">
      <ConnectorGroup
        id="connected"
        title="Connected"
        connectors={connected}
        selectedId={selectedId}
        extra={monidConnected ? monidRow : null}
      />
      <ConnectorGroup
        id="available"
        title="Available"
        connectors={available}
        selectedId={selectedId}
        extra={monidConnected ? null : monidRow}
      />
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
  const status = connectorStatus(connector);
  const activeInteraction = interaction?.connectorId === connector.id ? interaction : null;
  const connecting =
    connector.status === "authorizing" ||
    activeInteraction?.kind === "connecting" ||
    activeInteraction?.kind === "awaiting_return";
  const disconnecting = activeInteraction?.kind === "disconnecting";
  const reconnect = connector.availableActions.includes("reconnect");
  const canConnect =
    connector.id !== "apollo" &&
    (connector.availableActions.includes("connect") || reconnect);
  const canDisconnect =
    connector.id !== "apollo" && connector.availableActions.includes("disconnect");
  const finishSetup = reconnect && connector.recovery?.category === "grant_scopes";
  const connectText = finishSetup ? connector.recovery!.actionLabel : reconnect ? "Reconnect" : "Connect";
  const connectLabel = finishSetup ? connectText : `${connectText} ${connector.title}`;
  const broken = connector.status === "failed" || connector.status === "reconnect_required";

  return (
    <section className="connection-detail" aria-label={`${connector.title} connection details`}>
      <BackToConnections />
      <header className="connection-detail-header">
        <ConnectorMark id={connector.id} />
        <h2>{connector.title}</h2>
        <StatusPill label={status.label} tone={status.tone} />
      </header>

      {connector.email && (
        <p className="connection-attr">
          <Icon name="draft" />
          <span>{connector.email}</span>
        </p>
      )}

      {broken && (
        <p className="connection-alert" role="alert">
          <Icon name="warn" />
          <span>{connector.health.message}</span>
        </p>
      )}

      {connector.id === "apollo" && connector.recovery?.category === "configure" && (
        <p className="connection-attr">
          <Icon name="key" />
          <span>{connector.recovery.message}</span>
        </p>
      )}

      {connector.requiredScopes.length > 0 && (
        <section className="connection-detail-section" aria-labelledby="connection-scopes-heading">
          <h3 id="connection-scopes-heading">Permissions</h3>
          <ul className="connection-list">
            {connector.requiredScopes.map((scope) => {
              // Before the first sign-in nothing is granted, and nothing is "missing" yet.
              const granted = !connector.missingScopes.includes(scope);
              const missing = !granted && connector.status !== "available";
              return (
                <li key={scope} className={missing ? "is-missing" : undefined}>
                  <Icon name={granted ? "check" : missing ? "warn" : "key"} />
                  <span>{scope}</span>
                  {missing && <StatusPill label="Missing" tone="warn" />}
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {connector.supportedActions.length > 0 && (
        <section className="connection-detail-section" aria-labelledby="connection-actions-heading">
          <h3 id="connection-actions-heading">Actions</h3>
          <ul className="connection-list">
            {connector.supportedActions.map((action) => (
              <li key={action}>
                <Icon name="assistant" />
                <span>{action}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {activeInteraction?.kind === "popup_blocked" && (
        <p className="connection-alert" role="alert">
          <Icon name="warn" />
          <span>The sign-in window didn’t open.</span>
          <a href={activeInteraction.url} target="_blank" rel="noopener noreferrer">
            <Icon name="external" />
            Open {connector.title} sign-in
          </a>
        </p>
      )}
      {activeInteraction?.kind === "authorization_failed" && (
        <p className="connection-alert" role="alert">
          <Icon name="warn" />
          <span>{activeInteraction.message || "Sign-in didn’t finish. Try again."}</span>
        </p>
      )}
      {activeInteraction?.kind === "disconnect_failed" && (
        <p className="connection-alert" role="alert">
          <Icon name="warn" />
          <span>Couldn’t disconnect. Try again.</span>
        </p>
      )}

      {(canConnect || canDisconnect) && (
        <div className="connection-controls">
          {canConnect && (
            <button
              type="button"
              className="connection-button is-primary"
              aria-label={connecting ? `Connecting ${connector.title}` : connectLabel}
              aria-busy={connecting || undefined}
              disabled={connecting || disconnecting}
              onClick={onConnect}
            >
              <Icon name={reconnect && !finishSetup ? "refresh" : "link"} />
              {connecting ? "Connecting…" : connectText}
            </button>
          )}
          {canDisconnect && (
            <button
              type="button"
              className="connection-button"
              aria-label={`Disconnect ${connector.title}`}
              disabled={connecting || disconnecting}
              onClick={onDisconnect}
            >
              <Icon name="unlink" />
              {disconnecting ? "Disconnecting…" : "Disconnect"}
            </button>
          )}
        </div>
      )}

      {confirmGoogleDisconnect && (
        <div
          className="connection-confirmation"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="google-disconnect-heading"
          aria-describedby="google-disconnect-description"
        >
          <h3 id="google-disconnect-heading">Disconnect Google account?</h3>
          <p id="google-disconnect-description">Gmail, Drive and Calendar share this sign-in.</p>
          <div className="connection-controls">
            <button type="button" className="connection-button" onClick={onCancelDisconnect}>
              <Icon name="close" />
              Cancel
            </button>
            <button
              type="button"
              className="connection-button is-danger"
              onClick={onConfirmGoogleDisconnect}
            >
              <Icon name="unlink" />
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
  const [checking, setChecking] = useState(false);
  const [query, setQuery] = useState("");
  const monid = useMonidStatus();

  const loadSequence = useRef(0);

  const loadConnections = useCallback(async (options?: { initial?: boolean; oauthReturn?: boolean; force?: boolean }) => {
    const sequence = ++loadSequence.current;
    if (options?.initial) setState({ status: "loading" });
    const apply = (body: { connectors: Connector[] }) => {
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
        // Otherwise a stale "didn't finish" alert can outlive the
        // `awaiting_return` state that produced it and contradict a
        // catalog row that has since flipped to Connected.
        if (authorized) return null;
        if (options?.oauthReturn && current.kind === "awaiting_return") {
          return { connectorId: current.connectorId, kind: "authorization_failed" };
        }
        return current;
      });
    };
    try {
      apply(await getConnectors());
    } catch {
      if (options?.initial) setState({ status: "failed" });
      setInteraction((current) =>
        current?.kind === "awaiting_return"
          ? { connectorId: current.connectorId, kind: "authorization_failed" }
          : current,
      );
      return false;
    }
    // The saved-credential view renders first; the live check then replaces
    // it in the background. A failed check leaves that view in place.
    setChecking(true);
    void (options?.force ? checkConnectors({ force: true }) : checkConnectors())
      .then(
        (checked) => {
          if (sequence === loadSequence.current) apply(checked);
        },
        () => {},
      )
      .finally(() => {
        if (sequence === loadSequence.current) setChecking(false);
      });
    return true;
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
    } catch (error) {
      // Only a refusal the backend explained is shown; other errors stay generic.
      const message =
        error instanceof Error && error.name === "ConnectorStartError" ? error.message : undefined;
      setInteraction({ connectorId: connector.id, kind: "authorization_failed", message });
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
      <header className="connections-titlebar">
        <div className="connections-heading">
          <h1>Connections</h1>
        </div>
        <div className="connections-actions">
          <label className="connections-search">
            <Icon name="search" />
            <input
              type="search"
              aria-label="Search connections"
              placeholder="Search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <button
            type="button"
            className={`connection-icon-button${checking ? " is-spinning" : ""}`}
            aria-label="Check connections"
            title={checking ? "Checking connections…" : "Check connections"}
            aria-busy={checking || undefined}
            disabled={checking || state.status !== "loaded"}
            onClick={() => void loadConnections({ force: true })}
          >
            <Icon name="refresh" />
          </button>
        </div>
      </header>
      {state.status === "loading" && <LoadingCatalog />}
      {state.status === "failed" && (
        <section className="route-error" role="alert">
          <h2>Connections couldn’t be loaded</h2>
          <button
            type="button"
            className="connection-button"
            aria-label="Retry loading connections"
            onClick={() => setLoadAttempt((attempt) => attempt + 1)}
          >
            <Icon name="refresh" />
            Retry
          </button>
        </section>
      )}
      {state.status === "loaded" && (
        <div className="connections-layout">
          <ConnectionsCatalog
            connectors={state.connectors}
            monid={monid}
            query={query}
            selectedId={connectorId}
            onClearQuery={() => setQuery("")}
          />
          {connectorId === "monid" ? <MonidConnection {...monid} /> : null}
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
          {connectorId && connectorId !== "monid" && !selectedConnector && (
            <section className="connection-detail" aria-label="Connection not found">
              <BackToConnections />
              <h2>Connection not found</h2>
            </section>
          )}
        </div>
      )}
    </main>
  );
}
