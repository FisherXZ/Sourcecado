import { useEffect, useState } from "react";

import {
  getConnectors,
  getSettings,
  setPersona as persistPersona,
  type Connector,
  type Settings,
  type WorkspaceDiagnostics,
} from "../api";
import { WorkspaceSettings } from "./WorkspaceSettings";

type SettingsState =
  | { status: "loading" }
  | { status: "loaded"; settings: Settings; connectors: Connector[] }
  | { status: "failed" };

const PERSONA_CHOICES = [
  {
    id: "sourcing",
    label: "Sourcing agent",
    description: "Research, shortlists, and outreach drafts for review.",
  },
  {
    id: "buddy",
    label: "Local coworker",
    description: "General local help, memory, and review-only drafts.",
  },
] as const;

const EMPTY_WORKSPACE: WorkspaceDiagnostics = {
  docker: {
    cli_available: false,
    daemon_available: false,
    image_available: false,
    available: false,
    image: "python:3.13-slim",
    network: "unrestricted",
  },
  execution_target: "host_fallback",
  host_fallback_enabled: true,
  grants: [],
  directory_requests: [],
  host_approvals: [],
  tasks: [],
};

function connectorStatusLabel(status: string): string {
  if (status === "connected") return "Connected";
  if (status === "configured") return "Configured";
  if (status === "missing") return "Needs setup";
  return "Needs attention";
}

export function SettingsPage() {
  const [state, setState] = useState<SettingsState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [personaPending, setPersonaPending] = useState<string | null>(null);
  const [personaFeedback, setPersonaFeedback] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    setState({ status: "loading" });
    Promise.all([getSettings(), getConnectors()]).then(
      ([settings, connectorBody]) => {
        if (active) {
          setState({ status: "loaded", settings, connectors: connectorBody.connectors });
        }
      },
      () => {
        if (active) setState({ status: "failed" });
      },
    );
    return () => {
      active = false;
    };
  }, [attempt]);

  async function choosePersona(id: string, label: string) {
    if (state.status !== "loaded" || personaPending || state.settings.persona.id === id) return;
    setPersonaPending(id);
    setPersonaFeedback(null);
    try {
      const body = await persistPersona(id);
      setState((current) =>
        current.status === "loaded"
          ? { ...current, settings: { ...current.settings, persona: body.persona } }
          : current,
      );
      setPersonaFeedback({ kind: "success", message: `Persona changed to ${label}.` });
    } catch {
      setPersonaFeedback({
        kind: "error",
        message: "Persona couldn’t be changed. Your previous selection is still active.",
      });
    } finally {
      setPersonaPending(null);
    }
  }

  return (
    <main className="route-page settings-page">
      <h1>Settings</h1>
      {state.status === "loading" && <p role="status">Loading settings…</p>}
      {state.status === "failed" && (
        <section className="route-error" role="alert">
          <h2>Settings couldn’t be loaded</h2>
          <p>Check that Sourcecado is available, then try again.</p>
          <button type="button" onClick={() => setAttempt((value) => value + 1)}>
            Retry loading settings
          </button>
        </section>
      )}
      {state.status === "loaded" && (
        <div className="settings-grid">
          <section className="settings-section" aria-labelledby="operator-heading">
            <h2 id="operator-heading">Operator</h2>
            <dl className="settings-facts">
              <div>
                <dt>Role</dt>
                <dd>Sourcing Director</dd>
              </div>
              <div>
                <dt>Workspace</dt>
                <dd>Local to this device</dd>
              </div>
            </dl>
          </section>

          <section className="settings-card" aria-labelledby="persona-heading">
            <h2 id="persona-heading">On-duty persona</h2>
            <p className="current-persona">
              <span>Currently on duty</span>
              <strong>{state.settings.persona.name}</strong>
            </p>
            <div className="persona-options" role="group" aria-label="Choose on-duty persona">
              {PERSONA_CHOICES.map((choice) => (
                <button
                  key={choice.id}
                  type="button"
                  aria-pressed={state.settings.persona.id === choice.id}
                  aria-busy={personaPending === choice.id || undefined}
                  disabled={personaPending !== null}
                  onClick={() => void choosePersona(choice.id, choice.label)}
                >
                  <strong>{choice.label}</strong>
                  <span>{choice.description}</span>
                </button>
              ))}
            </div>
            {personaFeedback && (
              <p
                className={`settings-feedback ${personaFeedback.kind}`}
                role={personaFeedback.kind === "error" ? "alert" : "status"}
              >
                {personaFeedback.message}
              </p>
            )}
          </section>

          <section className="settings-section" aria-labelledby="model-heading">
            <h2 id="model-heading">Model</h2>
            {state.settings.model && (
              <p className="settings-status">
                <strong>Configured</strong>
                <span>Chat is ready to use the configured model.</span>
              </p>
            )}
            {!state.settings.model && (
              <p className="settings-status settings-status-missing">
                <strong>Not configured</strong>
                <span>Chat needs a configured model before it can run.</span>
              </p>
            )}
          </section>

          <section className="settings-section settings-connectors" aria-labelledby="connectors-heading">
            <h2 id="connectors-heading">Connections</h2>
            <ul aria-label="Connector status">
              {state.connectors.map((connector) => (
                <li key={connector.id}>
                  <span>{connector.title}</span>
                  <strong>{connectorStatusLabel(connector.status)}</strong>
                </li>
              ))}
            </ul>
          </section>

          <WorkspaceSettings
            workspace={state.settings.workspace ?? EMPTY_WORKSPACE}
            onChange={(workspace) =>
              setState((current) =>
                current.status === "loaded"
                  ? {
                      ...current,
                      settings: { ...current.settings, workspace },
                    }
                  : current,
              )
            }
          />
        </div>
      )}
    </main>
  );
}
