import { useEffect, useState, type ReactNode } from "react";

import { InInspector } from "./inInspector";
import type { InspectorTarget } from "./Inspector";
import { InspectorIcon } from "./InspectorIcons";
import {
  STEP_STATE_LABEL,
  formatDuration,
  formatTiming,
  humanizeKey,
  safeExternalUrl,
} from "./inspectorFormat";
import { StatusIcon, ToolIcon } from "./ProcessIcons";

/** Re-renders each second while `active`, so a running step shows a live duration. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** The steps of this turn, each with its state and how long it took. */
export function ProgressPane({ target }: { readonly target: InspectorTarget }) {
  const steps = target.steps ?? [];
  const live =
    target.state === "running" ||
    steps.some((step) => step.state === "running" && step.completedAt === undefined);
  const now = useNow(live);
  const timing = formatTiming(target.timing, target.state === "running", now);
  return (
    <div className="sourcecado-inspector-progress">
      {timing ? <p className="sourcecado-inspector-timing">{timing}</p> : null}
      <ol className="sourcecado-inspector-steps" aria-label="Steps in this turn">
        {steps.map((step) => {
          const took =
            step.startedAt === undefined
              ? null
              : step.completedAt !== undefined
                ? step.completedAt - step.startedAt
                : step.state === "running"
                  ? now - step.startedAt
                  : null;
          const plain = step.state === "done" || step.state === "running";
          return (
            <li
              key={step.id}
              className={`sourcecado-inspector-step sourcecado-inspector-step-${step.state}`}
              aria-current={step.id === target.id ? "step" : undefined}
            >
              <ToolIcon name={step.icon} />
              <span className="sourcecado-inspector-step-label">{step.label}</span>
              {took !== null ? (
                <span className="sourcecado-inspector-step-time">{formatDuration(took)}</span>
              ) : null}
              <StatusIcon state={step.state} />
              <span className={plain ? "sr-only" : "sourcecado-inspector-step-state"}>
                {STEP_STATE_LABEL[step.state]}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

const MAX_CELL_CHARS = 500;

function clip(text: string): string {
  return text.length > MAX_CELL_CHARS ? `${text.slice(0, MAX_CELL_CHARS - 1).trimEnd()}…` : text;
}

function isScalar(value: unknown): boolean {
  return value === null || value === undefined || typeof value !== "object";
}

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "string") return value === "" ? "Empty" : clip(value);
  return String(value);
}

function compactJson(value: unknown): string {
  try {
    return clip(JSON.stringify(value) ?? String(value));
  } catch {
    return "Unreadable value";
  }
}

type Row = { readonly label: string; readonly value: string; readonly code?: boolean };

function rowsOf(value: object, prefix: string, depth: number): Row[] {
  const rows: Row[] = [];
  for (const [key, item] of Object.entries(value)) {
    const label = prefix ? `${prefix} · ${humanizeKey(key)}` : humanizeKey(key);
    if (isScalar(item)) {
      rows.push({ label, value: scalarText(item) });
    } else if (Array.isArray(item)) {
      rows.push(
        item.length === 0
          ? { label, value: "None" }
          : item.every(isScalar)
            ? { label, value: clip(item.map(scalarText).join(", ")) }
            : { label, value: compactJson(item), code: true },
      );
    } else if (depth < 1 && Object.keys(item as object).length > 0) {
      rows.push(...rowsOf(item as object, label, depth + 1));
    } else {
      rows.push({ label, value: compactJson(item), code: true });
    }
  }
  return rows;
}

/**
 * A readable key and value table for arguments, a result or a context record.
 * A row whose value is the `skip` text is left out, so a line that the panel
 * already shows above the table does not show twice.
 */
export function ValueTable({
  value,
  skip,
}: {
  readonly value: unknown;
  readonly skip?: string | null;
}) {
  if (isScalar(value)) {
    return <p className="sourcecado-inspector-text">{scalarText(value)}</p>;
  }
  const all = Array.isArray(value)
    ? [{ label: "Items", value: compactJson(value), code: true } as Row]
    : rowsOf(value as object, "", 0);
  const rows = all.filter((row) => row.value !== skip);
  if (rows.length === 0) {
    return all.length === 0 ? <p className="sourcecado-inspector-empty">None</p> : null;
  }
  return (
    <table className="sourcecado-inspector-table">
      <tbody>
        {rows.map((row, index) => (
          <tr key={index}>
            <th scope="row">{row.label}</th>
            <td>{row.code ? <code>{row.value}</code> : row.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function rawJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return "Unreadable value";
  }
}

/** True when there is something for the Output view to show besides its empty note. */
export function hasOutput(target: InspectorTarget): boolean {
  return (
    (target.card !== undefined && target.card !== null) ||
    Boolean(target.preview) ||
    target.result !== undefined ||
    target.stale === true ||
    target.truncated === true ||
    (target.status === "loading" && target.state === undefined)
  );
}

/** True when the Context view has a provider, a link, a tool name or arguments. */
export function hasContext(target: InspectorTarget): boolean {
  return Boolean(target.provider || target.toolName || target.externalUrl) || target.args !== undefined;
}

/** The result as the thread's card first, and the raw JSON behind a toggle. */
export function OutputPane({ target }: { readonly target: InspectorTarget }) {
  const [view, setView] = useState<"card" | "raw">("card");
  const hasResult = target.result !== undefined;
  const hasCard = target.card !== undefined && target.card !== null;
  const showRaw = hasResult && view === "raw";
  return (
    <div className="sourcecado-inspector-output">
      {target.stale || target.truncated ? (
        <p className="sourcecado-inspector-flags">
          {target.stale ? <span className="sourcecado-inspector-badge">Cached stale</span> : null}
          {target.truncated ? <span className="sourcecado-inspector-badge">Truncated</span> : null}
        </p>
      ) : null}
      {hasResult ? (
        <div className="sourcecado-inspector-toggle" role="group" aria-label="Output view">
          <button type="button" aria-pressed={view === "card"} onClick={() => setView("card")}>
            <InspectorIcon name="card" />
            {hasCard ? "Card" : "Summary"}
          </button>
          <button type="button" aria-pressed={view === "raw"} onClick={() => setView("raw")}>
            <InspectorIcon name="raw" />
            Raw
          </button>
        </div>
      ) : null}
      {showRaw ? (
        <pre className="sourcecado-inspector-raw">{rawJson(target.result)}</pre>
      ) : hasCard ? (
        <div className="sourcecado-inspector-card">
          <InInspector value>{target.card}</InInspector>
        </div>
      ) : target.status === "loading" && target.state === undefined ? (
        <div className="sourcecado-inspector-skeleton" aria-label="Loading detail" />
      ) : (
        <>
          {target.preview ? <p className="sourcecado-inspector-text">{target.preview}</p> : null}
          {hasResult ? <ValueTable value={target.result} skip={target.preview} /> : null}
          {!target.preview && !hasResult ? (
            <p className="sourcecado-inspector-empty">{target.note ?? "No output recorded."}</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** The provider, the source link and the arguments, as a table. */
export function ContextPane({ target }: { readonly target: InspectorTarget }) {
  const externalUrl = safeExternalUrl(target.externalUrl);
  const rows: { readonly label: string; readonly value: ReactNode }[] = [];
  if (target.provider) rows.push({ label: "Provider", value: target.provider });
  if (target.toolName) rows.push({ label: "Tool", value: <code>{target.toolName}</code> });
  if (externalUrl) {
    rows.push({
      label: "Source",
      value: (
        <span className="sourcecado-inspector-source">
          <a
            href={externalUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="sourcecado-inspector-external"
            aria-label="Open externally"
            title="Open externally"
          >
            <InspectorIcon name="external" />
          </a>
          <span>{hostOf(externalUrl)}</span>
        </span>
      ),
    });
  } else if (target.externalUrl) {
    rows.push({ label: "Source", value: "External URL unavailable" });
  }
  const hasArgs = target.args !== undefined;
  if (rows.length === 0 && !hasArgs) {
    return <p className="sourcecado-inspector-empty">No context recorded.</p>;
  }
  return (
    <div className="sourcecado-inspector-context">
      {rows.length > 0 ? (
        <table className="sourcecado-inspector-table">
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <th scope="row">{row.label}</th>
                <td>{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {hasArgs ? (
        <>
          <h3>Arguments</h3>
          <ValueTable value={target.args} />
        </>
      ) : null}
    </div>
  );
}
