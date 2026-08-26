# Sourcecado Autonomous Sourcing OS Design

Date: 2026-06-08
Status: Draft for user review

## Summary

Sourcecado should evolve from the current SourcyAvo local memory CLI into a
hosted team sourcing operating system. The product is not only a club memory
layer. The memory layer is pillar one. Pillar two is an autonomous sourcing
agent that tells Sourcing Directors what to do next and produces
review-ready work.

The two-month product target is a hosted app that runs a weekly sourcing loop:

1. A Sourcing Director configures a routine with a simple form and an
   instruction box.
2. Sourcecado pulls or imports candidate contacts.
3. Sourcecado enriches and researches those contacts.
4. Sourcecado records sourcing signals for those contacts.
5. Sourcecado resolves contacts into one profile per person where possible.
6. Sourcecado ranks which contacts should be worked this week.
7. Sourcecado creates personalized Gmail drafts and sourcing artifacts.
8. The Sourcing Director reviews, edits, accepts, rejects, or annotates the
   work. In this phase, acceptance means the draft/recommendation is useful;
   it does not trigger sending.
9. Sourcecado writes the run outputs and human feedback back into memory.

This design keeps the strong club-brain goal, but the end product is broader:
a memory-grounded sourcing operator for Codeology.

## Product Thesis

Sourcecado should help a Sourcing Director answer and act on this question:

> Who should I contact this week, why, what should I say, and what happened
> after I acted?

The product should become the place where Codeology's sourcing work happens:
contact discovery, research, outreach preparation, reply tracking, outcome
capture, and institutional memory.

The agent should not be a generic assistant that happens to know sourcing
context. It should be a sourcing-specific operator with a durable memory model,
scoped tools, repeatable routines, and a reviewable run ledger.

## Two-Month Success Demo

The final demo should prove the whole sourcing loop on a small controlled
dataset and real integrations where practical:

- A director creates a weekly sourcing routine.
- Sourcecado uses Apollo to find or enrich contacts.
- Sourcecado reads relevant context from Gmail, Google Drive, Notion, and
  existing memory.
- Sourcecado uses web search for fresh public research.
- Sourcecado ranks a weekly contact list.
- Sourcecado drafts personalized outreach in Gmail.
- Sourcecado records each step, tool call, artifact, source reference, and
  decision rationale summary.
- The director reviews or edits the work.
- The director's feedback becomes source-backed memory.
- A later run uses the updated memory to improve recommendations.

## Locked Decisions

- Product form: hosted team app.
- Runtime ownership: Sourcecado owns its own domain runtime.
- Reference systems: heavily borrow patterns from OpenClaw and Hermes, but do
  not depend on either as the core harness.
- MCP: deferred. Keep interfaces MCP-shaped where useful, but do not require
  MCP for the two-month build.
- First connectors: Gmail, Apollo, web search, Apify, Google Drive, and Notion.
- Apollo: use real Apollo API because an Apollo account is available.
- Apify and LinkedIn: keep Apify in the connector boundary, but defer the
  LinkedIn/Apify v2 path.
- Gmail output: create Gmail drafts first. Actual sending and approve-to-send
  workflow are deferred.
- Routine configuration: simple form plus freeform instruction box.
- Sourcing signals: make them a first-class v1 primitive.
- Identity resolution: build it early enough that contacts do not fragment
  across Apollo, Gmail, Notion, Drive, web research, and human notes.
- Usage: record Apollo credits, web calls, Apify runs, Gmail quota-sensitive
  actions, and model usage in the run ledger.
- First UI: keep it to routine setup, run result, and run contact. Gmail draft
  review can be a popup, and memory can live under settings.
- Memory inputs: all connector outputs, agent actions, director feedback,
  source imports, draft artifacts, and outcomes.
- Human feedback loop: approvals, rejections, edits, notes, outcome labels,
  and priority overrides should become memory.
- Weekly routine output: review-ready Gmail draft artifacts.
- Run ledger depth: store steps, tool calls, artifacts, source references, and
  decision rationale summaries.
- Curriculum emphasis: balanced, but weighted toward agent/tool orchestration
  and memory architecture. Full-stack app polish is secondary.

## V1 Scope Adjustment

The first build should prove the weekly sourcing loop without turning into a
large revenue platform. V1 should be practical and visible:

- build sourcing signals as simple records with source references;
- build identity resolution with deterministic matching and human review for
  ambiguous cases;
- build usage tracking into the run ledger from the beginning;
- keep the UI to three primary pages: routine setup, run result, and run
  contact.

Defer formal routine/agent versioning, test contact suites, and eval prep to
v2. Keep the future need documented, but do not let it block the first useful
agent loop.

## Important Audit Boundary

The run ledger should store inspectable execution evidence, not hidden model
scratchpad text.

Store:

- run inputs and routine configuration;
- retrieved source records and citations;
- tool calls and tool results;
- scoring inputs and rubric outputs;
- decision rationale summaries;
- generated drafts and artifact versions;
- human edits, approvals, rejections, notes, and outcome labels;
- errors, retries, skipped actions, and blocked actions.

Do not make raw hidden chain-of-thought a product requirement. The useful audit
artifact is a structured explanation of what happened and why, grounded in
tools and sources.

## User Experience

The v1 UI should stay intentionally narrow. The first application should have
three primary pages:

- routine setup page;
- run result page;
- run contact page.

Gmail draft review can appear as a popup from the run contact page. Memory can
live under settings instead of becoming a primary navigation area.

### Routine Setup Page

The first routine setup surface should be intentionally narrow:

- routine name;
- target domain or theme;
- target count;
- cadence;
- contact filters;
- source scope;
- freeform instructions;
- Gmail draft settings;
- optional exclusions or do-not-contact notes.

This avoids a large workflow builder while still letting the director steer the
agent.

### Run Result Page

After a routine runs, the director should see:

- ranked contacts;
- why each contact was selected;
- visible sourcing signals;
- source citations and research notes;
- proposed outreach angle;
- Gmail draft status;
- usage summary;
- gaps or risks;
- actions available: accept, reject, edit, add note, override priority, mark
  outcome.

### Run Contact Page

Each ranked contact should have a focused detail page:

- canonical contact profile;
- identities and aliases found across sources;
- sourcing signals;
- supporting evidence;
- research notes and memory;
- outreach angle;
- draft popup entry point;
- feedback actions.

### Gmail Draft Output

Sourcecado creates Gmail drafts as the first real external artifact. Sending is
not part of the two-month scope. A director can accept or reject a draft inside
Sourcecado, but that acceptance is a feedback signal, not permission to send.

Each draft should preserve:

- contact id;
- routine run id;
- source references;
- generated subject/body;
- personalization claims and their citations;
- current review status;
- whether the draft was edited in Sourcecado.

If a director edits a draft inside Sourcecado, that edit becomes memory. If the
director edits directly inside Gmail, Sourcecado can record the draft id and
latest known content when fetched later, but perfect external edit diffing is a
stretch goal.

## Architecture

```text
Hosted Team App
  -> Auth and workspace context
  -> Routine configuration
  -> Agent runtime service
      -> Run ledger
      -> Usage ledger
      -> Scoped tool registry
      -> Connector adapters
      -> Memory read/write service
      -> Identity resolution service
      -> Signal extraction and scoring service
      -> Artifact service
  -> Review surfaces
      -> ranked contacts
      -> draft artifacts
      -> gaps and outcomes

Connectors
  -> Apollo
  -> Gmail
  -> Google Drive
  -> Notion
  -> Web search
  -> Apify base adapter

Memory
  -> source records
  -> chunks
  -> contacts
  -> contact identities
  -> organizations
  -> sourcing signals
  -> outreach attempts
  -> draft artifacts
  -> outcomes
  -> human feedback
  -> run-derived observations
  -> usage events
```

The important boundary is that Sourcecado owns sourcing truth. OpenClaw and
Hermes are useful references for runtime, cron, tool registry, skills, memory,
and gateway patterns, but Sourcecado should own the sourcing data model and
workflow semantics.

## Runtime Spine

The runtime center should be an `executeAgentRun()` boundary:

```text
trigger
  -> resolve workspace and actor
  -> load routine config
  -> create agent_run
  -> compile source scope and tool scope
  -> execute sourcing workflow
  -> record steps, tool calls, usage, artifacts, gaps, and rationales
  -> write selected outputs to memory
  -> present review surface
```

The first agent is a weekly sourcing operator. It should be domain-specific,
not a general multi-agent framework.

### Run Ledger Records

Candidate records:

```text
agent_runs
  id
  workspace_id
  routine_id
  actor_id
  trigger_type
  status
  started_at
  completed_at
  input_summary
  output_summary
  error

agent_steps
  id
  run_id
  step_index
  step_type
  status
  input_summary
  output_summary
  rationale_summary
  started_at
  completed_at
  error

tool_calls
  id
  run_id
  step_id
  tool_name
  tool_class
  connector
  input_json
  output_summary
  output_ref
  status
  error
  created_at

run_usage_events
  id
  run_id
  step_id
  tool_call_id
  usage_type
  connector
  quantity
  unit
  estimated_cost
  budget_limit
  created_at

run_artifacts
  id
  run_id
  artifact_type
  title
  content_json
  source_refs_json
  contact_refs_json
  review_status
  created_at

run_events
  id
  run_id
  event_type
  message
  metadata_json
  created_at
```

## Scoped Tool Registry

Tools should be grouped by capability class:

- `read`: search memory, read sources, web search, inspect contacts, inspect
  prior runs.
- `enrich`: Apollo search/enrichment, safe Apify lookups, company research.
- `draft`: create internal draft artifacts, generate Gmail draft content.
- `external_write`: create Gmail drafts.
- `admin`: change connector credentials, source scopes, routines, and
  ingestion settings.

The two-month build should allow `external_write` only for Gmail draft
creation. Actual message sending is out of scope.

The runtime should inject workspace, actor, source scope, and routine context
server-side. The model should not self-declare permissions.

## Connector Scope

### Apollo

Apollo should be the first real contact source and enrichment provider.

Initial capabilities:

- search people by target filters;
- enrich known people or organizations;
- store Apollo source ids and returned fields;
- track API failures, empty results, and credit-sensitive calls;
- record which Apollo data informed each contact recommendation.

### Gmail

Initial capabilities:

- create drafts;
- read relevant sent/reply context when authorized;
- associate email threads with contacts;
- classify reply/outcome candidates for review;
- write Gmail-derived source records into memory.

Sending is deferred.

### Google Drive

Initial capabilities:

- import or read allowed documents and folders;
- preserve Drive file ids and citations;
- refresh source records from selected materials;
- support sourcing-history context in memory answers and routines.

### Notion

Initial capabilities:

- import or read selected pages/databases;
- preserve page ids and citations;
- use Notion as semester/source-of-truth context where appropriate.

### Web Search

Initial capabilities:

- search for contact/company context;
- extract public facts with URLs;
- cite public sources in research notes;
- separate unverified web findings from accepted memory until reviewed or
  corroborated.

### Apify

Initial capabilities:

- define adapter shape and credential path;
- support non-LinkedIn safe actors if useful.

Deferred:

- LinkedIn/Apify v2 sourcing workflow.

## Sourcing Signals V1

A sourcing signal is a reason a contact may be worth action now. It should be
a first-class record, not a loose note embedded in a summary.

Examples:

- prior Codeology relationship;
- recent funding;
- recent job change;
- AI safety relevance;
- warm intro path;
- prior non-response;
- director note;
- Gmail reply;
- Notion task.

V1 should keep the model deliberately simple:

```text
sourcing_signals
  id
  workspace_id
  contact_id
  organization_id
  signal_type
  signal_label
  signal_summary
  source_record_id
  source_ref_json
  confidence
  observed_at
  expires_at
  created_by_actor_id
  created_by_run_id
```

Signals should be created from connector observations and human actions:

- Apollo creates funding, role, company, and profile-data signals where the
  source supports it.
- Gmail creates reply, prior outreach, non-response, and relationship signals.
- Notion and Drive create task, note, relationship, and institutional-memory
  signals.
- Web research creates public-news and relevance signals with URL citations.
- Human notes create director-note, priority, exclusion, and warm-intro
  signals.

Ranking can start with explicit weights rather than a learned model. For
example, a warm intro path and recent reply can raise priority, while prior
non-response or do-not-contact notes can lower priority or block drafting.

## Identity Resolution V1

Identity resolution means Sourcecado should keep one contact profile per
person even when that person appears in Apollo, Gmail, Notion, Drive, web
research, and human notes.

V1 should avoid a complex ML identity system. Use deterministic matching first:

- exact email match;
- Apollo person id match;
- Gmail sender/recipient email match;
- exact LinkedIn/profile URL match when available;
- normalized name plus organization/domain match;
- human-confirmed merge.

Recommended records:

```text
contacts
  id
  workspace_id
  canonical_name
  primary_email
  primary_organization_id
  current_title
  confidence_summary
  created_at
  updated_at

contact_identities
  id
  workspace_id
  contact_id
  provider
  external_id
  email
  profile_url
  display_name
  organization_name
  confidence
  source_record_id
  first_seen_at
  last_seen_at
```

The identity resolver should take new source records and either:

- attach the observation to an existing contact when the match is strong;
- create a new contact when no strong match exists;
- create a merge candidate when the match is plausible but not safe.

Ambiguous matches should go to human review instead of being auto-merged.
This keeps the memory layer clean without making identity resolution a large
project on day one.

## Memory Model Direction

The current local memory layer already has source records, chunks, entities,
relationships, semantic facts, citations, gaps, and refresh. The hosted product
should extend that into a sourcing-specific memory model:

- contacts;
- contact identities;
- organizations;
- sourcing signals;
- source records;
- source chunks;
- contact aliases;
- outreach attempts;
- outreach outcomes;
- research observations;
- draft artifacts;
- routine runs;
- human feedback;
- knowledge gaps;
- temporal fact status.

Every important record should keep provenance:

- connector/source type;
- external id when available;
- source record id;
- source chunk id or artifact id;
- run id when agent-created;
- actor id when human-created;
- observed time.

## Human Feedback As Memory

Human actions are not just UI events. They are memory inputs.

Record at least:

- contact accepted for outreach;
- contact rejected;
- priority overridden;
- draft edited;
- note added;
- outcome marked;
- missing context flagged;
- recommendation dismissed;
- routine instruction changed.

Feedback should influence future ranking, drafting, and gap analysis. The first
implementation can use explicit rules before learning a sophisticated scoring
model.

## Usage And Limits

Usage should be built into the run ledger from v1. The director should be able
to see what a run consumed and where it spent scarce resources.

Track at least:

- Apollo credit-sensitive calls;
- web search calls;
- Apify runs when Apify is used;
- Gmail draft creation and quota-sensitive Gmail calls;
- model input/output tokens and estimated model cost.

The first version can use simple per-run and per-workspace counters. Hard
budget enforcement can start with warnings and stop-rules for obvious limits,
then become more sophisticated later.

## Weekly Sourcing Workflow

The first autonomous workflow should be:

```text
load routine
  -> gather existing memory
  -> find/import contacts from Apollo
  -> resolve contact identities
  -> enrich contacts
  -> research contacts with web/Drive/Notion/Gmail context
  -> extract sourcing signals
  -> score and rank contacts
  -> generate outreach angles
  -> create Gmail drafts
  -> create review artifacts
  -> write run observations and gaps to memory
```

This gives one visible product loop while teaching the major system ideas:
connectors, source-grounded memory, tool calls, artifacts, feedback, and
runtime auditability.

## Safety And Permissions

The product should enforce permissions before retrieval or tool use, not by
prompt instruction.

Two-month baseline:

- hosted users and workspace membership;
- connector credentials scoped to workspace;
- source scope on routine runs;
- tool class filtering;
- no external sending;
- Gmail draft creation only;
- audit trail for all external writes;
- source citations for recommendations and personalization claims.

Later:

- approve-to-send;
- finer source permissions;
- more complete audit UI;
- MCP read surface;
- admin permission management;
- advanced temporal answers.

## Curriculum Shape

The internal project should teach through visible product milestones:

- memory architecture: sources, chunks, facts, citations, gaps;
- connector design: Apollo, Gmail, Drive, Notion, web search;
- agent orchestration: steps, tool registry, scoped execution;
- artifact design: drafts, research notes, ranked lists;
- feedback loops: human edits and outcomes become memory;
- safety: no sending without later approval design, tool scopes, audit logs.

Full-stack app development matters, but it should serve these lessons rather
than become the center of the project.

## Out Of Scope For The Two-Month Build

- Sending Gmail messages.
- Fully automated approve-to-send.
- Formal routine/agent versioning.
- Test contact suites and eval preparation.
- MCP runtime integration.
- Making OpenClaw or Hermes the core harness.
- LinkedIn/Apify v2 workflow.
- Full CRM dashboard.
- Full learned ontology workflow.
- Complex multi-agent orchestration.
- Production-grade permission administration.
- Raw hidden chain-of-thought storage.

## Verification

The final build should be verified with controlled data and real credentials
where available.

Core checks:

- routine setup creates a durable routine;
- weekly run creates a run ledger entry;
- Apollo search/enrichment produces contact candidates;
- identity resolution attaches repeated observations to one contact profile;
- sourcing signals are recorded with source references;
- web/Drive/Notion/Gmail context can be attached as cited source material;
- ranking output references evidence;
- Gmail drafts are created and linked back to run/contact/artifact records;
- usage summary records connector/API/model consumption;
- director feedback updates memory;
- a later run can use previous feedback;
- unauthorized tools are unavailable for a run;
- every external write is recorded as a tool call and artifact.

## Open Risks

- Apollo API access and credit limits may constrain search/enrichment volume.
- Gmail OAuth and draft creation may take longer than expected.
- Drive and Notion connector scope can sprawl unless source selection is kept
  narrow.
- Web research quality can produce weak personalization unless citations and
  gap language are strict.
- Apify/LinkedIn automation can become fragile or policy-sensitive, so it must
  stay off the critical path.
- Storing useful rationales without exposing hidden scratchpad text requires a
  deliberate rationale schema.

## Current Decisions To Carry Into Timeline

Carry these into the week-by-week timeline:

1. The two-month build is draft-only for Gmail, with no send action.
2. Apollo API is a required real integration.
3. Drive and Notion can start with selected source import/read paths rather
   than full workspace sync.
4. Apify exists as an adapter boundary, while LinkedIn/Apify v2 is deferred.
5. Sourcecado owns the runtime and uses OpenClaw/Hermes as design references.
6. Sourcing signals and identity resolution are v1 primitives.
7. Usage tracking is built into the run ledger from v1.
8. Formal routine/agent versioning, test contacts, and eval preparation are v2.
9. The v1 UI is routine setup, run result, and run contact. Gmail drafts can be
   a popup, and memory can live under settings.
10. The run ledger stores execution traces and rationale summaries, not raw
    hidden chain-of-thought.
