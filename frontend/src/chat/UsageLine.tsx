import { useEffect, useId, useState, type ReactNode } from "react";

import type { CurrentRunMetrics } from "../api";
import type { SourcecadoStructuredMessage } from "./messageAdapter";
import type { RunBudgetStatus } from "./protocol";

/**
 * The one line above the composer: how full the context window is, what the
 * run cost, and how many steps it used.
 *
 * Every value comes from data the app already has: the current-run telemetry
 * for context, cost and tokens, and the run budget for steps. A value that was
 * not reported reads "unavailable", never 0. After a run ends the line keeps
 * its numbers and says "Last run", because they belong to a finished run.
 *
 * Context share is how full the model's window was on the latest request. It
 * is not plan or account usage, and nothing here is named that way.
 *
 * Compactions come from the run's `turn_end.compaction`, the same data as the
 * receipt in the thread. The backend sends it on every run while a compaction
 * is in effect, so a run counts as compacting only when its generation is
 * higher than any earlier run's. The telemetry's `compaction_count` is not
 * used: the backend does not record a compaction into it during a live run.
 */

/** Context share at which the bar changes color. Compaction starts at 75%. */
const HIGH_CONTEXT_PERCENT = 75;

const LIVE_STATES: ReadonlySet<SourcecadoStructuredMessage["state"]> = new Set([
  "running",
  "stopping",
  "waiting-approval",
]);

export type UsageSource = {
  readonly metrics: CurrentRunMetrics | null;
  readonly budget: RunBudgetStatus | undefined;
  /** True while the numbers belong to the run that is working now. */
  readonly live: boolean;
  /** Compactions the latest run made. A run reports at most one. */
  readonly compactions: number;
  /** The run ended and its final telemetry reading is still on its way. */
  readonly settling: boolean;
  /** The final reading failed, so the values may be from before the run ended. */
  readonly partial: boolean;
};

/** How the read of the run's final telemetry went. */
export type FinalRead = "pending" | "done" | "failed";

/**
 * Chooses which run the line describes.
 *
 * While the thread works, the line describes that run. Telemetry that still
 * holds the previous run is left out, because it is not this run's number.
 * Once the run ends, the line describes that finished run and says "Last run",
 * but only after the final reading is in. A failed final reading leaves the
 * mid-run values on screen, so the line marks them as possibly incomplete.
 */
export function pickUsage(
  messages: readonly SourcecadoStructuredMessage[],
  metrics: CurrentRunMetrics | null,
  running: boolean,
  finalRead: FinalRead = "done",
  endedRunId?: string | null,
): UsageSource | null {
  if (messages.length === 0) return null;
  const runs = messages.filter(
    (message) =>
      message.role === "assistant" &&
      !message.parts.some((part) => part.type === "notice"),
  );
  const latest = runs.at(-1);
  if (!running) {
    // Telemetry from another run is not this run's reading. A run that never
    // got an id (null) has no reading at all. No id given means none is known.
    const own =
      endedRunId === undefined || metrics?.run_id === endedRunId ? metrics : null;
    const before = Math.max(
      0,
      ...runs.slice(0, -1).map((run) => run.compactionGeneration ?? 0),
    );
    return {
      metrics: own,
      budget: latest?.runBudget,
      live: false,
      compactions: (latest?.compactionGeneration ?? 0) > before ? 1 : 0,
      settling: finalRead === "pending",
      partial: finalRead === "failed" && own !== null,
    };
  }
  return {
    metrics: metrics?.status === "running" ? metrics : null,
    // Before the new run's first event, the latest message is the previous run.
    budget: latest && LIVE_STATES.has(latest.state) ? latest.runBudget : undefined,
    live: true,
    compactions: 0,
    settling: false,
    partial: false,
  };
}

function percentOf(metrics: CurrentRunMetrics | null): number | null {
  if (!metrics) return null;
  if (metrics.context_use_ratio !== null) return metrics.context_use_ratio * 100;
  if (
    metrics.current_context_tokens !== null &&
    metrics.context_window_tokens
  ) {
    return (metrics.current_context_tokens / metrics.context_window_tokens) * 100;
  }
  return null;
}

function percentLabel(percent: number): string {
  return `${percent > 0 && percent < 1 ? "<1" : Math.round(percent)}%`;
}

function costLabel(cost: number): string {
  const digits = cost === 0 ? 2 : cost < 0.0001 ? 6 : cost < 0.01 ? 4 : 2;
  return `$${cost.toFixed(digits)}`;
}

function count(value: number): string {
  return value.toLocaleString();
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

function stepsOf(
  budget: RunBudgetStatus | undefined,
): { readonly used: number; readonly limit: number } | null {
  const limit = budget?.limits.model_turns ?? 0;
  return budget && limit > 0
    ? { used: budget.consumed.model_turns, limit }
    : null;
}

function plural(value: number, one: string, many: string): string {
  return `${value} ${value === 1 ? one : many}`;
}

function Icon({ children }: { readonly children: ReactNode }) {
  return (
    <svg
      className="sourcecado-usage-icon"
      viewBox="0 0 16 16"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

const ContextIcon = () => (
  <Icon>
    <path d="M2.5 11.5a5.5 5.5 0 1 1 11 0" />
    <path d="M8 11.5 10.5 7" />
  </Icon>
);
const CostIcon = () => (
  <Icon>
    <circle cx="8" cy="8" r="5.5" />
    <path d="M9.6 6.3c-.3-.6-.9-.9-1.6-.9-.9 0-1.6.5-1.6 1.2 0 1.7 3.2.8 3.2 2.5 0 .7-.7 1.2-1.6 1.2-.8 0-1.4-.3-1.7-.9M8 4.2v1.2M8 10.6v1.2" />
  </Icon>
);
const StepsIcon = () => (
  <Icon>
    <path d="M3 12.5h3v-3h3v-3h4" />
  </Icon>
);
const RetryIcon = () => (
  <Icon>
    <path d="M13 8a5 5 0 1 1-1.6-3.7M13 2.8v2.5h-2.5" />
  </Icon>
);
export const CompactIcon = () => (
  <Icon>
    <path d="M3 3.5h10M3 12.5h10M8 5.5v5M6 8.3l2 2 2-2" />
  </Icon>
);

/** One row of the breakdown. `value` null means the provider did not report it. */
function Row({
  term,
  value,
}: {
  readonly term: string;
  readonly value: string | null;
}) {
  return (
    <>
      <dt>{term}</dt>
      <dd>{value ?? "unavailable"}</dd>
    </>
  );
}

function Breakdown({
  source,
  percent,
  steps,
}: {
  readonly source: UsageSource;
  readonly percent: number | null;
  readonly steps: ReturnType<typeof stepsOf>;
}) {
  const { metrics, budget } = source;
  const consumed = budget?.consumed;
  const limits = budget?.limits;
  const known = (value: number | null | undefined) =>
    value === null || value === undefined ? null : count(value);
  const of = (value: string, limit: string | null) =>
    limit ? `${value} of ${limit}` : value;
  const cost =
    metrics?.estimated_cost_usd == null
      ? null
      : of(
          `${costLabel(metrics.estimated_cost_usd)} est.`,
          limits && limits.estimated_cost_usd > 0
            ? costLabel(limits.estimated_cost_usd)
            : null,
        );
  const time =
    consumed && limits && limits.elapsed_seconds > 0
      ? of(duration(consumed.elapsed_seconds), duration(limits.elapsed_seconds))
      : metrics?.elapsed_ms != null
        ? duration(metrics.elapsed_ms / 1000)
        : null;
  const context =
    percent === null
      ? null
      : metrics?.current_context_tokens != null && metrics.context_window_tokens
        ? `${percentLabel(percent)}, ${of(
            count(metrics.current_context_tokens),
            `${count(metrics.context_window_tokens)} tokens`,
          )}`
        : percentLabel(percent);
  return (
    <dl className="sourcecado-usage-grid">
      <Row term="Context window" value={context} />
      <Row term="Estimated cost" value={cost} />
      <Row
        term="Steps"
        value={steps ? of(count(steps.used), count(steps.limit)) : null}
      />
      {consumed && limits && limits.tool_calls > 0 ? (
        <Row
          term="Tool calls"
          value={of(count(consumed.tool_calls), count(limits.tool_calls))}
        />
      ) : null}
      <Row term="Time" value={time} />
      <Row term="Input tokens" value={known(metrics?.input_tokens)} />
      <Row term="Output tokens" value={known(metrics?.output_tokens)} />
      <Row
        term="Reasoning tokens"
        value={known(metrics?.reasoning_tokens)}
      />
      <Row term="Total tokens" value={known(metrics?.total_tokens)} />
      {metrics && metrics.retry_count > 0 ? (
        <Row term="Retries" value={count(metrics.retry_count)} />
      ) : null}
      {source.compactions > 0 ? (
        <Row term="Compactions" value={count(source.compactions)} />
      ) : null}
    </dl>
  );
}

export function UsageLine({ source }: { readonly source: UsageSource }) {
  const { metrics, compactions, partial } = source;
  // A run that just ended keeps its "this run" label until its final reading is in.
  const live = source.live || source.settling;
  const percent = percentOf(metrics);
  const steps = stepsOf(source.budget);
  const cost = metrics?.estimated_cost_usd ?? null;
  const contextTokens = metrics?.current_context_tokens ?? null;
  const retries = metrics?.retry_count ?? 0;
  const reported =
    percent !== null ||
    contextTokens !== null ||
    cost !== null ||
    steps !== null ||
    retries > 0 ||
    compactions > 0;
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const detailId = useId();
  const open = (hovered || focused) && !dismissed;

  // Escape closes the breakdown wherever focus is, and leaves focus where it
  // is. The window sees the key before the composer, so the first Escape
  // closes the breakdown and does not also stop a running turn.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setDismissed(true);
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [open]);
  const scope = live
    ? "This run"
    : partial
      ? "Last run, may be incomplete"
      : "Last run";
  const unpriced = source.budget?.unpriced_requests ?? 0;

  return (
    <div
      className="sourcecado-usage"
      data-scope={live ? "live" : "last"}
      onMouseEnter={() => {
        setDismissed(false);
        setHovered(true);
      }}
      onMouseLeave={() => setHovered(false)}
    >
      <div
        className="sourcecado-usage-line"
        role="group"
        tabIndex={0}
        aria-label={`Usage, ${scope.toLowerCase()}`}
        aria-describedby={detailId}
        onFocus={() => {
          setDismissed(false);
          setFocused(true);
        }}
        onBlur={() => setFocused(false)}
      >
        {!live && reported ? (
          <span className="sourcecado-usage-scope">{scope}</span>
        ) : null}
        <span className="sourcecado-usage-item">
          <ContextIcon />
          {percent !== null ? (
            <>
              <span
                className="sourcecado-usage-bar"
                role="meter"
                aria-label="Context window used"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.min(100, Math.round(percent))}
                aria-valuetext={percentLabel(percent)}
                data-level={percent >= HIGH_CONTEXT_PERCENT ? "high" : "normal"}
              >
                <span style={{ width: `${Math.min(100, percent)}%` }} />
              </span>
              <span className="sourcecado-usage-value">
                {percentLabel(percent)}
              </span>
            </>
          ) : contextTokens !== null ? (
            <span className="sourcecado-usage-value">
              <span className="sr-only">Context window </span>
              {`${count(contextTokens)} tokens`}
            </span>
          ) : (
            <span className="sourcecado-usage-value">
              <span className="sr-only">
                Context window unavailable
              </span>
              <span aria-hidden="true">—</span>
            </span>
          )}
        </span>
        <span className="sourcecado-usage-item">
          <CostIcon />
          <span className="sourcecado-usage-value">
            <span className="sr-only">Estimated cost </span>
            {cost === null ? (
              <>
                <span className="sr-only">unavailable</span>
                <span aria-hidden="true">—</span>
              </>
            ) : (
              `${costLabel(cost)} est.`
            )}
          </span>
        </span>
        <span className="sourcecado-usage-item">
          <StepsIcon />
          <span className="sourcecado-usage-value">
            {steps === null ? (
              <>
                <span className="sr-only">Steps unavailable</span>
                <span aria-hidden="true">—</span>
              </>
            ) : (
              `${steps.used}/${steps.limit} steps`
            )}
          </span>
        </span>
        {retries > 0 ? (
          <span className="sourcecado-usage-chip">
            <RetryIcon />
            {plural(retries, "retry", "retries")}
          </span>
        ) : null}
        {compactions > 0 ? (
          <span className="sourcecado-usage-chip">
            <CompactIcon />
            {plural(compactions, "compaction", "compactions")}
          </span>
        ) : null}
      </div>
      <div
        id={detailId}
        className="sourcecado-usage-detail"
        role="tooltip"
        hidden={!open}
      >
        <p className="sourcecado-usage-detail-title">{scope}</p>
        <Breakdown source={source} percent={percent} steps={steps} />
        <p className="sourcecado-usage-detail-note">
          {partial
            ? "The final reading failed. These values are from earlier in this run. "
            : ""}
          Context window is the share of the model's window that the latest
          request filled.
          {unpriced > 0
            ? ` ${plural(unpriced, "request has", "requests have")} no price, so the cost is a minimum.`
            : ""}
        </p>
      </div>
    </div>
  );
}
