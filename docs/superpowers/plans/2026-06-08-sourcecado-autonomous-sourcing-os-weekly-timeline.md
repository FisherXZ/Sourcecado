# Sourcecado Autonomous Sourcing OS Weekly Timeline

Date: 2026-06-08
Status: Draft for review

> **For agentic workers:** This is the two-month roadmap and curriculum
> timeline. When turning any week into implementation tasks, use
> `superpowers:writing-plans` for that week's detailed plan, then
> `superpowers:executing-plans` or `superpowers:subagent-driven-development`
> to execute it.

## Goal

Build Sourcecado from a local sourcing memory CLI into a narrow hosted sourcing
operator for Codeology: routine setup, ranked weekly contacts, contact review,
Gmail draft creation, sourcing signals, identity resolution, usage tracking,
run ledger, and memory feedback.

## Scope Guardrails

- V1 UI has three primary pages: routine setup, run result, and run contact.
- Gmail drafts open from a popup; Gmail sending is not in v1.
- Memory can live under settings; no standalone memory dashboard in v1.
- Sourcing signals and identity resolution are v1 primitives.
- Usage tracking is built into the run ledger from the beginning.
- Apollo is the first real enrichment/search integration.
- Drive, Notion, Gmail, and web search should start with selected, narrow read
  scopes.
- Apify stays as an adapter boundary; LinkedIn/Apify v2 is not in this timeline.
- Formal routine/agent versioning, test contact suites, and eval prep are v2.

## End State By 2026-08-16

A Sourcing Director can create a weekly routine, run it, review ranked contacts,
open each contact's evidence and signals, generate or inspect a Gmail draft,
give feedback, and see that the run recorded tool calls, artifacts, usage, and
source-backed memory updates.

---

**WEEK 1: 6.3 - 6.7**  
GOAL: Establish the memory-layer baseline and lock the product direction.

- [x] Read the existing local Sourcecado/SourcyAvo codebase and understand the
  current CLI memory layer.
- [x] Confirm the current product baseline: ingest source files, refresh memory,
  and answer sourcing questions with evidence, gaps, and next actions.
- [x] Shift the roadmap from "club memory only" to "memory-grounded sourcing
  operator."
- [x] Research OpenClaw and Hermes as agent-runtime references without adopting
  either as the core harness.
- [x] Review mature products in the same problem space and extract the relevant
  lessons: signals, identity, usage, and narrow UI.
- [x] Write and commit the autonomous sourcing OS design doc.
- [x] Add brief high-level direction to `AGENTS.md` and `CLAUDE.md`.

Curriculum focus:

- [x] Teach the difference between a memory layer, an agent runtime, and a
  product workflow.
- [x] Teach why Sourcecado owns its domain runtime instead of outsourcing the
  core product to a generic harness.

Definition of done:

- [x] The team can explain the product in one sentence: "Who should I contact
  this week, why, what should I say, and what happened after I acted?"

---

**WEEK 2: 6.8 - 6.14**  
GOAL: Lock the hosted app architecture and create the foundation for v1.

- [ ] Choose the hosted stack and write the short stack decision in the repo.
  Recommended default: TypeScript app, hosted web UI, server-side runtime
  service, durable relational database, and a background job path for runs.
- [ ] Scaffold the hosted app shell while preserving the existing CLI memory
  package as referenceable code.
- [ ] Define the v1 database model for workspaces, users, routines, contacts,
  contact identities, source records, sourcing signals, agent runs, agent
  steps, tool calls, usage events, run artifacts, and human feedback.
- [ ] Add environment and secret conventions for Apollo, Gmail, web search,
  Drive, Notion, Apify, model provider, and database credentials.
- [ ] Create a minimal settings area for connector status and memory/source
  scope visibility.
- [ ] Create a seed workspace and fixture dataset that can exercise the whole
  future flow without live APIs.
- [ ] Keep a v2 parking-lot note for routine/agent versioning, test contacts,
  and eval prep.

Curriculum focus:

- [ ] Teach how a local CLI memory system becomes a hosted team product.
- [ ] Teach the core data model: source records, contacts, identities, signals,
  runs, artifacts, feedback, and usage.
- [ ] Teach why narrow product surfaces beat CRM sprawl for v1.

Definition of done:

- [ ] A developer can start the app, see the narrow shell, inspect the database
  schema, and explain how a future weekly run will be recorded.

---

**WEEK 3: 6.15 - 6.21**  
GOAL: Build the hosted memory primitives, identity resolution, and sourcing signals.

- [ ] Port or wrap the existing source-record and memory-ingestion concepts so
  hosted code can store source-backed memory.
- [ ] Implement `contacts` and `contact_identities` with deterministic matching
  rules: exact email, Apollo person id, Gmail email, profile URL, normalized
  name plus organization/domain, and human-confirmed merge.
- [ ] Implement merge-candidate records for ambiguous matches so unsafe merges
  go to review instead of being auto-applied.
- [ ] Implement `sourcing_signals` as first-class records with type, label,
  summary, source reference, confidence, observed date, expiry date, creator,
  and run id.
- [ ] Add the first signal types: prior Codeology relationship, recent funding,
  recent job change, AI safety relevance, warm intro path, prior non-response,
  director note, Gmail reply, and Notion task.
- [ ] Add rule-based signal scoring so ranking can start without a learned
  model.
- [ ] Add tests around duplicate identities, ambiguous identities, signal
  creation, signal expiry, and score contribution.

Curriculum focus:

- [ ] Teach why identity resolution prevents memory fragmentation.
- [ ] Teach signals as "reasons to act now," not generic tags.
- [ ] Teach provenance: every useful claim should point back to a source.

Definition of done:

- [ ] A contact appearing in two sources resolves to one profile when the match
  is strong, creates a review candidate when uncertain, and displays source-
  backed sourcing signals.

---

**WEEK 4: 6.22 - 6.28**  
GOAL: Connect Apollo for real contact discovery and enrichment.

- [ ] Build the Apollo credential path and validate the available Apollo account
  against a low-volume test call.
- [ ] Implement Apollo people search for the routine's target filters and target
  count.
- [ ] Implement Apollo enrichment for known contacts and organizations.
- [ ] Store Apollo person ids, organization ids, returned fields, and source
  references as contact identities and source records.
- [ ] Convert useful Apollo fields into sourcing signals when appropriate, such
  as title, organization, company domain, funding-like metadata if available,
  and profile freshness.
- [ ] Record Apollo usage events for credit-sensitive calls, empty results,
  rate-limit responses, and failures.
- [ ] Add fixtures and tests for successful search, empty search, enrichment,
  rate limit, and malformed Apollo responses.

Curriculum focus:

- [ ] Teach connector boundaries: API client, normalized data, source record,
  usage event, and product-facing artifact.
- [ ] Teach how to design around paid API credits.

Definition of done:

- [ ] A routine can pull a small candidate list from Apollo, store the contacts
  with identities, record usage, and show which Apollo data informed the
  recommendation.

---

**WEEK 5: 6.29 - 7.5**  
GOAL: Add Gmail context and draft creation without sending.

- [ ] Implement Gmail OAuth or the selected Gmail credential flow for the
  hosted workspace.
- [ ] Read limited Gmail context for authorized sent/reply history relevant to
  contacts.
- [ ] Convert relevant Gmail context into source records, contact identities,
  outreach attempts, outcomes, and sourcing signals.
- [ ] Implement Gmail draft creation as the only external write action.
- [ ] Store draft artifacts with contact id, run id, subject, body,
  personalization claims, citations, Gmail draft id, and review status.
- [ ] Build the draft popup path from the run contact page.
- [ ] Record Gmail usage events for draft creation and quota-sensitive reads.
- [ ] Add tests for draft generation, no-send enforcement, Gmail failure, and
  linking a draft back to a run and contact.

Curriculum focus:

- [ ] Teach OAuth and scoped external writes.
- [ ] Teach why "draft only" is a safety boundary, not a missing feature.
- [ ] Teach artifact design: generated work should be reviewable, cited, and
  linked back to a run.

Definition of done:

- [ ] A selected contact can produce a review-ready Gmail draft, and the system
  records the draft as an artifact without sending any email.

---

**WEEK 6: 7.6 - 7.12**  
GOAL: Add narrow Drive, Notion, and web research context.

- [ ] Implement selected Google Drive document or folder import/read scope.
- [ ] Implement selected Notion page or database import/read scope.
- [ ] Implement web search for public contact and company research with URL
  citations.
- [ ] Store Drive, Notion, and web findings as source records with source refs
  and freshness metadata.
- [ ] Convert selected Drive and Notion context into relationship, task,
  director-note, and institutional-memory signals.
- [ ] Convert web research into public-news, relevance, and gap signals while
  keeping unverified findings separate from accepted memory.
- [ ] Add source selection controls in settings so the v1 app does not attempt
  full workspace sync.
- [ ] Add tests for citation preservation, stale source handling, and weak web
  research being flagged as a gap instead of treated as fact.

Curriculum focus:

- [ ] Teach selected-source ingestion instead of broad, risky sync.
- [ ] Teach citation discipline for personalization claims.
- [ ] Teach how to represent uncertainty without hiding it.

Definition of done:

- [ ] A run can use selected Drive, Notion, Gmail, Apollo, memory, and web
  context to explain why a contact is ranked and what evidence supports the
  draft angle.

---

**WEEK 7: 7.13 - 7.19**  
GOAL: Build the agent runtime spine, scoped tools, run ledger, and usage ledger.

- [ ] Implement the `executeAgentRun()` boundary: resolve workspace, load
  routine, compile source scope, compile tool scope, create run, execute steps,
  record outputs, and present review artifacts.
- [ ] Implement scoped tool classes: `read`, `enrich`, `draft`,
  `external_write`, and `admin`.
- [ ] Enforce that the model cannot self-declare permissions; workspace, actor,
  source scope, and tool scope are injected server-side.
- [ ] Implement run ledger records for agent runs, steps, tool calls, run
  events, artifacts, and rationale summaries.
- [ ] Implement usage ledger records for Apollo credits, web calls, Apify runs,
  Gmail quota-sensitive actions, and model tokens/cost estimates.
- [ ] Implement clear stop states for missing credentials, exhausted budget,
  empty Apollo results, insufficient evidence, and unauthorized tool use.
- [ ] Add tests proving unauthorized tools cannot be invoked and external writes
  are limited to Gmail draft creation.

Curriculum focus:

- [ ] Teach domain-specific agent runtime design.
- [ ] Teach tool scopes, auditability, and safe automation.
- [ ] Teach why the run ledger stores execution evidence and rationale
  summaries instead of hidden scratchpad text.

Definition of done:

- [ ] A weekly routine can execute through the runtime, leave a readable run
  ledger, record usage, and stop safely when a required scope or credential is
  missing.

---

**WEEK 8: 7.20 - 7.26**  
GOAL: Build the first end-to-end weekly routine and the three v1 pages.

- [ ] Build the routine setup page with routine name, target domain/theme,
  target count, cadence, contact filters, source scope, freeform instructions,
  Gmail draft settings, and exclusions.
- [ ] Build the run result page with ranked contacts, signal badges, evidence
  summaries, draft status, usage summary, gaps, and review actions.
- [ ] Build the run contact page with canonical profile, identities, signals,
  evidence, research notes, outreach angle, draft popup, and feedback actions.
- [ ] Wire routine setup to `executeAgentRun()` for fixture data first, then
  controlled live connectors where credentials are ready.
- [ ] Make the first ranking output explainable through signals and citations.
- [ ] Make the first draft popup usable from a contact page.
- [ ] Keep navigation intentionally narrow; settings is the only secondary area.
- [ ] Add UI tests for routine creation, run result display, contact detail,
  draft popup, and visible usage summary.

Curriculum focus:

- [ ] Teach vertical slicing: one complete workflow beats many unfinished
  surfaces.
- [ ] Teach product restraint: routine setup, run review, contact review.
- [ ] Teach how data model choices show up in the UI.

Definition of done:

- [ ] A Sourcing Director can create a routine, run it, open ranked contacts,
  inspect why they were selected, and open a draft popup.

---

**WEEK 9: 7.27 - 8.2**  
GOAL: Close the human feedback loop and make the second run smarter.

- [ ] Implement feedback actions: accept contact, reject contact, edit draft,
  add note, override priority, mark outcome, and flag missing context.
- [ ] Store every feedback action as memory with actor id, run id, contact id,
  source refs when applicable, and timestamp.
- [ ] Feed feedback into ranking rules so accepted contacts, rejected contacts,
  prior non-response, director notes, and outcomes affect the next run.
- [ ] Show feedback history on the run contact page.
- [ ] Add memory/settings visibility for recent feedback, source gaps, and
  connector source status.
- [ ] Add tests proving that feedback from run one changes scoring or draft
  guidance in run two.

Curriculum focus:

- [ ] Teach human-in-the-loop systems.
- [ ] Teach the difference between UI events and memory inputs.
- [ ] Teach how simple rules can create useful learning before a learned model.

Definition of done:

- [ ] A director's review choices become memory, and a later run uses those
  choices to change ranking, warnings, or draft guidance.

---

**WEEK 10: 8.3 - 8.9**  
GOAL: Harden the product loop and prepare the final demo.

- [ ] Run the full fixture-based weekly routine repeatedly and fix reliability
  issues in ranking, identity resolution, signal extraction, draft creation,
  and ledger recording.
- [ ] Run controlled live tests for Apollo and Gmail with low-volume limits.
- [ ] Run controlled selected-source tests for Drive, Notion, and web search.
- [ ] Add budget warnings and stop-rules for obvious overuse conditions.
- [ ] Improve error states for missing credentials, expired OAuth, empty
  connector results, weak evidence, and failed draft creation.
- [ ] Add a final smoke-test script or documented checklist for the whole v1
  flow.
- [ ] Clean up UI copy so it speaks to Sourcing Directors instead of engineers.
- [ ] Confirm v2 parking-lot items remain documented and out of the demo path.

Curriculum focus:

- [ ] Teach integration hardening.
- [ ] Teach product-quality error handling.
- [ ] Teach demo discipline: prove the workflow, not every possible feature.

Definition of done:

- [ ] The full v1 loop can run on fixtures and controlled live credentials with
  clear logs, usage, artifacts, and recovery paths.

---

**WEEK 11: 8.10 - 8.16**  
GOAL: Ship the internal demo, curriculum materials, and next roadmap.

- [ ] Prepare the final demo dataset and connector configuration.
- [ ] Run the final demo flow: setup routine, execute run, review ranked
  contacts, inspect signals/evidence, open Gmail draft popup, provide feedback,
  and show memory/ledger updates.
- [ ] Write the internal curriculum recap: memory, identity, signals,
  connectors, tool scopes, run ledger, usage, drafts, feedback loop, and safety.
- [ ] Write the operator handoff: how a Sourcing Director should use the app
  weekly.
- [ ] Write the engineering handoff: architecture map, run flow, connector
  setup, test commands, and known risks.
- [ ] Write the v2 roadmap note for routine/agent versioning, eval prep, test
  contact suites, approve-to-send, LinkedIn/Apify v2, richer permissions, and
  more complete audit UI.
- [ ] Decide whether the next two-month cycle should prioritize deeper
  automation, better review UX, or broader source coverage.

Curriculum focus:

- [ ] Teach the complete system story end to end.
- [ ] Teach what is production-ready, what is a teaching-grade prototype, and
  what is deliberately deferred.

Definition of done:

- [ ] Sourcecado has a credible internal v1: a weekly sourcing operator that
  produces ranked contacts and review-ready drafts with memory, evidence,
  usage, and feedback.

---

## V2 Parking Lot

- Formal routine/agent versioning with prompt snapshots, scoring-rubric
  snapshots, deployable routine versions, and rollback.
- Test contact suites and eval preparation.
- Approve-to-send and actual Gmail sending.
- LinkedIn/Apify v2 sourcing workflow.
- MCP runtime integration.
- More complete audit UI.
- Finer-grained permission administration.
- Broader CRM-style account/contact management.
- Learned scoring model after enough human feedback exists.

## Review Notes

- The timeline starts with data integrity before automation because identity
  resolution and signals are what keep the agent useful.
- Apollo and Gmail come before Drive/Notion/web depth because they create the
  first visible sourcing loop: find people and draft outreach.
- The runtime arrives after connector primitives so the ledger records real
  steps instead of becoming a hollow framework exercise.
- The UI arrives once there is enough backend truth to make the three pages
  meaningful.
