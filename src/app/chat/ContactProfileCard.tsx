import { Card, Tag } from "@/components/ui";
import type { ContactCardView, MemoryFactEntry } from "./stream";

// A fact's subject usually just restates the contact's own name (search_memory
// facts are subject/predicate/object triples with no notion of "this card's
// person"), which reads redundantly on a card already headlined by that name.
// Drop it only on an exact match — a fact about a different entity (an org, a
// related person) keeps its subject since dropping it there would be wrong,
// not just repetitive.
function formatFact(fact: MemoryFactEntry, contactName: string): string {
  const sameSubject = fact.subject.trim().toLowerCase() === contactName.trim().toLowerCase();
  return sameSubject ? `${fact.predicate}: ${fact.object}` : `${fact.subject} ${fact.predicate} ${fact.object}`;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

// One card summarizing everything known about a person: identity, our
// relationship with them (past interactions), key facts, and gaps. A missing
// role/org/phone/email/LinkedIn or an empty history is shown as a plain gap,
// not hidden — same "call out what's missing" philosophy as the rest of the
// memory system. photoUrl is a plain field here; auto-populating it from a
// confirmed LinkedIn match is C3's job, not this component's.
export function ContactProfileCard({ contact, history, acceptedFacts, gapFacts }: ContactCardView) {
  return (
    <Card className="flex flex-col gap-3" data-testid="contact-profile-card">
      <div className="flex items-center gap-3">
        {contact.photoUrl ? (
          <img
            src={contact.photoUrl}
            alt={contact.canonicalName}
            className="h-11 w-11 flex-none rounded-full object-cover"
          />
        ) : (
          <div className="flex h-11 w-11 flex-none items-center justify-center rounded-full bg-accent-tint text-[13px] font-medium text-accent-deep">
            {initials(contact.canonicalName)}
          </div>
        )}
        <div>
          <div className="text-[15px] font-semibold text-text">{contact.canonicalName}</div>
          <div className="text-[12px] text-muted">
            {contact.role ?? "Role not yet known"}
            {" · "}
            {contact.organizationName ?? "Organization not yet known"}
          </div>
        </div>
      </div>

      <section>
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Contact</div>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
          <span className={contact.phone ? "text-text" : "text-muted"}>{contact.phone ?? "Phone not yet known"}</span>
          <span className={contact.email ? "text-text" : "text-muted"}>{contact.email ?? "Email not yet known"}</span>
          {contact.linkedinUrl ? (
            <a href={contact.linkedinUrl} className="text-accent-deep underline" aria-label="LinkedIn profile">
              LinkedIn
            </a>
          ) : (
            <span className="text-muted">LinkedIn not yet known</span>
          )}
        </div>
      </section>

      <section>
        <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Relationship</div>
        {history.length === 0 ? (
          <p className="mt-1 text-[12px] text-muted">No recorded interactions yet.</p>
        ) : (
          <ul className="mt-1 flex flex-col gap-1">
            {history.map((entry, i) => (
              <li key={i} className="flex items-baseline gap-2 text-[12px] text-text">
                <span className="font-mono text-[11px] text-muted">
                  {new Date(entry.occurredAt).toLocaleDateString()}
                </span>
                {entry.channel ? <Tag>{entry.channel}</Tag> : null}
                <span>{entry.summary}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {acceptedFacts.length > 0 ? (
        <section>
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted">Key facts</div>
          <ul className="mt-1 flex flex-col gap-1">
            {acceptedFacts.map((fact, i) => (
              <li key={i} className="text-[12px] text-text">
                {formatFact(fact, contact.canonicalName)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {gapFacts.length > 0 ? (
        <section>
          <div className="text-[11px] font-medium uppercase tracking-wide text-warn-tx">Knowledge gaps</div>
          <ul className="mt-1 flex flex-col gap-1">
            {gapFacts.map((fact, i) => (
              <li key={i} className="text-[12px] text-warn-tx">
                {formatFact(fact, contact.canonicalName)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Card>
  );
}
