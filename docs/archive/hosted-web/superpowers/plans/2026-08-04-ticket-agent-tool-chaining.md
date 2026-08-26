# Ticket — Agent stops after one tool instead of chaining to a deliverable

Date: 2026-08-04
Found by: live agent run against real tools, 2026-08-04
Est: 0.25

## Problem

Asked to find recruiters at Anthropic and draft an intro email, the agent made
**two tool calls in 6.4 seconds** and produced a placeholder template. It had
`web_search`, `web_fetch`, and `apollo_enrich_contact` available and used none of
them.

## Evidence

```
[tool 1] search_memory("Anthropic recruiters talent leads")
      -> ok  {"chunks":[]}
[tool 2] apollo_search_people({organizationName:"Anthropic", personTitles:[...]})
      -> ok  {"people":[{"name":null,"title":"Talent Acquisition",...}]}

elapsed: 6.4s | tool calls: 2 | steps used: 2 of 8 | status: succeeded
```

Output was addressed to "Anthropic Talent Acquisition Team" and contained
`[Your Name]` and `[briefly mention Codeology's mission]`. Zero citations.

Note what this is **not**: it did not hit the step ceiling (2 of 8), nothing
errored, and the loop returned `succeeded`. The runtime behaved correctly. The
agent simply stopped.

## Why it happens

Two compounding causes.

1. **Starvation.** Apollo returned `name: null` for every person — see
   `2026-08-04-ticket-apollo-search-field-mapping.md`. With no names, there is
   nothing to enrich or research, so stopping is locally reasonable.

2. **No chain is stated.** `src/lib/context.ts` describes *outcomes* well
   ("end with the deliverable itself — not a description of one") and the
   Capabilities envelope says tools "appear in your tool list," but nothing tells
   the agent how the sourcing tools compose. It has to infer the sequence, and
   it doesn't.

The required sequence was established empirically on 2026-08-04: Apollo search
cannot return full names on this plan, so search results cannot feed enrich
directly. Web research is a mandatory bridge, not an optional enhancement.

**Apollo search** (who exists, what titles) → **web_search / web_fetch** (resolve
the full name) → **apollo_enrich_contact** (name → verified email) → draft.

## Fix

Add the chain to the sourcing doctrine in `src/lib/context.ts` as a named default,
in the same voice as the existing bullets. It belongs with the other
"Defaults for common situations" entries, not as a new section.

Also close two gaps the run exposed:

- **A draft with placeholders is not a deliverable.** `[Your Name]` and
  `[mention the mission]` are the model describing a draft rather than writing
  one. State that a draft ships addressed to a named person with no placeholder
  tokens, or it is not ready and the agent should say what it is missing instead.
- **A department is not a Contact.** Addressing "the Talent Acquisition Team"
  contradicts the doctrine that a Contact is a person or organization with a
  why-now. Name the person or report the Knowledge Gap.

## Out of scope

Model selection. Not a variable here.

## Done when

- The doctrine names the search → resolve → enrich chain and the
  no-placeholder-drafts rule.
- Re-running the probe question produces either a named person with a verified
  email, or an explicit Knowledge Gap saying which step failed — not a template.
- The re-run is captured as eval case #1 (see
  `2026-08-04-ticket-k1-eval-harness.md`).
