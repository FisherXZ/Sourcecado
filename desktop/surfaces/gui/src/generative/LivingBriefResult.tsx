import { useId } from "react";

import type { LivingBrief } from "../api";
import type { DomainRendererProps } from "../chat/toolRegistry";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function textOrNull(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function stringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function claim(value: unknown): boolean {
  const item = record(value);
  return Boolean(
    item &&
      typeof item.id === "string" &&
      typeof item.text === "string" &&
      typeof item.state === "string" &&
      typeof item.authority === "string" &&
      typeof item.updated_at === "string" &&
      typeof item.truncated === "boolean" &&
      stringArray(item.source_refs),
  );
}

function claimOrNull(value: unknown): boolean {
  return value === null || claim(value);
}

function claimArray(value: unknown): boolean {
  return Array.isArray(value) && value.every(claim);
}

function sourceRef(value: unknown): boolean {
  const item = record(value);
  return Boolean(
    item &&
      typeof item.id === "string" &&
      typeof item.provider === "string" &&
      textOrNull(item.locator) &&
      textOrNull(item.title) &&
      typeof item.observed_at === "string" &&
      textOrNull(item.modified_at) &&
      typeof item.fresh === "boolean" &&
      typeof item.evidence === "string" &&
      typeof item.truncated === "boolean",
  );
}

function livingBriefOf(value: unknown): LivingBrief | null {
  const item = record(value);
  const state = record(item?.state);
  const lastContact = record(item?.last_contact);
  const followUp = record(lastContact?.follow_up);
  const handoff = record(item?.handoff);
  if (
    !item ||
    typeof item.version !== "string" ||
    typeof item.who !== "string" ||
    typeof item.why !== "string" ||
    !stringArray(item.learned) ||
    !stringArray(item.missing) ||
    !stringArray(item.sources) ||
    !claim(item.identity) ||
    !claimOrNull(item.target) ||
    !state ||
    !textOrNull(state.sequence) ||
    !claimOrNull(state.claim) ||
    !claimOrNull(item.outcome) ||
    !lastContact ||
    !textOrNull(lastContact.at) ||
    !textOrNull(lastContact.direction) ||
    typeof lastContact.replied !== "boolean" ||
    !followUp ||
    typeof followUp.needed !== "boolean" ||
    !textOrNull(followUp.reason) ||
    !claimOrNull(lastContact.claim) ||
    !claim(item.wants) ||
    !claimArray(item.evidence) ||
    !claimArray(item.conflicts) ||
    !claimArray(item.gaps) ||
    !claimArray(item.artifacts) ||
    !claimArray(item.claims) ||
    !Array.isArray(item.source_refs) ||
    !item.source_refs.every(sourceRef) ||
    typeof item.restricted_source_count !== "number" ||
    typeof item.partial !== "boolean" ||
    !stringArray(item.partial_sources) ||
    typeof item.omitted !== "number" ||
    !handoff ||
    typeof handoff.who !== "string" ||
    typeof handoff.wanted !== "string" ||
    typeof handoff.happened !== "string" ||
    typeof handoff.they_want !== "string" ||
    typeof handoff.generated !== "boolean" ||
    !stringArray(handoff.source_refs) ||
    !(handoff.version === null || typeof handoff.version === "number") ||
    !textOrNull(handoff.saved_at) ||
    typeof handoff.stale !== "boolean" ||
    !stringArray(handoff.stale_fields) ||
    !stringArray(handoff.truncated_fields) ||
    typeof handoff.freshness_unknown !== "boolean" ||
    typeof item.person_version !== "number"
  ) {
    return null;
  }
  return item as unknown as LivingBrief;
}

function fieldLabels(fields: readonly string[]): string {
  const labels: Record<string, string> = {
    who: "Who this is",
    wanted: "What we wanted",
    happened: "What happened",
    they_want: "What they want",
  };
  const values = fields.map((field) => labels[field] ?? field);
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
  const brief = livingBriefOf(payload?.brief);
  if (status === "loading") {
    return (
      <section className="sourcecado-living-brief" aria-label="Loading living brief">
        <h3>Reading the living brief…</h3>
      </section>
    );
  }
  if (status === "success" && !brief) {
    return (
      <section className="sourcecado-living-brief" aria-label="Legacy person-file receipt">
        <h3>Person file read completed</h3>
        <p>
          This earlier receipt predates the complete living-brief view. Open the current
          person file or ask Sourcecado to read it again.
        </p>
      </section>
    );
  }
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
        {handoff.stale ? (
          <p>Review outdated fields: {fieldLabels(handoff.stale_fields)}.</p>
        ) : null}
        {handoff.truncated_fields.length > 0 ? (
          <p>
            Shortened for this chat view: {fieldLabels(handoff.truncated_fields)}.
          </p>
        ) : null}
      </section>
    </section>
  );
}
