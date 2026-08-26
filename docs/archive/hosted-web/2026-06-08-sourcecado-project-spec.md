# Sourcecado Project Spec

Date: 2026-06-08
Team: 2 people, ~5 hrs/week each (~10 combined hrs/week, ~10 weeks)

One-sentence product: a memory-grounded sourcing operator for Codeology that
answers "Who should I contact this week, why, what should I say, and what
happened after I acted?"

See the rescoped roadmap in
`docs/superpowers/plans/2026-06-08-sourcecado-autonomous-sourcing-os-weekly-timeline.md`.

---

## 1. Features and Functionality

### MVP features (the floor + target we commit to)

**The institutional memory brain (the floor)**
- Ingest the Google Drive of past sourcing directors' notes into queryable
  memory (currently nobody reads those docs).
- Ask a question and get a cited answer in four parts: Answer, Evidence (source
  citations), Gaps (what's missing/uncertain), Next Action.
- Humans can add notes and correct memory from the web app, with provenance.
- Anything the agent produces is written back into memory automatically.

**The weekly sourcing loop (the target)**
- A director sets up a **playbook**: target companies/profile, target roles
  (PMs, engineers, eng managers, university/recruiting, decision-makers),
  target count, freeform instructions, draft settings, and exclusions.
- "Run now" executes the playbook: research each company's last ~18 months ->
  use Apollo to find people in the target roles -> enrich (emails) -> draft a
  personalized outreach email per contact, grounded in cited research.
- A **run-result page** shows ranked contacts, why each was selected, the
  evidence, and the draft status.
- A **per-contact timeline page** shows the contact's profile, the research,
  the draft, and the follow-up sequence (initial -> +3 days -> +5 days, ~2
  follow-ups, stop on reply) with what's due today.
- Drafts are created in Gmail only (or stored as artifacts) - **never sent**
  automatically. Acceptance is feedback, not permission to send.
- Director feedback (accept/reject/edit/note/outcome) becomes memory and nudges
  the next run.

### Later / more challenging features (stretch and v2)

- **Recurring scheduling** (e.g. auto-run every Mon/Wed/Fri) via cron.
- **LinkedIn recent-activity enrichment** for sharper personalization.
- **Approve-to-send** and actual email sending (deliberately out of v1 for
  safety).
- **Learned ranking** once enough human feedback exists (v1 uses explicit
  weights).
- **Multi-tenant / per-workspace isolation** with row-level security (v1 is a
  single shared team workspace).
- Formal routine versioning, eval suites, a fuller audit UI, broader
  CRM-style account management.

### How we'd implement these (functions, libraries, services)

**Stack**
- Next.js (App Router), TypeScript end-to-end.
- Supabase: Postgres (data), Auth (team login), Storage (uploaded notes),
  pgvector (embeddings for semantic memory search).
- Anthropic SDK (`@anthropic-ai/sdk`) for the model + tool-use agent loop.

**The brain (port of existing code, not a rewrite)**
- Reuse the existing memory model: `source_records -> memory_chunks ->
  entities/aliases -> relationships -> semantic_facts`, all with citations.
- Functions we already have and port to Postgres: `ingestFolder`,
  `refreshMemory`, `buildSourcingMemoryAnswer`, chunking, embeddings.
- New: embeddings via pgvector instead of stored text; an `addMemory` /
  `correctMemory` write path with provenance.

**The agent runtime (small, owned)**
- A ~200-line tool-use loop on the Anthropic SDK: send the routine + memory
  context, expose scoped tools, run the model's tool calls, log each call.
- Tool classes: `read` (search memory, web), `enrich` (Apollo), `draft`
  (generate outreach), `external_write` (create Gmail draft only).
- Deliberately NOT adopting Hermes/OpenClaw as the harness - owning a small loop
  is more teachable and avoids weeks learning a framework.

**Connectors (bought/borrowed, behind thin adapters)**
- Apollo REST API (team key) for people search + enrichment.
- Web/company research and LinkedIn activity via available MCP sourcing tools
  if the runtime can reach them; otherwise web-search-only fallback.
- Gmail API for draft creation (drafts only, no send scope).

**Operational data (new tables in the same Postgres)**
- `routines`, `organizations`, `contacts`, `contact_identities`,
  `outreach_actions` (the follow-up sequence state machine), `drafts`, `runs`,
  `tool_calls`.
- Deterministic identity matching (exact email, Apollo person id) so the same
  person across sources resolves to one profile.

---

## 2. Teaching Goals

### Concepts we want every member to take away

- **What a memory layer actually is:** source records, chunking, embeddings,
  citations, and answering with explicit gaps instead of confident guesses.
- **Provenance and grounding:** every claim points back to a source; an LLM
  answer without citations is not trustworthy.
- **Agent runtime design:** the difference between a memory layer, an agent
  tool-use loop, and a product workflow - and why we own a small runtime instead
  of importing a heavy framework.
- **Connector/integration design:** API client -> normalized data -> stored
  source -> usage log -> product artifact; and how to design around paid API
  credits and rate limits.
- **Human-in-the-loop systems:** the difference between a UI event and a memory
  input, and how simple rules create useful learning before a learned model.
- **Safety boundaries:** "draft only, never auto-send" as a deliberate design
  decision; scoped tools; auditability.
- **Product restraint:** vertical slicing and a narrow surface beating CRM
  sprawl - one complete workflow over many half-built pages.
- **Full-stack reality:** auth, a real database, server-side execution, and a
  thin UI wired to actual backend truth.

### How members practice these skills

- **Build in cut tiers (floor -> target -> stretch):** each member ships a
  vertical slice that works end-to-end, so they practice finishing, not just
  starting.
- **Pair on weekly Definitions of Done:** the roadmap gives each week a concrete
  DoD; members demo against it.
- **Split by surface, swap mid-project:** one owns memory/agent/connectors, one
  owns the app/pages; they swap a slice midway so both touch the full stack.
- **Code review on every PR:** practice reading and critiquing, not just writing.
- **A short "teach-back" per phase:** whoever built a phase explains it to the
  other (and to newer members) - memory brain, then connectors, then the agent
  loop, then sequences/feedback.
- **Demo discipline:** the final demo proves the workflow on real data, which
  forces the habit of building something usable, not a fake demo.

---

## 3. Challenges

### Challenges we foresee

- **Capacity is the biggest risk.** ~10 combined hrs/week against a product that
  could easily balloon. Mitigation: the cut order (floor/target/stretch) so we
  always have something useful, and ruthless deferral of Gmail OAuth, cron
  scheduling, and LinkedIn scraping (the three biggest time traps).
- **Over-engineering** (a known tendency on this team). Mitigation: buy
  connectors, port the proven memory model instead of rebuilding, own a tiny
  agent loop, single shared workspace instead of multi-tenant.
- **External dependency uncertainty:** whether our runtime can actually reach
  Apollo and a LinkedIn-activity source. We'll spike this in week 2 before
  building around it; fallback is web-research-only personalization.
- **Apollo credits / rate limits** constraining search and enrichment volume.
  Mitigation: low-volume testing, usage logging from day one, fixtures for most
  development.
- **Gmail OAuth** taking longer than expected. Mitigation: the loop stores
  drafts as artifacts even if OAuth slips, so it never blocks progress.
- **Research/personalization quality:** weak web research producing generic
  outreach. Mitigation: strict citations and flagging weak findings as gaps
  rather than treating them as fact.
- **Data sensitivity:** past directors' notes and contact data must only be
  ingested if we're allowed to use them, and stored in a single controlled
  workspace.

### What we need from tech (the club's tech/leadership)

- **An Apollo account with API access and a credit budget** for the team.
- **A Google Workspace path for Gmail draft creation** (OAuth client / consent
  setup) and **read access to the sourcing-notes Drive** to ingest.
- **A Supabase project** (free tier likely fine for v1) and a place to host the
  Next.js app.
- **A model API budget** (Anthropic API key) for the agent and extraction.
- **A decision on the LinkedIn-activity source** (which MCP/data provider is
  acceptable, or confirm we stay web-research-only for v1).
- **Confirmation of data-use permissions** for the historical sourcing notes and
  contact data, and which workspace/owner they live under.
