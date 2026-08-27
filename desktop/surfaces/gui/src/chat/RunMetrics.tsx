import type { CurrentRunMetrics } from "../api";

function tokenLabel(metrics: CurrentRunMetrics): string {
  const total = metrics.total_tokens;
  return total === null ? "Tokens —" : `${total.toLocaleString()} tokens`;
}

function contextLabel(metrics: CurrentRunMetrics): string {
  if (metrics.context_use_ratio !== null) {
    const percent = metrics.context_use_ratio * 100;
    return `Context ${percent > 0 && percent < 1 ? "<1" : Math.round(percent)}%`;
  }
  if (metrics.current_context_tokens !== null) {
    return `Context ${metrics.current_context_tokens.toLocaleString()} tokens`;
  }
  return "Context —";
}

function elapsedLabel(elapsedMs: number | null): string {
  return elapsedMs === null ? "Elapsed —" : `${(elapsedMs / 1000).toFixed(1)}s`;
}

function costLabel(cost: number | null): string {
  if (cost === null) return "Cost —";
  const digits = cost === 0 ? 2 : cost < 0.0001 ? 6 : cost < 0.01 ? 4 : 2;
  return `$${cost.toFixed(digits)} est.`;
}

function countLabel(value: number, singular: string): string {
  const plural = singular === "retry" ? "retries" : `${singular}s`;
  return `${value} ${value === 1 ? singular : plural}`;
}

export function RunMetrics({ metrics }: { readonly metrics: CurrentRunMetrics }) {
  return (
    <section
      className={`sourcecado-run-metrics sourcecado-run-metrics-${metrics.status}`}
      aria-label="Current run metrics"
    >
      <span>{tokenLabel(metrics)}</span>
      <span>{contextLabel(metrics)}</span>
      <span>{elapsedLabel(metrics.elapsed_ms)}</span>
      <span>{countLabel(metrics.retry_count, "retry")}</span>
      <span>{countLabel(metrics.compaction_count, "compaction")}</span>
      <span>{costLabel(metrics.estimated_cost_usd)}</span>
    </section>
  );
}
