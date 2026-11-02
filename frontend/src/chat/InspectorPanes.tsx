import { useEffect, useMemo, useState, type ReactNode } from "react";

import { InInspector } from "./inInspector";
import type { InspectorTarget } from "./Inspector";
import { InspectorIcon } from "./InspectorIcons";
import {
  STEP_STATE_LABEL,
  formatDuration,
  formatTiming,
  humanizeKey,
  maskUrlCredentials,
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
  return <RowsTable rows={rows} />;
}

function RowsTable({ rows }: { readonly rows: readonly Row[] }) {
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

/** A short line of text is a fact. Anything longer, or shaped like JSON, stays in Raw. */
const FACT_TEXT_LIMIT = 100;
/** Keys that carry the provider's own error text. That text stays in Raw. */
const RAW_ONLY_KEY = /^(error|errors|err|message|detail|details|reason|stack|stacktrace|trace|traceback|exception)$/i;

function isFactText(text: string): boolean {
  return text.length <= FACT_TEXT_LIMIT && !/[{}[\]\n\r]/.test(text);
}

function count(total: number, one: string, many: string): string {
  return `${total} ${total === 1 ? one : many}`;
}

/**
 * The readable form of a result with no card: counts for lists, short values
 * for fields. Long text, nested records and error text do not show. They are
 * counted, and the count points to Raw.
 */
function summaryOf(
  value: object,
  prefix: string,
  depth: number,
): { readonly rows: Row[]; readonly hidden: number } {
  const rows: Row[] = [];
  let hidden = 0;
  for (const [key, item] of Object.entries(value)) {
    if (RAW_ONLY_KEY.test(key)) {
      hidden += 1;
      continue;
    }
    const label = prefix ? `${prefix} · ${humanizeKey(key)}` : humanizeKey(key);
    if (typeof item === "string") {
      if (isFactText(item)) rows.push({ label, value: scalarText(item) });
      else hidden += 1;
    } else if (isScalar(item)) {
      rows.push({ label, value: scalarText(item) });
    } else if (Array.isArray(item)) {
      const facts = item.every((entry) => typeof entry === "string" ? isFactText(entry) : isScalar(entry));
      rows.push({
        label,
        value:
          item.length === 0
            ? "None"
            : facts && item.length <= 5
              ? item.map(scalarText).join(", ")
              : count(item.length, "item", "items"),
      });
    } else if (depth < 1 && Object.keys(item as object).length > 0) {
      const nested = summaryOf(item as object, label, depth + 1);
      rows.push(...nested.rows);
      hidden += nested.hidden;
    } else {
      hidden += 1;
    }
  }
  return { rows, hidden };
}

/** Facts the panel knows (the failure class), then the readable summary of the result. */
export function SummaryTable({
  value,
  skip,
  facts = [],
}: {
  readonly value: unknown;
  readonly skip?: string | null;
  readonly facts?: readonly { readonly label: string; readonly value: string }[];
}) {
  const rows: Row[] = facts.map((fact) => ({ ...fact }));
  let hidden = 0;
  if (value !== undefined) {
    if (typeof value === "string") {
      if (isFactText(value)) rows.push({ label: "Result", value: scalarText(value) });
      else hidden += 1;
    } else if (isScalar(value)) {
      rows.push({ label: "Result", value: scalarText(value) });
    } else if (Array.isArray(value)) {
      rows.push({ label: "Items", value: value.length === 0 ? "None" : count(value.length, "item", "items") });
    } else {
      const summary = summaryOf(value as object, "", 0);
      rows.push(...summary.rows);
      hidden += summary.hidden;
    }
  }
  if (hidden > 0) rows.push({ label: "In Raw", value: count(hidden, "more field", "more fields") });
  const shown = rows.filter((row) => row.value !== skip);
  return shown.length === 0 ? null : <RowsTable rows={shown} />;
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
    Boolean(target.facts?.length) ||
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
  // A password in an address never shows here, in the summary or in Raw.
  const result = useMemo(() => maskUrlCredentials(target.result), [target.result]);
  const preview = maskUrlCredentials(target.preview) as string | null | undefined;
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
        <pre className="sourcecado-inspector-raw">{rawJson(result)}</pre>
      ) : hasCard ? (
        <div className="sourcecado-inspector-card">
          <InInspector value>{target.card}</InInspector>
        </div>
      ) : target.status === "loading" && target.state === undefined ? (
        <div className="sourcecado-inspector-skeleton" aria-label="Loading detail" />
      ) : (
        <>
          {preview ? <p className="sourcecado-inspector-text">{preview}</p> : null}
          {hasResult || target.facts?.length ? (
            <SummaryTable value={result} skip={preview} facts={target.facts} />
          ) : null}
          {!target.preview && !hasResult && !target.facts?.length ? (
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
          <ValueTable value={maskUrlCredentials(target.args)} />
        </>
      ) : null}
    </div>
  );
}
