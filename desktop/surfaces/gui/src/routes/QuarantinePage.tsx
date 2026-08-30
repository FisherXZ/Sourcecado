import { useCallback, useEffect, useState } from "react";

import {
  getQuarantinedEffects,
  settleQuarantinedEffect,
  type QuarantineDecision,
  type QuarantinedEffect,
} from "../api";

// Three decisions, in the order an operator actually works: check whether it
// happened, say which, and only then give up on knowing. The wording avoids
// "succeeded" and "failed" because those are what the machine says about
// something it watched. These are what a person says about something nobody
// watched.
const DECISIONS: {
  value: QuarantineDecision;
  label: string;
  help: string;
}[] = [
  {
    value: "resolved_succeeded",
    label: "It happened",
    help: "You checked and the action went through. Do not run it again.",
  },
  {
    value: "resolved_failed",
    label: "It did not happen",
    help: "You checked and nothing went out. Safe to ask for it again.",
  },
  {
    value: "abandoned",
    label: "Stop tracking it",
    help: "You cannot tell, and you are choosing to leave it unresolved.",
  },
];

const TOOL_LABELS: Record<string, string> = {
  gmail_send: "Send email",
  gmail_draft: "Create draft",
  apollo_enrich_contact: "Enrich contact",
  calendar_create: "Create calendar event",
  calendar_update: "Update calendar event",
};

type PageState =
  | { status: "loading" }
  | { status: "loaded"; effects: QuarantinedEffect[] }
  | { status: "failed" };

function formatTimestamp(stamp: string | null): string {
  if (!stamp) return "Unknown time";
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function describeTool(name: string): string {
  return TOOL_LABELS[name] || name;
}

/** The binding the director approved: recipient, subject, account. Never a body. */
function approvalFacts(
  resource: Record<string, unknown> | null,
): { label: string; value: string }[] {
  if (!resource) return [];
  const fields: [string, string][] = [
    ["to", "To"],
    ["subject", "Subject"],
    ["account", "Account"],
    ["person_id", "Person"],
  ];
  return fields
    .filter(([key]) => typeof resource[key] === "string" && resource[key])
    .map(([key, label]) => ({ label, value: String(resource[key]) }));
}

function EffectCard({
  effect,
  onSettle,
}: {
  effect: QuarantinedEffect;
  onSettle: (
    effectId: string,
    decision: QuarantineDecision,
    note: string,
  ) => Promise<void>;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const facts = approvalFacts(effect.approval?.resource ?? null);

  async function decide(decision: QuarantineDecision) {
    setBusy(true);
    setError(null);
    try {
      await onSettle(effect.effectId, decision, note.trim());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save that.");
      setBusy(false);
    }
  }

  return (
    <li className="quarantine-card">
      <header>
        <h2>{describeTool(effect.toolName)}</h2>
        <time dateTime={effect.dispatchedAt || undefined}>
          {formatTimestamp(effect.dispatchedAt)}
        </time>
      </header>
      <p className="quarantine-lede">
        Sourcecado started this action and never found out whether it finished.
        It may have reached the other side, and it may not have. Nothing will
        retry it.
      </p>
      {facts.length > 0 && (
        <dl className="quarantine-facts">
          {facts.map((fact) => (
            <div key={fact.label}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {effect.reason && <p className="quarantine-reason">{effect.reason}</p>}
      {effect.supersedesInbox && effect.inboxClaim && (
        // The reconciliation rule, said out loud. The approval record is not
        // wrong about its own claim; it simply cannot know whether the call
        // was made, and reading it alone would invite a second attempt.
        <p className="quarantine-contested">
          The approval record calls this “{effect.inboxClaim}”. That describes
          the request, not the action. Only the answer below settles it.
        </p>
      )}
      <label className="quarantine-note">
        <span>What did you find? (optional)</span>
        <input
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Checked the Sent folder"
          disabled={busy}
        />
      </label>
      <div className="quarantine-actions">
        {DECISIONS.map((decision) => (
          <button
            key={decision.value}
            type="button"
            onClick={() => void decide(decision.value)}
            disabled={busy}
            title={decision.help}
          >
            {decision.label}
          </button>
        ))}
      </div>
      {error && (
        <p className="quarantine-error" role="alert">
          {error}
        </p>
      )}
    </li>
  );
}

export function QuarantinePage() {
  const [state, setState] = useState<PageState>({ status: "loading" });
  const [operator, setOperator] = useState("");

  const load = useCallback(() => {
    getQuarantinedEffects().then(
      (effects) => setState({ status: "loaded", effects }),
      () => setState({ status: "failed" }),
    );
  }, []);

  useEffect(() => {
    load();
    window.addEventListener("focus", load);
    return () => window.removeEventListener("focus", load);
  }, [load]);

  const settle = useCallback(
    async (effectId: string, decision: QuarantineDecision, note: string) => {
      // The store refuses an operator decision that names nobody, so the name
      // is asked for here rather than discovered as a server error.
      const who = operator.trim();
      if (!who) throw new Error("Enter your name before settling an action.");
      await settleQuarantinedEffect(effectId, decision, who, note || undefined);
      load();
    },
    [operator, load],
  );

  if (state.status === "loading") {
    return (
      <main className="route-page" aria-busy="true">
        <h1>Inbox</h1>
        <div className="route-skeleton" aria-label="Loading held actions" />
      </main>
    );
  }

  if (state.status === "failed") {
    return (
      <main className="route-page">
        <h1>Inbox</h1>
        <p>Could not load held actions.</p>
        <button type="button" onClick={load}>
          Try again
        </button>
      </main>
    );
  }

  return (
    <main className="route-page quarantine-page">
      <h1>Inbox</h1>
      <p className="route-lede">
        Actions that reach the outside world are recorded before Sourcecado
        attempts them. If it stops before recording what happened, the action
        lands here instead of being guessed at or run a second time.
      </p>
      {state.effects.length === 0 ? (
        <p className="quarantine-empty">
          Nothing is waiting. Every action Sourcecado started has a recorded
          outcome.
        </p>
      ) : (
        <>
          <label className="quarantine-operator">
            <span>Your name</span>
            <input
              type="text"
              value={operator}
              onChange={(event) => setOperator(event.target.value)}
              placeholder="Who is deciding"
            />
          </label>
          <ul className="quarantine-list">
            {state.effects.map((effect) => (
              <EffectCard
                key={effect.effectId}
                effect={effect}
                onSettle={settle}
              />
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
