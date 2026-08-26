# Sourcecado Full Agent Stack Roadmap Design

Date: 2026-06-15
Status: Draft for user review

## Summary

Sourcecado should be built as a production-shaped sourcing agent for
Codeology, not as a public SaaS product and not as a generic agent platform.
The product should teach the full agent stack through one narrow, real
workflow: helping Sourcing Directors understand current sourcing standing,
research organizations, surface target personas and contacts, produce
review-ready artifacts, track follow-up state, capture outcomes, and remember
what the club learns.

The full stack should be sketched and taught, but the two-month build should
use only two scope labels:

- **Build**: committed for the two-month roadmap.
- **Out of Scope**: explicitly not part of the committed build.

The product should feel like a real agent system: Research Chat and Routines
trigger the Agent Harness; the Agent Harness runs a ReAct-style tool-use loop;
the Model Gateway handles all model calls; memory and state provide sourcing
context; Apollo and Web Enrichment gather external context; generated
artifacts are validated for citations and duplicate contacts; the Run Ledger
records work, usage, status, errors, and feedback; human feedback and outcomes
flow back into memory.

## Product Frame

Sourcecado answers this operating question:

> What sourcing work should Codeology do next, why, what should we produce, and
> what happened after we acted?

The product is not a generic assistant and not a CRM. It borrows from GTM and
sales-agent patterns, but translates them into Sourcecado language:

- account -> Organization;
- buyer -> Target Persona or Contact;
- lead -> Sourcing Lead;
- sequence -> Follow-Up Sequence;
- CRM write -> Sourcecado memory/state update.

Research Chat is the primary real-time interaction surface. Routines are the
separate surface for saved, repeatable processes. Playbooks are reusable
sourcing instructions that can be used by chat or by a Routine.

## Build Scope

### UI Surfaces

Build:

- Research Chat.
- Routine page.
- Run results inside Research Chat.
- Contact / Organization detail page.
- Artifact panel.
- Memory management page.

Out of Scope:

- Separate run-result page.
- CRM dashboard.
- Admin console.
- Analytics dashboard.
- Calendar or inbox UI.
- Slack-like collaborative chat.

### Agent Harness

Build:

- ReAct-style tool-use loop as the core runtime.
- Run creation.
- Step logging.
- Tool-call logging.
- Model-call logging.
- Artifact creation.
- Error capture.
- Usage counters.
- Simple retry/error recovery.
- Tool permission classes.

Out of Scope:

- Resumability/checkpointing.
- Resume from exact failed step.
- Distributed durable execution.
- Multi-agent orchestration.
- Sandboxed code execution.
- Dynamic tool marketplace.
- External tracing integration.

### Model Gateway

Build:

- Single path for model calls.
- Named model tasks.
- Prompt/version naming.
- Structured output parsing.
- Usage logging.
- Error capture.
- Model-call records.
- Run Ledger linkage when a model call happens inside a run.

Out of Scope:

- Model provider marketplace.
- Automatic model routing.
- Fine-tuning.
- Custom transformer internals.
- Complex prompt evaluation platform.

### Memory And State

Build:

- Organizations.
- Contacts.
- Target Personas.
- Outreach History.
- Outreach Outcomes.
- Follow-Up Sequence state.
- Artifacts.
- Routines / Playbooks.
- Runs / Run Ledger.
- Human Feedback.

The current local memory model should be ported into Postgres/pgvector rather
than redesigned from scratch: source records, chunks, embeddings, cited
retrieval, answers, and memory corrections remain the core.

Out of Scope:

- Full CRM pipeline.
- Advanced ownership model.
- Learned preference profiles.
- Complex temporal knowledge graph.
- Production multi-tenant workspace model.

### Memory Ingestion

Build:

- File/export/import based ingestion only.
- Imported source records with citations.
- Chunking and embeddings in Postgres/pgvector.
- Memory add/correction from the app.

Out of Scope:

- Live Google Drive sync.
- Live Notion sync.
- Live Gmail sync.
- Messenger ingestion.

### Tool Layer

Build tool classes:

- `read`
- `enrich`
- `reason`
- `draft`
- `write_internal`
- `admin`

Build concrete tools:

- `search_memory`
- `get_contact`
- `get_organization`
- `list_outreach_history`
- `inspect_run`
- `search_apollo`
- `enrich_apollo_contact`
- `web_enrich_company`
- `web_enrich_contact`
- `create_draft_artifact`
- `revise_draft_artifact`
- `save_artifact`
- `add_memory_note`
- `record_outreach_outcome`
- `update_followup_sequence`
- `flag_knowledge_gap`
- `import_sources`
- `configure_routine`

Out of Scope:

- `send_email`
- Gmail draft creation.
- Calendar booking.
- CRM write.
- Slack notification.
- Filesystem/code execution.
- Arbitrary browser control.
- LinkedIn enrichment via Apify actor.

### Connectors And Enrichment

Build:

- Apollo search/enrichment.
- General Web Enrichment.
- Apify provider support for general Web Enrichment.
- Provider usage tracking.
- Source citations for enrichment claims.

Out of Scope:

- Gmail integration.
- ZoomInfo, Clay, or 6sense.
- LinkedIn-specific enrichment.
- Massive unattended scraping.
- Enrichment without provenance.

### Trigger Layer

Build:

- Research Chat request triggers agent work.
- Manual Routine run triggers agent work.
- Manual outcome/reply update can trigger next-action suggestion.
- New imported source or memory correction affects future runs.

Out of Scope:

- Scheduled Routine cron.
- Inbound email/reply webhook.
- CRM update trigger.
- Call-ended trigger.
- Slack trigger.

### Context Layer

Build:

- Sourcecado memory.
- Imported notes, files, and exports.
- Apollo data.
- Web Enrichment data.
- Manual reply/outcome notes.
- Routine and Playbook instructions.
- Prior Run Ledger summaries.
- Saved artifacts.

Out of Scope:

- Gmail inbox/sent history sync.
- Live Drive/Notion context.
- Call transcripts.
- CRM data.
- Slack history.
- Calendar data.

### Generation Layer

Build artifacts:

- Sourcing Memory Answer.
- Organization Research Brief.
- Target Persona Brief.
- Sourcing Lead List.
- Draft Artifact.
- Outreach Strategy.
- Current Sourcing Standing Summary.
- Run Summary.

Out of Scope:

- Sent emails.
- Calendar invites.
- Slack updates.
- Call notes from recordings.
- Polished public reports.
- Full CRM dashboards.

### Validation Layer

Build:

- Citation checker.
- Duplicate Contact check.

Out of Scope:

- Legal/compliance engine.
- Deliverability scoring.
- Approval-routing system.
- Enterprise policy engine.
- Broad moderation/safety suite.

### Evaluation Layer

Build:

- Service usage.
- Human feedback.
- Run status.

Out of Scope:

- Citation evaluation suite.
- Reply-rate optimization.
- Meeting-rate optimization.
- Hallucination benchmark suite.
- Learned scoring.
- Bad-send rate, because Sourcecado does not send email in this build.

### Observability

Build:

- Run Ledger as the product-owned observability spine.
- Run status.
- Step, tool, model, artifact, usage, error, and feedback records.
- Lightweight developer/debug inspection through Sourcecado records.

Out of Scope:

- External tracing integration as a dependency.
- LangSmith-style tracing as source of truth.
- Hidden model reasoning logs.
- Full production observability platform.

## Two-Month Roadmap

The roadmap should build a production-shaped agent, not isolated platform
layers. Each phase should leave the product more demoable than before.

### Phase 1: App And Data Foundation

Goal: create the hosted app and database foundation that every later slice uses.

Build:

- Next.js app shell.
- Postgres/pgvector project setup.
- Single team workspace assumption.
- Core schema for memory, artifacts, runs, routines, organizations, contacts,
  personas, outreach history, outcomes, follow-up state, and feedback.
- Basic seed/fixture data path.

Definition of Done:

- A developer can run the app locally.
- The database can store imported source records, artifacts, runs, contacts,
  and organizations.
- The schema supports the rest of the roadmap without needing a second
  redesign.

### Phase 2: Model Gateway And Agent Harness

Goal: establish the runtime path every sourcing behavior will use.

Build:

- Model Gateway module.
- ReAct-style tool-use loop.
- Tool registry with permission classes.
- Run creation.
- Step/tool/model logging.
- Artifact creation.
- Error capture and simple retry/error recovery.
- Usage counters.

Definition of Done:

- A simple local or development trigger can start an agent run.
- The run can call a model through the Model Gateway.
- The run can call at least one registered tool.
- The Run Ledger records the run, steps, tool calls, model calls, usage, and
  final status.

### Phase 3: Memory Port And Management

Goal: move the existing SourcyAvo memory brain into the hosted app.

Build:

- File/export/import ingestion.
- Source records and citations.
- Chunking.
- Embeddings in pgvector.
- Retrieval.
- Cited Sourcing Memory Answers.
- Memory add/correction path.
- Memory management page.

Definition of Done:

- Imported sourcing notes can be queried through Research Chat.
- Answers include cited evidence and gaps.
- A user can add or correct memory from the app.

### Phase 4: Research Chat As Agent Surface

Goal: make chat the primary real-time way to work with the agent.

Build:

- Research Chat UI.
- Chat-triggered agent runs.
- Run status/results shown inside chat.
- Artifact creation from chat.
- Artifact panel.
- Saved artifacts can become memory.

Definition of Done:

- A Sourcing Director can ask a sourcing question, receive a cited answer, save
  an artifact, and inspect the run that produced it.

### Phase 5: Sourcing State Model

Goal: give the agent enough sourcing state to act like a Sourcing Director.

Build:

- Organizations.
- Contacts.
- Target Personas.
- Outreach History.
- Outreach Outcomes.
- Follow-Up Sequence state.
- Human Feedback.
- Contact / Organization detail page.
- Manual Reply Capture.

Definition of Done:

- A user can inspect an Organization or Contact, see relevant history and
  artifacts, record an outcome, and update follow-up state.

### Phase 6: Enrichment Tools

Goal: let the agent gather external sourcing context with provenance and usage
tracking.

Build:

- Apollo search/enrichment tools.
- General Web Enrichment tools.
- Apify provider support for general Web Enrichment.
- Usage tracking for enrichment calls.
- Citation records for enrichment results.

Definition of Done:

- The agent can research an Organization, enrich potential Contacts, preserve
  source citations, and record service usage in the Run Ledger.

### Phase 7: Sourcing Artifact Generation

Goal: produce the review-ready outputs that make Sourcecado useful.

Build:

- Organization Research Brief.
- Target Persona Brief.
- Sourcing Lead List.
- Outreach Strategy.
- Draft Artifact.
- Current Sourcing Standing Summary.
- Run Summary.
- Citation checker.
- Duplicate Contact check.

Definition of Done:

- The agent can produce the committed artifact set from memory plus enrichment,
  with citations and duplicate checks.

### Phase 8: Routine Page And Manual Runs

Goal: support saved, repeatable sourcing processes without committing to
automatic scheduling.

Build:

- Routine page.
- Playbook fields.
- Manual Routine run.
- Routine results displayed in Research Chat and the Run Ledger.

Definition of Done:

- A user can save a repeatable sourcing Routine, run it manually, and inspect
  the results and artifacts through chat.

### Phase 9: Feedback Loop And Demo Hardening

Goal: close the learning loop and make the demo dependable.

Build:

- Accept/reject/edit/note feedback on artifacts.
- Manual reply/outcome capture.
- Feedback written to memory/state.
- Run status and usage visibility.
- Seeded demo scenario.
- End-to-end smoke path.

Definition of Done:

- The demo shows a full sourcing loop: imported memory, chat/routine run,
  enrichment, generated artifacts, review, outcome/feedback capture, and memory
  update.

## Linear-Ready Slice Groups

These are still groups, not final tickets. Each group should be broken into
single-engineer Linear issues during implementation planning.

1. App shell and Postgres/pgvector foundation.
2. Core database schema for memory, state, runs, artifacts, and routines.
3. File/export memory import pipeline.
4. pgvector retrieval and cited memory answers.
5. Memory management page.
6. Model Gateway module.
7. Agent Harness ReAct/tool-use loop.
8. Run Ledger tables and logging.
9. Tool registry and permission classes.
10. Research Chat agent surface.
11. Artifact system and artifact panel.
12. Organization / Contact / Target Persona state.
13. Outreach History / Outcome / Follow-Up Sequence state.
14. Contact / Organization detail page.
15. Apollo enrichment tool.
16. Web Enrichment tool.
17. Sourcing artifact generators.
18. Citation checker and duplicate Contact check.
19. Routine setup and manual run.
20. Human feedback and memory write-back.
21. Service usage and run status visibility.
22. Demo seed data, fixtures, and end-to-end hardening.

## Teaching Goals

The build should teach:

- Memory systems: source records, chunks, embeddings, retrieval, citations,
  gaps, and memory correction.
- Model Gateway design: one path for model tasks, structured outputs, prompt
  naming, usage, and error handling.
- Agent Harness design: ReAct/tool-use loop, tool registry, permissions,
  observations, artifacts, and run status.
- Connector design: Apollo and Web Enrichment as provenance-preserving tools
  with usage tracking.
- Grounding and safety: citation checks, duplicate checks, internal artifacts,
  and human review.
- Product restraint: a real sourcing workflow without building a public SaaS,
  CRM, or generic agent platform.

## Key Decisions Captured

- Build the full production-shaped agent stack, but scope it to Codeology
  sourcing.
- Keep only two roadmap labels: Build and Out of Scope.
- Research Chat is the real-time agent surface.
- Routines are saved repeatable processes; automatic scheduling is out of
  scope.
- Drafts are internal Draft Artifacts; Gmail integration and sending are out of
  scope.
- Memory ingestion is file/export/import based only.
- Apollo and general Web Enrichment are Build; LinkedIn-specific enrichment is
  out of scope.
- The Run Ledger is Sourcecado's observability spine.
- The Model Gateway is the single path for model calls.
