import type { CurrentRunMetrics } from "../api";
import { RunMetrics } from "./RunMetrics";

export function ThreadHeader({
  title,
  personaName,
  runMetrics,
}: {
  readonly title: string | null;
  readonly personaName?: string | null;
  readonly runMetrics?: CurrentRunMetrics | null;
}) {
  return (
    <header className="sourcecado-thread-header">
      <div>
        <p className="eyebrow">Sourcing workspace</p>
        <h1>{title?.trim() || "New sourcing conversation"}</h1>
        {runMetrics ? <RunMetrics metrics={runMetrics} /> : null}
      </div>
      {personaName ? (
        <p className="sourcecado-persona-badge">{personaName}</p>
      ) : null}
    </header>
  );
}
