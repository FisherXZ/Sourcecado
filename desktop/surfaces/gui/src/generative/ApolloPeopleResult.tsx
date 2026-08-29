import { useId, useState } from "react";

import {
  curateApolloCandidates,
  openPersonSourcingChat,
  type ApolloCurationResult,
} from "../api";
import { usePopulateComposer } from "../chat/ComposerDraftContext";
import { useInspector } from "../chat/Inspector";
import type { DomainRendererProps } from "../chat/toolRegistry";
import { hasApolloNameMask } from "../personName";

type ApolloCandidate = {
  readonly id: string;
  readonly name: string;
  readonly title: string | null;
  readonly company: string | null;
  readonly surnameHidden: boolean;
  readonly hasEmail: boolean;
  readonly phoneStatus: string | null;
  readonly missing: readonly string[];
  readonly raw: Record<string, unknown>;
};

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
    title,
    company,
    surnameHidden,
    hasEmail: raw.hasEmail === true,
    phoneStatus: text(raw.directPhoneStatus),
    missing,
    raw,
  };
}

function missingLabel(fields: readonly string[]): string | null {
  if (fields.length === 0) return null;
  if (fields.length === 1) return `Missing ${fields[0]}`;
  return `Missing ${fields.slice(0, -1).join(", ")} and ${fields.at(-1)}`;
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

export function ApolloPeopleResult({
  args,
  result,
  status,
  toolCallId,
  toolName,
}: DomainRendererProps) {
  const titleId = useId();
  const listId = useId();
  const [visibleCount, setVisibleCount] = useState(5);
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
  if (status === "error") return null;
  if (status === "loading") {
    return (
      <ol
        className="sourcecado-apollo-skeleton"
        aria-label="Loading Apollo candidates"
        aria-busy="true"
      >
        {[0, 1, 2].map((row) => (
          <li key={row}>
            <span />
            <span />
          </li>
        ))}
      </ol>
    );
  }
  const raw = record(result);
  if (toolName === "apollo_enrich_contact") {
    const name = text(raw?.name);
    const title = text(raw?.title);
    const company = text(raw?.organizationName);
    const email = text(raw?.email);
    const phone = text(raw?.phone);
    const linkedinUrl = text(raw?.linkedinUrl);
    if (!raw || ![name, title, company, email, phone, linkedinUrl].some(Boolean)) {
      return (
        <section className="sourcecado-apollo-result sourcecado-apollo-fallback" aria-labelledby={titleId}>
          <h3 id={titleId}>Apollo enrichment needs review</h3>
          <p>Sourcecado couldn’t summarize this enrichment safely. Use Inspect above to review it.</p>
        </section>
      );
    }
    const contactName = name ?? "Enriched contact";
    const missing = [
      !name ? "name" : null,
      !title ? "title" : null,
      !company ? "company" : null,
      !email ? "email" : null,
      !phone ? "phone" : null,
      !linkedinUrl ? "LinkedIn source" : null,
    ].filter((field): field is string => field !== null);
    return (
      <section className="sourcecado-apollo-result sourcecado-apollo-enriched" aria-labelledby={titleId}>
        <header>
          <div>
            <h3 id={titleId}>Apollo enriched contact</h3>
            <p>Apollo credit used for this approved enrichment.</p>
          </div>
        </header>
        <button
          type="button"
          className="sourcecado-apollo-candidate-name"
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
          {contactName}
        </button>
        <p>{[title, company].filter(Boolean).join(" · ") || "Role and company unavailable"}</p>
        {email ? <p>{email}</p> : null}
        {phone ? <p>{phone}</p> : null}
        {missingLabel(missing) ? <p>{missingLabel(missing)} from Apollo source.</p> : null}
      </section>
    );
  }
  if (toolName === "apollo_search_people" && !Array.isArray(raw?.people)) {
    return (
      <section className="sourcecado-apollo-result sourcecado-apollo-fallback" aria-labelledby={titleId}>
        <h3 id={titleId}>Apollo result needs review</h3>
        <p>Sourcecado couldn’t summarize this legacy result safely. Use Inspect above to review it.</p>
      </section>
    );
  }
  const people = Array.isArray(raw?.people)
    ? raw.people
        .map(candidateOf)
        .filter((candidate): candidate is ApolloCandidate => candidate !== null)
        .filter(
          (candidate, index, candidates) =>
            candidates.findIndex((item) => item.id === candidate.id) === index,
        )
    : [];
  const query = queryLabel(args);
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
      <section className="sourcecado-apollo-result sourcecado-apollo-empty" aria-labelledby={titleId}>
        <h3 id={titleId}>No Apollo matches</h3>
        <p>No candidates matched {query}.</p>
        <button
          type="button"
          onClick={() =>
            populateComposer?.(`Adjust the Apollo people search for ${query}.`)
          }
        >
          Adjust criteria
        </button>
      </section>
    );
  }

  return (
    <section className="sourcecado-apollo-result" aria-labelledby={titleId}>
      <header>
        <div>
          <h3 id={titleId}>Apollo shortlist</h3>
          <p>
            <span>{people.length} {people.length === 1 ? "candidate" : "candidates"}</span>
            {" · "}
            <span>{query}</span>
          </p>
        </div>
        <button
          type="button"
          aria-label="Inspect Apollo source"
          onClick={(event) =>
            select(
              {
                kind: "source",
                id: `apollo-source:${toolCallId}`,
                title: "Apollo people search",
                status: "success",
                provider: "Apollo",
                preview: `${people.length} candidates for ${query}`,
                args,
                result: { candidateCount: people.length },
              },
              event.currentTarget,
            )
          }
        >
          Source
        </button>
      </header>
      <ol id={listId} aria-label="Apollo candidates">
        {people.slice(0, visibleCount).map((candidate) => (
          <li key={candidate.id}>
            <label className="sourcecado-apollo-select">
              <input
                type="checkbox"
                checked={selectedIds.has(candidate.id)}
                onChange={() => toggleCandidate(candidate.id)}
              />
              <span>Select {candidate.name}</span>
            </label>
            <button
              type="button"
              className="sourcecado-apollo-candidate-name"
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
            <p>
              {[candidate.title, candidate.company].filter(Boolean).join(" · ") ||
                "Role and company unavailable"}
            </p>
            <p>
              <span>
                {candidate.hasEmail
                  ? "Email available after enrichment"
                  : "Email availability unknown"}
              </span>
              {candidate.phoneStatus ? ` · Phone: ${candidate.phoneStatus}` : ""}
            </p>
            {candidate.surnameHidden ? <p>Surname hidden by Apollo</p> : null}
            {missingLabel(candidate.missing) ? (
              <p>{missingLabel(candidate.missing)} from Apollo source.</p>
            ) : null}
          </li>
        ))}
      </ol>
      {visibleCount < people.length ? (
        <button
          type="button"
          aria-controls={listId}
          onClick={() => setVisibleCount((count) => Math.min(count + 5, people.length))}
        >
          Show {Math.min(5, people.length - visibleCount)} more candidates
        </button>
      ) : null}
      <section className="sourcecado-apollo-curation" aria-label="Curate Apollo shortlist">
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
        <button
          type="button"
          disabled={selected.length === 0 || !target.trim()}
          onClick={() => setReviewing(true)}
        >
          Review {selected.length} selected {selected.length === 1 ? "candidate" : "candidates"}
        </button>
        {reviewing ? (
          <section
            className="sourcecado-apollo-review"
            aria-label="Review selected Apollo candidates"
          >
            <h4>Review before keeping</h4>
            <p>{target.trim()}</p>
            <ul>
              {selected.map((candidate) => <li key={candidate.id}>{candidate.name}</li>)}
            </ul>
            <p>Keeping creates person files. It does not enrich or use Apollo credits.</p>
            <button type="button" disabled={submitting} onClick={() => void keepSelected()}>
              {submitting
                ? "Keeping…"
                : `Keep ${selected.length} ${selected.length === 1 ? "person" : "people"}`}
            </button>
          </section>
        ) : null}
        {curationFailed ? <p role="alert">Couldn’t keep the selected people. Try again.</p> : null}
        {curation ? (
          <section
            className="sourcecado-apollo-curation-receipt"
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
                    <button
                      type="button"
                      disabled={openingPersonId !== null}
                      aria-label={`${person.sourcing_chat ? "Open" : "Create"} sourcing chat for ${name}`}
                      onClick={() => void openKeptChat(person)}
                    >
                      {openingPersonId === person.person_id
                        ? "Opening…"
                        : person.sourcing_chat
                          ? "Open chat"
                          : "Create chat"}
                    </button>
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
              <div className="sourcecado-apollo-curation-failures">
                {failedCandidates.map(({ candidate, code }) => (
                  <p key={candidate.id}>
                    {candidate.name} needs review ({code.replaceAll("_", " ")}).
                  </p>
                ))}
                <button type="button" disabled={submitting} onClick={() => void retryFailed()}>
                  Retry {failedCandidates.length} failed {failedCandidates.length === 1 ? "candidate" : "candidates"}
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
      </section>
      <div className="sourcecado-apollo-credit-note">
        <p>Enrichment uses Apollo credits and requires approval.</p>
        <button
          type="button"
          aria-label="Review Apollo credit use"
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
          Review credit use
        </button>
      </div>
    </section>
  );
}
