import { useId, useState } from "react";

import {
  curateApolloCandidates,
  openPersonSourcingChat,
  type ApolloCurationResult,
} from "../api";
import { usePopulateComposer } from "../chat/ComposerDraftContext";
import { useInspector } from "../chat/Inspector";
import type { DomainRendererProps } from "../chat/toolRegistry";
import { hasApolloNameMask, withoutApolloNameMasks } from "../personName";
import {
  CardButton,
  CardField,
  CardRow,
  CardRows,
  Gap,
  Glyph,
  InspectButton,
  rawToolTarget,
  ResultCard,
  SkeletonRows,
  UnsupportedCard,
} from "./ResultCard";

type ApolloCandidate = {
  readonly id: string;
  readonly name: string;
  readonly firstName: string | null;
  readonly lastName: string | null;
  readonly title: string | null;
  readonly company: string | null;
  readonly surnameHidden: boolean;
  readonly hasEmail: boolean;
  readonly phoneStatus: string | null;
  readonly missing: readonly string[];
  readonly raw: Record<string, unknown>;
};

const PAGE_SIZE = 5;

type FailedCandidate = {
  readonly candidate: ApolloCandidate;
  readonly code: string;
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function candidateOf(value: unknown, index: number): ApolloCandidate | null {
  const raw = record(value);
  if (!raw) return null;
  const firstName = text(raw.firstName);
  const apolloLastName = text(raw.lastNameObfuscated);
  const surnameHidden = hasApolloNameMask(apolloLastName);
  const lastName = surnameHidden ? null : apolloLastName;
  const title = text(raw.title);
  const company = text(raw.organizationName);
  const missing = [
    !title ? "title" : null,
    !firstName || (!lastName && !surnameHidden) ? "full name" : null,
    !company ? "company" : null,
  ].filter((field): field is string => field !== null);
  return {
    id: text(raw.apolloId) ?? `candidate-${index + 1}`,
    name: [firstName, lastName].filter(Boolean).join(" ") || "Unnamed candidate",
    firstName,
    lastName,
    title,
    company,
    surnameHidden,
    hasEmail: raw.hasEmail === true,
    phoneStatus: text(raw.directPhoneStatus),
    missing,
    raw,
  };
}

function missingList(fields: readonly string[]): string {
  if (fields.length === 1) return fields[0]!;
  return `${fields.slice(0, -1).join(", ")} and ${fields.at(-1)}`;
}

function displayUrl(value: string): string {
  return value
    .replace(/^https?:\/\/(www\.)?/, "")
    .replace(/[?#].*$/, "")
    .replace(/\/$/, "");
}

function queryLabel(value: unknown): string {
  const args = record(value);
  const company = text(args?.organizationName);
  const titles = Array.isArray(args?.personTitles)
    ? args.personTitles.map(text).filter((title): title is string => title !== null)
    : [];
  if (titles.length > 0 && company) return `${titles.join(", ")} at ${company}`;
  if (titles.length > 0) return titles.join(", ");
  return company ?? "this search";
}

/** The role and company of one person, with a gap marker for each missing half. */
function RoleCell({
  title,
  company,
}: {
  readonly title: string | null;
  readonly company: string | null;
}) {
  if (title && company) return <span>{`${title} · ${company}`}</span>;
  return (
    <span className="sourcecado-card-cell-group">
      {title ? <span>{title}</span> : <Gap>No title</Gap>}
      {company ? <span>{company}</span> : <Gap>No company</Gap>}
    </span>
  );
}

export function ApolloPeopleResult({
  args,
  result,
  status,
  toolCallId,
  toolName,
  failure,
  cardId,
}: DomainRendererProps) {
  const listId = useId();
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [target, setTarget] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [curation, setCuration] = useState<ApolloCurationResult | null>(null);
  const [curationFailed, setCurationFailed] = useState(false);
  const [openingPersonId, setOpeningPersonId] = useState<string | null>(null);
  const [chatActionFailed, setChatActionFailed] = useState(false);
  const [failedCandidates, setFailedCandidates] = useState<FailedCandidate[]>([]);
  const populateComposer = usePopulateComposer();
  const { select, threadId } = useInspector();
  const enrich = toolName === "apollo_enrich_contact";
  const cardTitle = enrich ? "Apollo enriched contact" : "Apollo shortlist";
  const common = { id: cardId, toolCallIds: [toolCallId], provider: "apollo" as const };
  if (status === "error") {
    return (
      <ResultCard
        {...common}
        title={enrich ? "Apollo enrichment" : "Apollo shortlist"}
        state={failure?.class === "permission" ? "restricted" : "failed"}
        note={failure?.summary ?? "Apollo did not return a result."}
      />
    );
  }
  if (status === "loading") {
    return (
      <ResultCard
        {...common}
        title={enrich ? "Apollo enrichment" : "Apollo shortlist"}
        state="loading"
      >
        <SkeletonRows
          label={enrich ? "Loading Apollo enrichment" : "Loading Apollo candidates"}
        />
      </ResultCard>
    );
  }
  const raw = record(result);
  const unsupported = (title: string) => (
    <UnsupportedCard
      cardId={cardId}
      toolCallIds={[toolCallId]}
      provider="apollo"
      title={title}
      inspect={rawToolTarget({ toolCallId, toolName, args, result }, title)}
    />
  );
  if (enrich) {
    const rawName = text(raw?.name);
    const nameMasked = hasApolloNameMask(rawName);
    const name = rawName
      ? nameMasked
        ? withoutApolloNameMasks(rawName)
        : rawName
      : null;
    const title = text(raw?.title);
    const company = text(raw?.organizationName);
    const email = text(raw?.email);
    const phone = text(raw?.phone);
    const linkedinUrl = text(raw?.linkedinUrl);
    if (!raw || ![name, title, company, email, phone, linkedinUrl].some(Boolean)) {
      return unsupported("Apollo enrichment");
    }
    const contactName = name ?? "Enriched contact";
    // The hidden surname shows once, as a gap marker. The name text keeps the rest.
    const shownName = contactName.replace(/\s*\(surname hidden by Apollo\)/, "").trim();
    const missing = [
      !name ? "name" : null,
      nameMasked ? "verified full name" : null,
      !title ? "title" : null,
      !company ? "company" : null,
      !email ? "email" : null,
      !phone ? "phone" : null,
      !linkedinUrl ? "LinkedIn source" : null,
    ].filter((field): field is string => field !== null);
    const notReturned = <Gap>Not returned by Apollo</Gap>;
    return (
      <ResultCard
        {...common}
        title={cardTitle}
        state={missing.length > 0 ? "partial" : "ready"}
        note={
          missing.length > 0 ? `Apollo did not return: ${missingList(missing)}.` : undefined
        }
        footer={
          <>
            <InspectButton
              target={{
                kind: "source",
                id: `apollo-enriched:${toolCallId}`,
                title: contactName,
                status: "success",
                provider: "Apollo",
                externalUrl: linkedinUrl,
                result: { title, company, email, phone, missing },
              }}
            />
            <p className="sourcecado-card-fact">
              <Glyph name="info" />
              <span>Apollo credit used for this approved enrichment.</span>
            </p>
          </>
        }
      >
        <CardRows label="Enriched contact">
          <CardField icon="user" label="Name">
            {name ? (
              <button
                type="button"
                className="sourcecado-card-name"
                aria-label={`Inspect enriched contact ${contactName}`}
                onClick={(event) =>
                  select(
                    {
                      kind: "source",
                      id: `apollo-enriched:${toolCallId}`,
                      title: contactName,
                      status: "success",
                      provider: "Apollo",
                      externalUrl: linkedinUrl,
                      result: { title, company, email, phone, missing },
                    },
                    event.currentTarget,
                  )
                }
              >
                {shownName}
              </button>
            ) : (
              <Gap>No name</Gap>
            )}
            {nameMasked ? <Gap>Surname hidden by Apollo</Gap> : null}
          </CardField>
          <CardField icon="doc" label="Role">
            <RoleCell title={title} company={company} />
          </CardField>
          <CardField icon="mail" label="Email">
            {email ?? notReturned}
          </CardField>
          <CardField icon="phone" label="Phone">
            {phone ?? notReturned}
          </CardField>
          <CardField icon="link" label="LinkedIn">
            {linkedinUrl ? displayUrl(linkedinUrl) : notReturned}
          </CardField>
        </CardRows>
      </ResultCard>
    );
  }
  if (!Array.isArray(raw?.people)) return unsupported("Apollo people search");
  const people = raw.people
    .map(candidateOf)
    .filter((candidate): candidate is ApolloCandidate => candidate !== null)
    .filter(
      (candidate, index, candidates) =>
        candidates.findIndex((item) => item.id === candidate.id) === index,
    );
  const query = queryLabel(args);
  const allShown = visibleCount >= people.length;
  const selected = people.filter((candidate) => selectedIds.has(candidate.id));

  function toggleCandidate(candidateId: string) {
    setReviewing(false);
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(candidateId)) next.delete(candidateId);
      else next.add(candidateId);
      return next;
    });
  }

  async function keepSelected() {
    if (!threadId || selected.length === 0 || !target.trim() || submitting) return;
    setSubmitting(true);
    setCurationFailed(false);
    try {
      const next = await curateApolloCandidates({
        sessionId: threadId,
        target: target.trim(),
        people: selected.map((candidate) => candidate.raw),
        bindOriginal: selected.length === 1,
      });
      setCuration(next);
      setFailedCandidates(
        next.failed.flatMap((failure) => {
          const candidate =
            selected.find((item) => item.id === failure.apollo_id) ??
            selected[failure.row_index];
          return candidate ? [{ candidate, code: failure.code }] : [];
        }),
      );
      setReviewing(false);
    } catch {
      setCurationFailed(true);
    } finally {
      setSubmitting(false);
    }
  }

  async function retryFailed() {
    if (!threadId || failedCandidates.length === 0 || submitting) return;
    const retrying = failedCandidates.map((failure) => failure.candidate);
    setSubmitting(true);
    setCurationFailed(false);
    try {
      const next = await curateApolloCandidates({
        sessionId: threadId,
        target: target.trim(),
        people: retrying.map((candidate) => candidate.raw),
        bindOriginal: false,
      });
      setCuration((current) => {
        if (!current) return next;
        const kept = [...current.kept];
        for (const person of next.kept) {
          const index = kept.findIndex((item) => item.apollo_id === person.apollo_id);
          if (index === -1) kept.push(person);
          else kept[index] = person;
        }
        return {
          ...next,
          kept,
          original_session: current.original_session,
        };
      });
      setFailedCandidates(
        next.failed.flatMap((failure) => {
          const candidate =
            retrying.find((item) => item.id === failure.apollo_id) ??
            retrying[failure.row_index];
          return candidate ? [{ candidate, code: failure.code }] : [];
        }),
      );
    } catch {
      setCurationFailed(true);
    } finally {
      setSubmitting(false);
    }
  }

  function keptName(person: ApolloCurationResult["kept"][number]): string {
    const name = [person.first_name, person.last_name].filter(Boolean).join(" ") || "Person";
    return person.last_name_status === "hidden_by_apollo"
      ? `${name} (surname hidden by Apollo)`
      : name;
  }

  async function openKeptChat(person: ApolloCurationResult["kept"][number]) {
    if (openingPersonId) return;
    const existingSession = person.sourcing_chat?.session_id;
    if (existingSession) {
      window.location.hash = `#/chat/${encodeURIComponent(existingSession)}/person/${encodeURIComponent(person.person_id)}`;
      return;
    }
    setOpeningPersonId(person.person_id);
    setChatActionFailed(false);
    try {
      const opened = await openPersonSourcingChat(person.person_id, person.version);
      if (opened.active_person.person_id !== person.person_id) return;
      window.location.hash = `#/chat/${encodeURIComponent(opened.session.id)}/person/${encodeURIComponent(person.person_id)}`;
    } catch {
      setChatActionFailed(true);
    } finally {
      setOpeningPersonId(null);
    }
  }

  if (people.length === 0) {
    return (
      <ResultCard
        {...common}
        title="Apollo shortlist"
        count="0 candidates"
        state="empty"
        note={`No candidates matched ${query}.`}
        footer={
          <CardButton
            icon="adjust"
            onClick={() =>
              populateComposer?.(`Adjust the Apollo people search for ${query}.`)
            }
          >
            Adjust criteria
          </CardButton>
        }
      />
    );
  }

  const partial = raw.partial === true;
  return (
    <ResultCard
      {...common}
      title={cardTitle}
      count={`${people.length} ${people.length === 1 ? "candidate" : "candidates"}`}
      meta={query === "this search" ? undefined : [query]}
      state={partial ? "partial" : "ready"}
      note={
        partial
          ? "Apollo returned part of this search. Some candidates may be missing."
          : undefined
      }
      footer={
        <>
          <div className="sourcecado-card-actions">
            <InspectButton
              target={{
                kind: "source",
                id: `apollo-source:${toolCallId}`,
                title: "Apollo people search",
                status: "success",
                provider: "Apollo",
                preview: `${people.length} candidates for ${query}`,
                args,
                result: { candidateCount: people.length },
              }}
            />
            {people.length > PAGE_SIZE ? (
              <CardButton
                icon="chevron"
                aria-controls={listId}
                aria-expanded={visibleCount > PAGE_SIZE}
                data-open={allShown}
                onClick={() =>
                  setVisibleCount(
                    allShown ? PAGE_SIZE : Math.min(visibleCount + PAGE_SIZE, people.length),
                  )
                }
              >
                {allShown
                  ? "Show fewer candidates"
                  : `Show ${Math.min(PAGE_SIZE, people.length - visibleCount)} more candidates`}
              </CardButton>
            ) : null}
          </div>
          <section className="sourcecado-card-curate" aria-label="Curate Apollo shortlist">
            <label>
              <span>Target for selected people</span>
              <input
                value={target}
                onChange={(event) => {
                  setTarget(event.currentTarget.value);
                  setReviewing(false);
                }}
              />
            </label>
            <CardButton
              icon="review"
              disabled={selected.length === 0 || !target.trim()}
              onClick={() => setReviewing(true)}
            >
              Review {selected.length} selected{" "}
              {selected.length === 1 ? "candidate" : "candidates"}
            </CardButton>
            {reviewing ? (
              <section
                className="sourcecado-card-review"
                aria-label="Review selected Apollo candidates"
              >
                <h4>Review before keeping</h4>
                <p>{target.trim()}</p>
                <ul>
                  {selected.map((candidate) => <li key={candidate.id}>{candidate.name}</li>)}
                </ul>
                <p>Keeping creates person files. It does not enrich or use Apollo credits.</p>
                <CardButton
                  icon="keep"
                  tone="primary"
                  disabled={submitting}
                  onClick={() => void keepSelected()}
                >
                  {submitting
                    ? "Keeping…"
                    : `Keep ${selected.length} ${selected.length === 1 ? "person" : "people"}`}
                </CardButton>
              </section>
            ) : null}
            {curationFailed ? (
              <p role="alert" className="sourcecado-card-fact" data-tone="error">
                <Glyph name="failed" />
                <span>Couldn’t keep the selected people. Try again.</span>
              </p>
            ) : null}
            {curation ? (
              <section
                className="sourcecado-card-receipt"
                aria-label="Apollo curation receipt"
              >
                <p role="status">
                  Kept {curation.kept.length} {curation.kept.length === 1 ? "person" : "people"}.
                </p>
                {curation.original_session.reason === "multiple_selection" ? (
                  <p>The original target conversation remains unbound. Continue in each person’s sourcing chat.</p>
                ) : null}
                <ul>
                  {curation.kept.map((person) => {
                    const name = keptName(person);
                    return (
                      <li key={person.person_id}>
                        <a
                          href={`#/people/${encodeURIComponent(person.person_id)}`}
                          aria-label={`Open person file for ${name}`}
                        >
                          {name}
                        </a>
                        <CardButton
                          icon="chat"
                          disabled={openingPersonId !== null}
                          aria-label={`${person.sourcing_chat ? "Open" : "Create"} sourcing chat for ${name}`}
                          onClick={() => void openKeptChat(person)}
                        >
                          {openingPersonId === person.person_id
                            ? "Opening…"
                            : person.sourcing_chat
                              ? "Open chat"
                              : "Create chat"}
                        </CardButton>
                      </li>
                    );
                  })}
                </ul>
                {chatActionFailed ? (
                  <p role="alert" aria-label="Sourcing chat unavailable">
                    Couldn’t open this person’s sourcing chat. Refresh the person file and try again.
                  </p>
                ) : null}
                {failedCandidates.length > 0 ? (
                  <div className="sourcecado-card-failures">
                    {failedCandidates.map(({ candidate, code }) => (
                      <p key={candidate.id}>
                        {candidate.name} needs review ({code.replaceAll("_", " ")}).
                      </p>
                    ))}
                    <CardButton
                      icon="retry"
                      disabled={submitting}
                      onClick={() => void retryFailed()}
                    >
                      Retry {failedCandidates.length} failed{" "}
                      {failedCandidates.length === 1 ? "candidate" : "candidates"}
                    </CardButton>
                  </div>
                ) : null}
              </section>
            ) : null}
          </section>
          <div className="sourcecado-card-credit">
            <p className="sourcecado-card-fact" data-tone="warn">
              <Glyph name="info" />
              <span>Enrichment uses Apollo credits and requires approval.</span>
            </p>
            <CardButton
              icon="info"
              onClick={(event) =>
                select(
                  {
                    kind: "legacy",
                    id: `apollo-credit:${toolCallId}`,
                    title: "Apollo enrichment credits",
                    status: "success",
                    provider: "Apollo",
                    preview:
                      "Enriching a contact uses Apollo credits and requires approval before execution.",
                  },
                  event.currentTarget,
                )
              }
            >
              Review Apollo credit use
            </CardButton>
          </div>
        </>
      }
    >
      <CardRows label="Apollo candidates" id={listId}>
        {people.slice(0, visibleCount).map((candidate) => (
          <CardRow key={candidate.id}>
            <label className="sourcecado-card-check">
              <input
                type="checkbox"
                checked={selectedIds.has(candidate.id)}
                onChange={() => toggleCandidate(candidate.id)}
              />
              <span className="sr-only">Select {candidate.name}</span>
            </label>
            <span className="sourcecado-card-cell sourcecado-card-cell-name">
              <button
                type="button"
                className="sourcecado-card-name"
                aria-label={`Inspect candidate ${candidate.name}`}
                onClick={(event) =>
                  select(
                    {
                      kind: "source",
                      id: `apollo-candidate:${toolCallId}:${candidate.id}`,
                      title: candidate.name,
                      status: "success",
                      provider: "Apollo",
                      result: {
                        title: candidate.title,
                        company: candidate.company,
                        hasEmail: candidate.hasEmail,
                        phoneStatus: candidate.phoneStatus,
                        missing: candidate.missing,
                      },
                    },
                    event.currentTarget,
                  )
                }
              >
                {candidate.name}
              </button>
              {candidate.surnameHidden ? (
                <Gap>Surname hidden by Apollo</Gap>
              ) : candidate.firstName && !candidate.lastName ? (
                <Gap>No surname</Gap>
              ) : null}
            </span>
            <span className="sourcecado-card-cell sourcecado-card-cell-role">
              <RoleCell title={candidate.title} company={candidate.company} />
            </span>
            <span className="sourcecado-card-cell sourcecado-card-cell-email">
              <Glyph name="mail" />
              {candidate.hasEmail ? (
                <span>Email available after enrichment</span>
              ) : (
                <Gap>Email availability unknown</Gap>
              )}
              {candidate.phoneStatus ? (
                <span>{`Phone: ${candidate.phoneStatus}`}</span>
              ) : null}
            </span>
          </CardRow>
        ))}
      </CardRows>
    </ResultCard>
  );
}
