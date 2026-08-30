---
name: weekly-sourcing
description: Builds a compact shortlist from active Person Files, current sequence state, source-backed why-now evidence, and known knowledge gaps.
use-when: The director asks for a weekly sourcing check-in, who to work next, or a prioritized why-now review.
---

When Fisher asks for a weekly sourcing check-in, produce a prioritized review of
who to work next and the next concrete action.

1. Start from the director-authored Target. Never invent or silently broaden it.
2. Treat each Person as the unit of work. Read the Person File and active
   Sequence; a company is context on a Person, not a record to rank.
3. Use the current Living Brief and durable evidence. For every recommended
   Person, state a source-backed why-now and include the relevant Source
   Reference. A Person without reliable why-now evidence is not a recommendation.
4. Name important missing, stale, or conflicting context as a Knowledge Gap.
   Show conflicts instead of silently choosing a convenient claim.
5. Return a compact shortlist. For each Person show Target fit, Sequence state,
   why-now evidence, Source References, Knowledge Gaps, and one concrete next
   action. Update the Living Brief when durable evidence changes. Record an
   Outreach Outcome only when it actually happened; never infer one.

Keep the outreach actions distinct:

- Drafting: prepare an editable Outreach Draft from the Target and evidence
  already in the Person File. Drafting does not enrich or send.
- Enrichment: do it manually, one Person at a time. Explain the Knowledge Gap
  and credit use, then wait for the runtime's approval before
  `apollo_enrich_contact`. Never enrich a shortlist in bulk.
- Sending: use the current `gmail_send` path only for the reviewed message and
  recipient covered by an explicit, per-message Approved Send. Approval is not
  standing permission. Never bulk-send or auto-send.

Loading this skill grants no tool or permission. Use only the runtime's current
effective tools and approval decisions.
