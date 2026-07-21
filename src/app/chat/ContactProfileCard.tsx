import { Card, Tag } from "@/components/ui";
import type { ContactCardView } from "./stream";

// One card summarizing everything known about a person: identity, our
// relationship with them (past interactions), key facts, and gaps. A missing
// role/org or an empty history is shown as a plain gap, not hidden — same
// "call out what's missing" philosophy as the rest of the memory system.
export function ContactProfileCard({ contact, history, acceptedFacts, gapFacts }: ContactCardView) {
  return (
    <Card className="flex flex-col gap-3" data-testid="contact-profile-card">
      <div>
        <div className="text-[15px] font-semibold text-text">{contact.canonicalName}</div>
        <div className="text-[12px] text-muted">
          {contact.role ?? "Role not yet known"}
          {" · "}
          {contact.organizationName ?? "Organization not yet known"}
        </div>
      </div>

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
                {fact.subject} {fact.predicate} {fact.object}
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
                {fact.subject} {fact.predicate} {fact.object}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </Card>
  );
}
