import { Glyph } from "../generative/ResultCard";
import { Icon } from "../icons";
import {
  revertPerson,
  type BriefClaim,
  type BriefSourceRef,
  type LivingBrief,
  type PersonFile,
} from "../api";

const CLAIM_STATE_LABEL: Record<string, string> = {
  current: "Current",
  stale: "Stale",
  conflicting: "Conflicting",
  missing: "Missing",
};

const EVIDENCE_LABEL: Record<string, string> = {
  present: "Read",
  partial: "Partly read",
  unsupported: "Body unavailable",
  missing: "Not reachable",
  ambiguous: "Unattributed",
  expired: "Aged out",
  absent: "Not present",
};

function shortDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

/** One brief claim: what it says, how far it holds, and what backs it. */
function Claim({ claim }: { claim: BriefClaim }) {
  return (
    <li className="person-claim">
      <p className="person-claim-line">
        <span className={`person-claim-state is-${claim.state}`}>
          {CLAIM_STATE_LABEL[claim.state] ?? claim.state}
        </span>
        <span>{claim.text}</span>
        {claim.truncated ? <span className="person-claim-flag">Truncated</span> : null}
      </p>
      {claim.source_refs.length > 0 ? (
        <p className="person-claim-refs">{claim.source_refs.join(" · ")}</p>
      ) : null}
    </li>
  );
}

function ClaimList({ claims, empty }: { claims: BriefClaim[]; empty: string }) {
  if (claims.length === 0) return <p className="person-empty">{empty}</p>;
  return (
    <ul className="person-claims">
      {claims.map((claim) => (
        <Claim key={claim.id} claim={claim} />
      ))}
    </ul>
  );
}

function sourceNote(source: BriefSourceRef): string {
  const parts = [EVIDENCE_LABEL[source.evidence] ?? source.evidence];
  parts.push(source.fresh ? "fresh" : "stale");
  if (source.truncated) parts.push("truncated");
  return parts.join(" · ");
}

function present(claim: BriefClaim | null | undefined): boolean {
  return Boolean(claim && claim.state !== "missing" && claim.text);
}

/**
 * Outcome, last contact, what they want and sources as cards when the brief
 * knows them. Everything the brief does not know is one compact Gaps list.
 */
export function BriefSummary({ brief }: { brief: LivingBrief }) {
  const contact = brief.last_contact;
  const hasContact = Boolean(contact.direction) || contact.follow_up.needed;
  const hasSources = brief.sources.length > 0 || brief.restricted_source_count > 0;
  const gaps = [
    present(brief.outcome) ? null : (brief.outcome?.text ?? "No outcome recorded."),
    hasContact ? null : "No outreach sent and no reply received.",
    present(brief.wants) ? null : (brief.wants.text || "What they want is not recorded."),
    hasSources ? null : "No sources recorded.",
    ...brief.gaps.map((claim) => claim.text),
  ].filter((text): text is string => Boolean(text));
  const unique = [...new Set(gaps)];
  return (
    <>
      <section className="person-section" aria-labelledby="person-gaps-heading">
        <h2 id="person-gaps-heading">Gaps</h2>
        {unique.length === 0 ? (
          <p className="person-empty">Nothing is open.</p>
        ) : (
          <ul className="pf-gaps">
            {unique.map((text) => (
              <li key={text}>
                <Glyph name="empty" />
                <span>{text}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      {present(brief.outcome) || hasContact || present(brief.wants) || hasSources ? (
        <div className="person-summary-grid">
          {present(brief.outcome) ? (
            <section className="person-summary-card" aria-labelledby="person-outcome-heading">
              <h2 id="person-outcome-heading">Outcome</h2>
              <p>{brief.outcome?.text}</p>
            </section>
          ) : null}
          {hasContact ? (
            <section className="person-summary-card" aria-labelledby="person-contact-heading">
              <h2 id="person-contact-heading">Last contact</h2>
              {contact.direction ? (
                <p>
                  {contact.direction} · {shortDate(contact.at) ?? "date not recorded"}
                </p>
              ) : null}
              {contact.follow_up.needed ? (
                <p className="person-attention">
                  {contact.follow_up.reason === "reply_unanswered"
                    ? "Reply to review."
                    : "Message attribution needs review."}
                </p>
              ) : null}
            </section>
          ) : null}
          {present(brief.wants) ? (
            <section className="person-summary-card" aria-labelledby="person-wants-heading">
              <h2 id="person-wants-heading">What they want</h2>
              <p>{brief.wants.text}</p>
            </section>
          ) : null}
          {hasSources ? (
            <section className="person-summary-card" aria-labelledby="person-sources-heading">
              <h2 id="person-sources-heading">Sources</h2>
              {brief.sources.length ? <p>{brief.sources.join(", ")}</p> : null}
              {brief.restricted_source_count > 0 ? (
                <p className="person-attention">
                  {brief.restricted_source_count} restricted source withheld from
                  this brief.
                </p>
              ) : null}
            </section>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

export function LearnedAndConflicts({ brief }: { brief: LivingBrief }) {
  return (
    <>
      <section className="person-section" aria-labelledby="person-learned-heading">
        <h2 id="person-learned-heading">Learned</h2>
        <ClaimList claims={brief.evidence} empty="Nothing filed yet." />
        {brief.omitted > 0 ? (
          <p className="person-empty">
            {brief.omitted} older record(s) are not in this brief.
          </p>
        ) : null}
      </section>
      {brief.conflicts.length > 0 ? (
        <section className="person-section" aria-labelledby="person-conflicts-heading">
          <h2 id="person-conflicts-heading">Conflicts</h2>
          <ClaimList claims={brief.conflicts} empty="None." />
        </section>
      ) : null}
    </>
  );
}

export function Artifacts({ brief }: { brief: LivingBrief }) {
  return (
    <section className="person-section" aria-labelledby="person-artifacts-heading">
      <h2 id="person-artifacts-heading">Artifacts</h2>
      <ClaimList claims={brief.artifacts} empty="Nothing filed yet." />
    </section>
  );
}

export function SourceReferences({ brief }: { brief: LivingBrief }) {
  return (
    <section className="person-section" aria-labelledby="person-source-refs-heading">
      <h2 id="person-source-refs-heading">Source references</h2>
      {brief.source_refs.length === 0 ? (
        <p className="person-empty">No sources recorded.</p>
      ) : (
        <ul className="person-claims">
          {brief.source_refs.map((source) => (
            <li key={source.id} className="person-claim">
              <p className="person-claim-line">
                <span
                  className={`person-claim-state is-${source.fresh ? "current" : "stale"}`}
                >
                  {source.provider}
                </span>
                <span>{source.title ?? source.locator ?? source.id}</span>
              </p>
              <p className="person-claim-refs">
                {source.id} · {sourceNote(source)} · seen {shortDate(source.observed_at)}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Timeline({ events }: { events: PersonFile["timeline"] }) {
  return (
    <section className="person-section" aria-labelledby="person-timeline-heading">
      <h2 id="person-timeline-heading">Timeline</h2>
      {events.length === 0 ? (
        <p className="person-empty">No sourcing activity yet.</p>
      ) : (
        <div className="person-timeline">
          {events.map((event) => (
            <article key={event.event_id} className="person-timeline-entry">
              <p className="person-timeline-meta">
                {event.source} · {event.kind}
              </p>
              <p>{event.summary}</p>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export function VersionHistory({
  personId,
  file,
  onReverted,
}: {
  personId: string;
  file: PersonFile;
  onReverted: () => void;
}) {
  if (!file.versions || file.versions.length === 0) return null;
  const current = file.person.version;
  return (
    <section className="person-section" aria-labelledby="person-history-heading">
      <h2 id="person-history-heading">Version history</h2>
      <p className="person-empty">
        {typeof current === "number" ? `Current version ${current}` : "Prior versions"}
      </p>
      <ol className="pf-versions">
        {file.versions.map((entry) => {
          const canRevert = typeof current === "number" && entry.version < current;
          return (
            <li key={entry.version}>
              <span>Version {entry.version}</span>
              {canRevert ? (
                <button
                  type="button"
                  className="pf-btn"
                  onClick={() =>
                    void revertPerson(personId, {
                      toVersion: entry.version,
                      expectedVersion: current,
                      rationaleSummary: `Restore version ${entry.version} from person history.`,
                    }).then(onReverted)
                  }
                >
                  <Icon name="refresh" />
                  Revert to version {entry.version}
                </button>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
