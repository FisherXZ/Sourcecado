import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  decideApproval,
  getPersonEnrichment,
  newOperationId,
  preparePersonEnrichment,
  type PersonDetailField,
  type PersonEnrichment,
} from "./api";
import { Icon } from "./icons";
import { Glyph } from "./generative/ResultCard";
import "./styles/enrichment.css";

const STATUS: Record<string, string> = {
  not_enriched: "Not looked up",
  awaiting_approval: "Ready for review",
  running: "Looking up…",
  found: "Contact details found",
  partial: "Some details found",
  no_match: "No match found",
  failed: "Lookup failed",
  insufficient_credit: "Not enough credits",
  outcome_unknown: "Outcome unknown",
  denied: "Lookup cancelled",
};
const FIELD: Record<string, string> = {
  name: "Name",
  email: "Email",
  phone: "Phone",
  linkedin_url: "LinkedIn",
  first_name: "First name",
  last_name: "Surname",
  title: "Role",
  company: "Company",
};
function shortDate(value: string): string | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? null
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function credits(value: number | null): string {
  return value === null
    ? "credits not reported"
    : `${value} credit${value === 1 ? "" : "s"}`;
}
/** One status line: what the last lookup found, what it cost, and when. */
function chip(context: PersonEnrichment): string {
  const latest = context.latest,
    label = STATUS[context.status] ?? "Lookup result";
  if (!latest || !["found", "partial"].includes(context.status))
    return latest &&
      typeof latest.credits_actual === "number" &&
      !["running", "awaiting_approval"].includes(context.status)
      ? `${label} · ${credits(latest.credits_actual)}`
      : label;
  const when = latest.fetched_at ? shortDate(latest.fetched_at) : null;
  return [
    context.status === "found" ? "Found via Apollo" : "Partly found via Apollo",
    credits(latest.credits_actual),
    when,
  ]
    .filter(Boolean)
    .join(" · ");
}
function storageKey(pid: string) {
  return `sourcecado:enrichment-review:${pid}`;
}
function savedOperation(pid: string): string | null {
  try {
    return localStorage.getItem(storageKey(pid));
  } catch {
    return null;
  }
}
function rememberOperation(pid: string, id: string | null) {
  try {
    if (id) localStorage.setItem(storageKey(pid), id);
    else localStorage.removeItem(storageKey(pid));
  } catch {
    /* Server receipts remain authoritative. */
  }
}

/** The same one-person review/result surface in preview and full Person File. */
export function EnrichmentPanel({
  personId,
  onEditDetail,
  onSessionOpened,
}: {
  personId: string;
  /** Opens the Details editor in place, on one field or on every field. */
  onEditDetail?: (field?: PersonDetailField) => void;
  /** Preparing the first review opens this person's sourcing chat. */
  onSessionOpened?: () => void;
}) {
  const [context, setContext] = useState<PersonEnrichment | null>(null);
  const [busy, setBusy] = useState<"prepare" | "allow" | "deny" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [newReview, setNewReview] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [fresh, setFresh] = useState(false);
  const owner = useRef(personId);
  owner.current = personId;
  const mounted = useRef(true);
  const operation = useRef<string | null>(savedOperation(personId));
  const sequence = useRef(0);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  const approving = context?.status === "awaiting_approval";

  async function refresh() {
    const pid = personId,
      request = ++sequence.current;
    try {
      const next = await getPersonEnrichment(pid);
      if (!mounted.current || owner.current !== pid || sequence.current !== request)
        return false;
      setContext(next);
      setProblem(null);
      setUnconfirmed(false);
      return true;
    } catch {
      if (mounted.current && owner.current === pid && sequence.current === request)
        setProblem("Couldn’t read lookup status. Saved details are kept.");
      return false;
    }
  }
  useEffect(() => {
    mounted.current = true;
    setContext(null);
    setProblem(null);
    setNewReview(false);
    setAcknowledged(false);
    setFresh(false);
    setBusy(null);
    setUnconfirmed(false);
    operation.current = savedOperation(personId);
    void refresh();
    return () => {
      mounted.current = false;
      ++sequence.current;
    };
  }, [personId]);
  useEffect(() => {
    const update = () => void refresh();
    window.addEventListener("sourcecado:board-changed", update);
    return () => window.removeEventListener("sourcecado:board-changed", update);
  }, [personId]);
  useEffect(() => {
    if (approving) reviewHeading.current?.focus();
  }, [approving]);
  useEffect(() => {
    if (context?.status !== "running") return;
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [context?.status, personId]);
  useEffect(() => {
    if (!fresh) return;
    const timer = window.setTimeout(() => setFresh(false), 4000);
    return () => window.clearTimeout(timer);
  }, [fresh]);

  async function prepare(unknownId?: string) {
    if (!context || busy) return;
    const pid = personId;
    const opensChat = !context.session_id;
    if (!operation.current) {
      operation.current = newOperationId();
      rememberOperation(pid, operation.current);
    }
    setBusy("prepare");
    setProblem(null);
    setFresh(false);
    try {
      const next = await preparePersonEnrichment(pid, context, operation.current, unknownId);
      if (!mounted.current || owner.current !== pid) return;
      ++sequence.current;
      setContext(next);
      setUnconfirmed(false);
      setNewReview(false);
      setAcknowledged(false);
    } catch {
      if (mounted.current && owner.current === pid)
        setProblem("Couldn’t load this review. Check status or retry preparing the same review.");
    } finally {
      if (mounted.current && owner.current === pid) {
        setBusy(null);
        if (opensChat) onSessionOpened?.();
      }
    }
  }
  async function decide(decision: "allow" | "deny") {
    const item = context?.pending;
    if (!item || busy || (decision === "allow" && (item.stale_identity || unconfirmed)))
      return;
    const pid = personId;
    setBusy(decision);
    setProblem(null);
    try {
      await decideApproval(item.id, decision);
      if (!mounted.current || owner.current !== pid) return;
      operation.current = null;
      rememberOperation(pid, null);
      if (!(await refresh())) setUnconfirmed(true);
      else if (decision === "allow") setFresh(true);
      window.dispatchEvent(new Event("sourcecado:board-changed"));
    } catch {
      if (mounted.current && owner.current === pid) {
        setUnconfirmed(true);
        setProblem("The outcome is not confirmed. Check status before another action.");
      }
    } finally {
      if (mounted.current && owner.current === pid) setBusy(null);
    }
  }

  const latest = context?.latest;
  const status = context?.status ?? "loading";
  const unknownId =
    context?.unresolved_unknown_id ??
    (context?.status === "outcome_unknown" ? latest?.id : undefined);
  const identityProblem = ["no_match_key", "ambiguous_match"].includes(context?.problem ?? "");
  const running = busy === "allow" || status === "running";
  const glyph =
    busy || status === "running" || status === "loading"
      ? "loading"
      : ["failed", "no_match", "insufficient_credit", "outcome_unknown"].includes(status)
        ? "failed"
        : status === "found" || status === "partial"
          ? "keep"
          : "info";
  const chipText = !context
    ? problem
      ? "Status unavailable"
      : "Loading lookup status…"
    : busy === "allow"
      ? "Looking up…"
      : busy === "deny"
        ? "Cancelling…"
        : busy === "prepare"
          ? "Preparing review…"
          : chip(context);
  const used =
    latest?.credits_actual === null || latest?.credits_actual === undefined
      ? "Credits not reported"
      : `${latest.credits_actual} credit${latest.credits_actual === 1 ? "" : "s"} used`;
  const reviewDetails = onEditDetail ? (
    <button type="button" className="enrichment-btn" onClick={() => onEditDetail()}>
      <Icon name="edit" />
      Review person details
    </button>
  ) : (
    <a className="enrichment-btn" href={`#/people/${encodeURIComponent(personId)}`}>
      <Icon name="edit" />
      Review person details
    </a>
  );
  const retry = (label: string) =>
    context?.can_request ? (
      <button type="button" className="enrichment-btn" disabled={Boolean(busy)} onClick={() => void prepare()}>
        <Icon name={label === "Try again" ? "refresh" : "search"} />
        {label}
      </button>
    ) : null;

  // A finished lookup that found nothing, failed, or ran out of credit says
  // why and what to do next in one card, where the Find button used to be.
  let outcome: ReactNode = null;
  if (context && !approving && !busy && !latest?.review_required) {
    const who = `${context.person.name}${context.person.company ? ` at ${context.person.company}` : ""}`;
    if (status === "no_match")
      outcome = (
        <div className="enrichment-card is-warn">
          <p className="enrichment-card-title">
            <Glyph name="failed" />
            Apollo found no match for {who}.
          </p>
          <p>{used}</p>
          <div className="enrichment-actions">
            {/* Apollo matches on email, name and company. A saved Apollo id
                overrides them, so editing them would not change that match. */}
            {onEditDetail && latest?.matched_on !== "apollo_id" ? (
              <>
                <button type="button" className="enrichment-btn" onClick={() => onEditDetail("email")}>
                  <Glyph name="mail" />
                  {context.known.email ? "Edit email" : "Add email"}
                </button>
                <button type="button" className="enrichment-btn" onClick={() => onEditDetail("company")}>
                  <Icon name="company" />
                  {context.person.company ? "Edit company" : "Add company"}
                </button>
              </>
            ) : null}
            {retry("Look up again")}
          </div>
        </div>
      );
    else if (status === "failed")
      outcome = (
        <div className="enrichment-card is-error">
          <p className="enrichment-card-title">
            <Glyph name="failed" />
            Apollo couldn’t complete this lookup.
          </p>
          <p>{used}</p>
          <div className="enrichment-actions">{retry("Try again")}</div>
        </div>
      );
    else if (status === "insufficient_credit")
      outcome = (
        <div className="enrichment-card is-error">
          <p className="enrichment-card-title">
            <Glyph name="failed" />
            Apollo has no credits left for this lookup.
          </p>
          <div className="enrichment-actions">
            <a className="enrichment-btn" href="#/connections/apollo">
              <Icon name="external" />
              Check Apollo credits
            </a>
            {retry("Try again")}
          </div>
        </div>
      );
    else if (status === "outcome_unknown")
      outcome = (
        <div className="enrichment-card is-warn">
          <p className="enrichment-card-title">
            <Glyph name="failed" />
            Apollo didn’t confirm this lookup. It may have used a credit.
          </p>
        </div>
      );
  }
  const filled = ["found", "partial"].includes(status) && !busy ? (latest?.fields_applied ?? []) : [];
  const showFind = context?.can_request && !approving && !outcome;

  return (
    <section
      className="person-enrichment"
      aria-label="Contact enrichment"
      aria-busy={running || busy === "prepare" || undefined}
    >
      <div className="enrichment-row">
        <span
          className={`enrichment-state enrichment-${busy === "allow" ? "running" : status}`}
          role="status"
          title={
            latest?.fields_applied.length
              ? `Updated: ${latest.fields_applied.map((field) => FIELD[field] ?? field).join(", ")}`
              : undefined
          }
        >
          <Glyph name={glyph} />
          {chipText}
        </span>
        {showFind ? (
          <button type="button" className="enrichment-btn" disabled={Boolean(busy)} onClick={() => void prepare()}>
            <Icon name="search" />
            {status === "not_enriched" || status === "denied" ? "Find contact details" : "Look up again"}
          </button>
        ) : null}
      </div>
      {filled.length ? (
        <ul className={`enrichment-filled${fresh ? " is-fresh" : ""}`} aria-label="Filled from Apollo">
          {filled.map((field) => (
            <li key={field}>
              <Icon name="check" />
              {FIELD[field] ?? field}
            </li>
          ))}
        </ul>
      ) : null}
      {context ? (
        <>
          {approving && context.pending ? (
            <div className="enrichment-review" aria-label="Review one contact lookup">
              <h4 ref={reviewHeading} tabIndex={-1}>
                {context.pending.person ?? context.person.name}
              </h4>
              <p>{[context.pending.title, context.pending.company].filter(Boolean).join(" · ")}</p>
              <p className="enrichment-match">
                <Glyph name="link" />
                {context.pending.matched_on === "apollo_id"
                  ? "Match: saved Apollo identity"
                  : context.pending.matched_on === "email"
                    ? `Match: ${context.pending.match?.email ?? context.known.email ?? "known email"}`
                    : "Match: full name and company"}
              </p>
              <div className="enrichment-cost">
                <span>Estimated credits · 1 person</span>
                <strong>{context.pending.credits_estimated ?? "Unknown"}</strong>
              </div>
              {context.pending.stale_identity ? (
                <p role="alert">These person details changed. Cancel this review and prepare a new one.</p>
              ) : null}
              <div className="enrichment-actions">
                <button
                  type="button"
                  className="enrichment-btn is-primary"
                  disabled={Boolean(busy) || unconfirmed || context.pending.stale_identity || Boolean(context.problem)}
                  onClick={() => void decide("allow")}
                >
                  <Icon name="approve" />
                  Approve lookup once
                </button>
                <button type="button" className="enrichment-btn" disabled={Boolean(busy)} onClick={() => void decide("deny")}>
                  <Icon name="close" />
                  Cancel lookup
                </button>
              </div>
            </div>
          ) : (
            <>
              {outcome}
              {latest?.review_required ? (
                <p role="alert" className="enrichment-alert">
                  Apollo couldn’t confirm this is the same person. No details were saved.
                </p>
              ) : null}
              {latest?.conflicts.length ? (
                <div className="enrichment-conflicts">
                  <p>Your corrections were kept. Apollo returned:</p>
                  <ul>
                    {latest.conflicts.map((value) => (
                      <li key={value.field}>
                        {FIELD[value.field] ?? value.field}: {value.current ?? "blank"} → {value.proposed ?? "blank"}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              {identityProblem ? <p role="status">Correct the person’s identity before a lookup.</p> : null}
              {unknownId && context.status !== "outcome_unknown" ? (
                <p role="status">An earlier lookup has an unknown outcome.</p>
              ) : null}
              {unknownId && !newReview ? (
                <button
                  type="button"
                  className="enrichment-btn"
                  disabled={Boolean(busy)}
                  onClick={() => {
                    setNewReview(true);
                    setAcknowledged(false);
                  }}
                >
                  <Icon name="approve" />
                  Review a new lookup
                </button>
              ) : null}
              {newReview ? (
                <div className="enrichment-review">
                  <p>The previous outcome remains unknown. A new lookup is a separate paid attempt.</p>
                  <label>
                    <input
                      type="checkbox"
                      checked={acknowledged}
                      onChange={(event) => setAcknowledged(event.target.checked)}
                    />
                    I understand this is a new paid attempt
                  </label>
                  <button
                    type="button"
                    className="enrichment-btn"
                    disabled={Boolean(busy) || !acknowledged}
                    onClick={() => {
                      operation.current = null;
                      rememberOperation(personId, null);
                      void prepare(unknownId);
                    }}
                  >
                    <Icon name="assistant" />
                    Review new attempt
                  </button>
                </div>
              ) : null}
            </>
          )}
          {context.problem === "apollo_not_configured" ? (
            <a className="enrichment-btn" href="#/connections/apollo">
              <Icon name="external" />
              Connect Apollo
            </a>
          ) : null}
          {identityProblem ||
          context.pending?.stale_identity ||
          latest?.review_required ||
          latest?.conflicts.length
            ? reviewDetails
            : null}
        </>
      ) : null}
      {problem ? (
        <p role="alert" className="enrichment-alert">
          {problem}
        </p>
      ) : null}
      {problem || context?.status === "running" || context?.status === "outcome_unknown" ? (
        <button type="button" className="enrichment-btn" disabled={Boolean(busy)} onClick={() => void refresh()}>
          <Icon name="refresh" />
          Check lookup status
        </button>
      ) : null}
      {context?.attempts && context.attempts.length > 1 ? (
        <details className="enrichment-history">
          <summary>
            <Glyph name="clock" />
            History
          </summary>
          <ol>
            {context.attempts.map((attempt) => {
              const when = attempt.fetched_at ?? attempt.created_at;
              return (
                <li key={attempt.id}>
                  {STATUS[attempt.status] ?? "Lookup result"} ·{" "}
                  {(when && shortDate(when)) ?? "Time not recorded"} ·{" "}
                  {attempt.credits_actual === null
                    ? "Cost not reported"
                    : credits(attempt.credits_actual)}
                </li>
              );
            })}
          </ol>
        </details>
      ) : null}
    </section>
  );
}
