import type { ActivePerson, CurrentRunMetrics } from "../api";
import { RunMetrics } from "./RunMetrics";

export function ThreadHeader({
  title,
  activePerson,
  personaName,
  runMetrics,
}: {
  readonly title: string | null;
  readonly activePerson?: ActivePerson | null;
  readonly personaName?: string | null;
  readonly runMetrics?: CurrentRunMetrics | null;
}) {
  return (
    <header className="sourcecado-thread-header">
      <div>
        <p className="eyebrow">Sourcing workspace</p>
        <h1>{title?.trim() || "New sourcing conversation"}</h1>
        {activePerson ? (
          <a
            className="sourcecado-active-person"
            href={`#/people/${encodeURIComponent(activePerson.person_id)}`}
            aria-label={`Active person: ${activePerson.label}`}
          >
            {activePerson.label}
          </a>
        ) : null}
        {runMetrics ? <RunMetrics metrics={runMetrics} /> : null}
      </div>
      {personaName ? (
        <p className="sourcecado-persona-badge">{personaName}</p>
      ) : null}
    </header>
  );
}
