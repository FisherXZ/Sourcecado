import { useId } from "react";

import type { LivingBrief } from "../api";
import type { DomainRendererProps } from "../chat/toolRegistry";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function fieldLabels(brief: LivingBrief): string {
  const labels: Record<string, string> = {
    who: "Who this is",
    wanted: "What we wanted",
    happened: "What happened",
    they_want: "What they want",
  };
  const values = brief.handoff.stale_fields.map((field) => labels[field] ?? field);
  if (values.length < 2) return values[0] ?? "";
  return `${values.slice(0, -1).join(", ")} and ${values.at(-1)}`;
}

function stateLabel(value: string | null): string {
  if (!value) return "Not started";
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

export function LivingBriefResult({ result, status }: DomainRendererProps) {
  const headingId = useId();
  const payload = record(result);
  const brief = record(payload?.brief) as LivingBrief | null;
  if (status === "error" || payload?.partial === true || !brief) {
    return (
      <section
        className="sourcecado-living-brief sourcecado-living-brief-partial"
        aria-label="Partial living brief"
      >
        <h3>Living brief is partial</h3>
        <p>Board is unavailable.</p>
        <p>Evidence already gathered remains available in this conversation.</p>
      </section>
    );
  }
  const handoff = brief.handoff;
  return (
    <section
      className="sourcecado-living-brief"
      aria-labelledby={headingId}
    >
      <header>
        <div>
          <h3 id={headingId}>Living brief for {brief.who || "this person"}</h3>
          <p>Complete · Sequence · {stateLabel(brief.state.sequence)}</p>
        </div>
      </header>
      <dl>
        <div>
          <dt>Target</dt>
          <dd>{brief.target?.text || brief.why || "Not recorded"}</dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>{brief.outcome?.text || "None recorded"}</dd>
        </div>
        <div>
          <dt>Last contact</dt>
          <dd>
            {brief.last_contact.direction
              ? `${brief.last_contact.direction} · ${brief.last_contact.at ?? "date not recorded"}`
              : "No outreach sent and no reply received"}
          </dd>
        </div>
        <div>
          <dt>What they want</dt>
          <dd>{brief.wants.text || "Not recorded"}</dd>
        </div>
        <div>
          <dt>Sources</dt>
          <dd>
            {brief.sources.length > 0
              ? brief.sources
                  .map((source) => source.replace(/^./, (letter) => letter.toUpperCase()))
                  .join(", ")
              : "None recorded"}
          </dd>
        </div>
      </dl>
      <section aria-label="Learned">
        <h4>Learned</h4>
        {brief.evidence.length > 0 ? (
          <ul>{brief.evidence.map((claim) => <li key={claim.id}>{claim.text}</li>)}</ul>
        ) : (
          <p>Nothing filed yet.</p>
        )}
      </section>
      {brief.conflicts.length > 0 ? (
        <section aria-label="Conflicts">
          <h4>Conflicts</h4>
          <ul>{brief.conflicts.map((claim) => <li key={claim.id}>{claim.text}</li>)}</ul>
        </section>
      ) : null}
      <section aria-label="Knowledge gaps">
        <h4>Knowledge gaps</h4>
        {brief.gaps.length > 0 ? (
          <ul>{brief.gaps.map((claim) => <li key={claim.id}>{claim.text}</li>)}</ul>
        ) : (
          <p>Nothing is open.</p>
        )}
      </section>
      {brief.artifacts.length > 0 ? (
        <section aria-label="Artifacts">
          <h4>Artifacts</h4>
          <ul>{brief.artifacts.map((claim) => <li key={claim.id}>{claim.text}</li>)}</ul>
        </section>
      ) : null}
      <section aria-label="Successor handoff">
        <h4>Successor handoff</h4>
        <dl>
          <div><dt>Who this is</dt><dd>{handoff.who || "Not recorded"}</dd></div>
          <div><dt>What we wanted</dt><dd>{handoff.wanted || "Not recorded"}</dd></div>
          <div><dt>What happened</dt><dd>{handoff.happened || "Not recorded"}</dd></div>
          <div><dt>What they want</dt><dd>{handoff.they_want || "Not recorded"}</dd></div>
        </dl>
        {handoff.generated ? (
          <p>Draft for review. Nothing was saved.</p>
        ) : handoff.freshness_unknown ? (
          <p>Saved handoff. Its saved version was not recorded.</p>
        ) : (
          <p>Saved at version {handoff.version}.</p>
        )}
        {handoff.stale ? <p>Review outdated fields: {fieldLabels(brief)}.</p> : null}
      </section>
    </section>
  );
}
