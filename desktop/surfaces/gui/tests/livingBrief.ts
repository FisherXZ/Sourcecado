import type { BriefClaim, LivingBrief } from "../src/api";

/** One claim as the sidecar renders it, for tests that need a whole brief. */
export function claim(over: Partial<BriefClaim> & { id: string; text: string }): BriefClaim {
  return {
    state: "current",
    authority: "connector",
    updated_at: "2026-08-26T10:00:00+00:00",
    truncated: false,
    source_refs: [],
    ...over,
  };
}

/**
 * The living brief the person route returns. Tests override only the part
 * they are about, so a component reading a field nobody mocked fails here
 * rather than in whichever test happened to run first.
 */
export function livingBrief(over: Partial<LivingBrief> = {}): LivingBrief {
  const identity = claim({
    id: "identity:person one",
    text: "Alyssa Lee",
    source_refs: ["sourcecado:person one"],
  });
  return {
    version: "living-brief-v1",
    who: "Alyssa Lee",
    why: "Strong sourcing fit.",
    learned: ["Led university sourcing."],
    missing: ["email"],
    sources: ["apollo"],
    identity,
    target: claim({ id: "target:person one", text: "Strong sourcing fit." }),
    state: {
      sequence: "open",
      claim: claim({ id: "sequence:person one", text: "Sequence: open" }),
    },
    outcome: claim({
      id: "outcome:person one",
      text: "No outreach outcome is recorded yet.",
      state: "missing",
    }),
    last_contact: {
      at: null,
      direction: null,
      replied: false,
      follow_up: { needed: false, reason: null },
      claim: null,
    },
    wants: claim({
      id: "gap:wants:person one",
      text: "What this person wants is not recorded.",
      state: "missing",
    }),
    evidence: [
      claim({
        id: "evidence:event-1",
        text: "Led university sourcing.",
        source_refs: ["apollo:event-1"],
      }),
    ],
    conflicts: [],
    gaps: [
      claim({
        id: "gap:email:person one",
        text: "No email address is recorded for this person.",
        state: "missing",
      }),
    ],
    artifacts: [],
    claims: [identity],
    source_refs: [
      {
        id: "apollo:event-1",
        provider: "apollo",
        locator: "event-1",
        title: null,
        observed_at: "2026-08-26T10:00:00+00:00",
        modified_at: null,
        fresh: true,
        evidence: "present",
        truncated: false,
      },
    ],
    restricted_source_count: 0,
    partial: false,
    partial_sources: [],
    omitted: 0,
    handoff: {
      who: "Alyssa Lee",
      wanted: "Strong sourcing fit.",
      happened: "Led university sourcing.",
      they_want: "What this person wants is not recorded.",
      generated: true,
      source_refs: ["identity:person one"],
      version: 2,
      saved_at: null,
      stale: false,
      stale_fields: [],
      truncated_fields: [],
      freshness_unknown: false,
    },
    person_version: 2,
    ...over,
  };
}
