import type { ReactNode } from "react";

import type { LivingBrief } from "../api";
import type { DomainRendererProps } from "../chat/toolRegistry";
import {
  CardField,
  CardRows,
  Gap,
  InspectButton,
  rawToolTarget,
  ResultCard,
  SkeletonRows,
  type CardState,
} from "./ResultCard";

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

function BriefList({
  label,
  items,
  empty,
}: {
  readonly label: string;
  readonly items: readonly { readonly id: string; readonly text: string }[];
  readonly empty?: ReactNode;
}) {
  return (
    <li className="sourcecado-card-row sourcecado-card-row-stack">
      <section aria-label={label} className="sourcecado-card-section">
        <h4>{label}</h4>
        {items.length > 0 ? (
          <ul>{items.map((claim) => <li key={claim.id}>{claim.text}</li>)}</ul>
        ) : (
          empty ?? null
        )}
      </section>
    </li>
  );
}

function Recorded({ value, none = "Not recorded" }: { value: string; none?: string }) {
  return value ? <>{value}</> : <Gap>{none}</Gap>;
}

export function LivingBriefResult({
  result,
  status,
  toolCallId,
  toolName,
  args,
  failure,
  cardId,
}: DomainRendererProps) {
  const payload = record(result);
  const brief = livingBriefOf(payload?.brief);
  const common = { id: cardId, toolCallIds: [toolCallId], provider: "person" as const };
  if (status === "loading") {
    return (
      <ResultCard {...common} title="Living brief" state="loading">
        <SkeletonRows label="Loading living brief" />
      </ResultCard>
    );
  }
  // The Board read can fail and still say what is partial. That wins over a bare failure.
  const unreadable = "Board is unavailable. Evidence already gathered remains available in this conversation.";
  if (status === "error" && payload?.partial !== true) {
    return (
      <ResultCard
        {...common}
        title="Living brief"
        state={failure?.class === "permission" ? "restricted" : "failed"}
        note={`${failure?.summary ?? "Board is unavailable."} Evidence already gathered remains available in this conversation.`}
      />
    );
  }
  if (status === "success" && !brief && payload?.partial !== true) {
    return (
      <ResultCard
        {...common}
        title="Person file read"
        state="unsupported"
        note="This earlier receipt has no living brief to show. Read the person file again for the current one."
        footer={
          <InspectButton
            target={rawToolTarget({ toolCallId, toolName, args, result }, "Person file read")}
          />
        }
      />
    );
  }
  if (payload?.partial === true || !brief) {
    return (
      <ResultCard {...common} title="Living brief" state="partial" note={unreadable} />
    );
  }
  const handoff = brief.handoff;
  const notes = [
    brief.partial
      ? `Partial: ${brief.partial_sources.join(", ") || "some sources"} unavailable.`
      : null,
    brief.restricted_source_count > 0
      ? `${brief.restricted_source_count} restricted ${brief.restricted_source_count === 1 ? "source is" : "sources are"} not shown.`
      : null,
    handoff.stale
      ? `Successor handoff is out of date: ${fieldLabels(handoff.stale_fields)}.`
      : null,
  ].filter((note): note is string => note !== null);
  const state: CardState = brief.partial
    ? "partial"
    : brief.restricted_source_count > 0
      ? "restricted"
      : handoff.stale
        ? "stale"
        : "ready";
  return (
    <ResultCard
      {...common}
      title={`Living brief for ${brief.who || "this person"}`}
      meta={[`Sequence · ${stateLabel(brief.state.sequence)}`]}
      state={state}
      note={notes.length > 0 ? notes.join(" ") : undefined}
      footer={
        <InspectButton
          target={{
            kind: "artifact",
            id: `living-brief:${toolCallId}`,
            title: `Living brief for ${brief.who || "this person"}`,
            status: "success",
            provider: "Board",
            preview: `Person file version ${brief.person_version}`,
            result: {
              version: brief.version,
              personVersion: brief.person_version,
              sources: brief.source_refs.map((ref) => ({
                provider: ref.provider,
                title: ref.title,
                fresh: ref.fresh,
              })),
            },
          }}
        />
      }
    >
      <CardRows label="Living brief">
        <CardField icon="doc" label="Target">
          <Recorded value={brief.target?.text || brief.why} />
        </CardField>
        <CardField icon="review" label="Outcome">
          <Recorded value={brief.outcome?.text ?? ""} none="None recorded" />
        </CardField>
        <CardField icon="mail" label="Last contact">
          {brief.last_contact.direction ? (
            `${brief.last_contact.direction} · ${brief.last_contact.at ?? "date not recorded"}`
          ) : (
            <Gap>No outreach sent and no reply received</Gap>
          )}
        </CardField>
        <CardField icon="user" label="What they want">
          <Recorded value={brief.wants.text} />
        </CardField>
        <CardField icon="link" label="Sources">
          {brief.sources.length > 0 ? (
            brief.sources
              .map((source) => source.replace(/^./, (letter) => letter.toUpperCase()))
              .join(", ")
          ) : (
            <Gap>None recorded</Gap>
          )}
        </CardField>
        <BriefList
          label="Learned"
          items={brief.evidence}
          empty={<Gap>Nothing filed yet</Gap>}
        />
        {brief.conflicts.length > 0 ? (
          <BriefList label="Conflicts" items={brief.conflicts} />
        ) : null}
        <BriefList
          label="Knowledge gaps"
          items={brief.gaps}
          empty={<p>Nothing is open.</p>}
        />
        {brief.artifacts.length > 0 ? (
          <BriefList label="Artifacts" items={brief.artifacts} />
        ) : null}
        <li className="sourcecado-card-row sourcecado-card-row-stack">
          <section aria-label="Successor handoff" className="sourcecado-card-section">
            <h4>Successor handoff</h4>
            <dl>
              <div><dt>Who this is</dt><dd><Recorded value={handoff.who} /></dd></div>
              <div><dt>What we wanted</dt><dd><Recorded value={handoff.wanted} /></dd></div>
              <div><dt>What happened</dt><dd><Recorded value={handoff.happened} /></dd></div>
              <div><dt>What they want</dt><dd><Recorded value={handoff.they_want} /></dd></div>
            </dl>
            {handoff.generated ? (
              <p>Draft for review. Nothing was saved.</p>
            ) : handoff.freshness_unknown ? (
              <p>Saved handoff. Its saved version was not recorded.</p>
            ) : (
              <p>Saved at version {handoff.version}.</p>
            )}
            {handoff.truncated_fields.length > 0 ? (
              <p>
                Shortened for this chat view: {fieldLabels(handoff.truncated_fields)}.
              </p>
            ) : null}
          </section>
        </li>
      </CardRows>
    </ResultCard>
  );
}
