import { useId, useState } from "react";

import { usePopulateComposer } from "../chat/ComposerDraftContext";
import { useInspector } from "../chat/Inspector";
import type { DomainRendererProps } from "../chat/toolRegistry";

type ApolloCandidate = {
  readonly id: string;
  readonly name: string;
  readonly title: string | null;
  readonly company: string | null;
  readonly hasEmail: boolean;
  readonly phoneStatus: string | null;
  readonly missing: readonly string[];
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
  const lastName = text(raw.lastNameObfuscated);
  const title = text(raw.title);
  const company = text(raw.organizationName);
  const missing = [
    !title ? "title" : null,
    !firstName || !lastName ? "full name" : null,
    !company ? "company" : null,
  ].filter((field): field is string => field !== null);
  return {
    id: text(raw.apolloId) ?? `candidate-${index + 1}`,
    name: [firstName, lastName].filter(Boolean).join(" ") || "Unnamed candidate",
    title,
    company,
    hasEmail: raw.hasEmail === true,
    phoneStatus: text(raw.directPhoneStatus),
    missing,
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
  const populateComposer = usePopulateComposer();
  const { select } = useInspector();
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
    : [];
  const query = queryLabel(args);

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
            <span>{people.length} candidates</span>
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
