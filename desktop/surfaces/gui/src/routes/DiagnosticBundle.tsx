import { useState } from "react";

import {
  exportDiagnosticBundle,
  previewDiagnosticBundle,
  type DiagnosticBundlePreview,
  type DiagnosticBundleResult,
  type DiagnosticScanMatch,
} from "../api";

type State =
  | { status: "idle" }
  | { status: "working" }
  | { status: "reviewing"; preview: DiagnosticBundlePreview }
  | { status: "saving"; preview: DiagnosticBundlePreview }
  | { status: "saved"; preview: DiagnosticBundlePreview; bundle: DiagnosticBundleResult }
  | { status: "refused"; matches: DiagnosticScanMatch[] }
  | { status: "failed" };

function sizeLabel(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  return `${Math.round(bytes / 102.4) / 10} KB`;
}

/**
 * Review first, save second.
 *
 * The two steps are separate on purpose. Preview assembles and scans the same
 * bundle the save would write, and writes nothing. Nothing here uploads: the
 * save action puts one file in the local state directory and reports where.
 */
export function DiagnosticBundle() {
  const [runId, setRunId] = useState("");
  const [state, setState] = useState<State>({ status: "idle" });

  async function review() {
    setState({ status: "working" });
    try {
      const outcome = await previewDiagnosticBundle({ run_id: runId.trim() });
      setState(
        outcome.status === "refused"
          ? { status: "refused", matches: outcome.matches }
          : { status: "reviewing", preview: outcome.value },
      );
    } catch {
      setState({ status: "failed" });
    }
  }

  async function save(preview: DiagnosticBundlePreview) {
    setState({ status: "saving", preview });
    try {
      const outcome = await exportDiagnosticBundle({ run_id: runId.trim() });
      setState(
        outcome.status === "refused"
          ? { status: "refused", matches: outcome.matches }
          : { status: "saved", preview, bundle: outcome.value },
      );
    } catch {
      setState({ status: "failed" });
    }
  }

  const preview =
    state.status === "reviewing" ||
    state.status === "saving" ||
    state.status === "saved"
      ? state.preview
      : null;

  return (
    <section className="settings-section" aria-labelledby="diagnostic-bundle-heading">
      <h2 id="diagnostic-bundle-heading">Diagnostic bundle</h2>
      <p className="settings-status">
        <span>
          Package one failed or interrupted run as a local file you can review
          before you share it. Sourcecado never uploads a bundle.
        </span>
      </p>

      <div className="bundle-start">
        <label htmlFor="bundle-run-id">Run ID</label>
        <input
          id="bundle-run-id"
          value={runId}
          placeholder="run-…"
          onChange={(event) => setRunId(event.target.value)}
        />
        <button
          type="button"
          onClick={review}
          disabled={!runId.trim() || state.status === "working"}
        >
          {state.status === "working" ? "Preparing…" : "Review bundle"}
        </button>
      </div>

      {state.status === "refused" && (
        <div className="bundle-refused" role="alert">
          <strong>Nothing was written.</strong>
          <span>
            The pre-export scan matched, so Sourcecado refused to build the
            bundle. The matched values are never shown or saved.
          </span>
          <ul aria-label="Scan matches">
            {state.matches.map((match) => (
              <li key={`${match.category}:${match.location}`}>
                <code>{match.category}</code> at <code>{match.location}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {state.status === "failed" && (
        <p className="settings-status settings-status-missing">
          <strong>The bundle could not be prepared</strong>
          <span>Check the run ID and try again.</span>
        </p>
      )}

      {preview && (
        <div className="bundle-preview">
          <dl className="settings-facts">
            <div>
              <dt>Run</dt>
              <dd>{preview.run?.run_id ?? "—"}</dd>
            </div>
            <div>
              <dt>Ended</dt>
              <dd>{preview.run?.state ?? "—"}</dd>
            </div>
            <div>
              <dt>Log records</dt>
              <dd>{preview.counts.log_records}</dd>
            </div>
            <div>
              <dt>State findings</dt>
              <dd>{preview.counts.findings}</dd>
            </div>
          </dl>

          <h3>What this bundle includes</h3>
          <ul className="bundle-categories" aria-label="Included evidence">
            {preview.evidence_categories.map((category) => (
              <li key={category.id}>
                <header>
                  <strong>{category.title}</strong>
                  <span className={category.included ? "bundle-in" : "bundle-out"}>
                    {category.included ? "Included" : "Not in this bundle"}
                  </span>
                </header>
                <p>{category.description}</p>
              </li>
            ))}
          </ul>

          <h3>What is left out</h3>
          <ul className="bundle-excluded" aria-label="Excluded content">
            {preview.excluded.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>

          {state.status !== "saved" && (
            <button
              type="button"
              className="bundle-save"
              onClick={() => save(preview)}
              disabled={state.status === "saving"}
            >
              {state.status === "saving" ? "Saving…" : "Save bundle to this Mac"}
            </button>
          )}

          {state.status === "saved" && (
            <p className="settings-status">
              <strong>Saved</strong>
              <span>
                <code>{state.bundle.path}</code>
              </span>
              <span>
                {sizeLabel(state.bundle.size_bytes)} · sha256{" "}
                <code>{state.bundle.sha256.slice(0, 16)}…</code>
              </span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}
