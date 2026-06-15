# Sourcecado Full Agent Stack — Task Breakdown

Date: 2026-06-15
Status: Draft for review (local md, not yet published to issue tracker)
Source spec: `docs/superpowers/specs/2026-06-15-sourcecado-full-agent-stack-roadmap-design.md`
Method: `/to-issues` vertical slices (tracer bullets), Option 3 build order.

## How to read this

- **Slice** = a demoable/verifiable tracer bullet that cuts end-to-end through every
  layer it touches. This is the unit you'd eventually publish as an issue.
- **Task** = a ~1–2hr chunk of human-with-Claude-Code work inside a slice. This is the
  unit you actually grab and do.
- **Type**: `AFK` (implement + merge with no human gate) or `HITL` (needs a human
  decision or design review mid-slice). Prefer AFK.
- **Build order = Option 3**: a minimal foundation cluster first (only what Feature A
  needs), then each feature as its own vertical stack. Schema grows per feature — no
  big-bang day-1 schema.

## Decisions already locked (do not relitigate)

- ADR-0001 Permissioned memory: source/file-level separation + vector metadata filter
  before retrieval. The memory port must carry this, not rely on prompt-level guarding.
- ADR-0002 Run Ledger is the observability spine. No external tracer as source of truth.
- ADR-0003 Routines are manual-first. No scheduler/cron in this build.
- ADR-0004 Single Model Gateway. All model calls go through it; no scattered provider
  calls.
- Domain vocabulary is fixed in `CONTEXT.md` (Contact, Sourcing Lead, Organization,
  Target Persona, Follow-Up Sequence, Manual Reply Capture, Web Enrichment, Outreach
  Outcome). Issue titles and code use these terms.

## Dependency map

```
FOUNDATION (F) ── enables everything
      │
      ▼
  A. Cited Memory Answer ──────────┐
      │                            │
      ├──► B. Sourcing State Model │
      │            │               │
      └──► C. Enrichment           │
                   │               │
                   ▼               │
            D. Artifact Generation ◄┘   (needs A + B + C)
                   │
                   ▼
            E. Routines & Manual Runs
                   │
                   ▼
            G. Feedback Loop & Demo Hardening   (needs A–E)
```

---

# FOUNDATION CLUSTER (F)

Minimal spine — only what Feature A needs to run one real, traced agent run. We do NOT
build the full schema here; later features add their own tables.

## F1 — App shell runs locally
Type: AFK · Blocked by: none

**What to build:** A Next.js app a developer can run locally with a single `/chat`
route placeholder and a health page. No agent logic yet.

**Acceptance criteria:**
- [ ] `npm run dev` serves the app locally
- [ ] `/chat` renders a placeholder; `/health` returns OK
- [ ] README updated with run instructions

**Tasks:**
- [ ] F1.1 Scaffold Next.js app shell + base layout/nav (~1h) · AFK
- [ ] F1.2 Add `/chat` placeholder route + `/health` route (~1h) · AFK

## F2 — Postgres + pgvector available locally
Type: AFK · Blocked by: none

**What to build:** A reproducible local Postgres+pgvector via docker-compose, plus a DB
access layer and a migration runner that later slices add migrations to.

**Acceptance criteria:**
- [ ] `docker compose up` starts Postgres with the `vector` extension enabled
- [ ] App connects via env-configured connection string
- [ ] Migration runner applies an empty baseline migration and is idempotent

**Tasks:**
- [ ] F2.1 docker-compose Postgres + pgvector + `.env.example` (~1h) · AFK
- [ ] F2.2 DB client/access layer + connection config (~1h) · AFK
- [ ] F2.3 Migration runner + baseline migration (~1.5h) · AFK

## F3 — Model Gateway with usage logging
Type: AFK · Blocked by: F2

**What to build:** The single `callModel()` entry point (ADR-0004). Records named model
task, prompt/version name, usage, and errors to a `model_calls` table. Returns a real
provider response.

**Acceptance criteria:**
- [ ] All model calls in the codebase go through the gateway (lint/grep check)
- [ ] A call writes a `model_calls` row with task name, prompt/version, tokens, status
- [ ] Provider error is captured, not thrown raw, and recorded

**Tasks:**
- [ ] F3.1 `model_calls` migration (task name, prompt/version, usage, status, error) (~1h) · AFK
- [ ] F3.2 `callModel()` with named tasks + prompt/version naming (~1.5h) · AFK
- [ ] F3.3 Structured-output parse helper + error capture + usage counters (~1.5h) · AFK

## F4 — Run Ledger spine
Type: AFK · Blocked by: F2

**What to build:** The Run Ledger tables and write path (ADR-0002): `runs`,
`run_steps`, `tool_calls`, plus linkage to `model_calls`. A run inspector view renders
the trace for one run.

**Acceptance criteria:**
- [ ] Starting a run creates a `runs` row; each step/tool/model call is recorded
- [ ] Final run status (success/error) is persisted
- [ ] Run inspector renders the full trace (steps, tool calls, model calls, usage) for a run id

**Tasks:**
- [ ] F4.1 `runs` + `run_steps` + `tool_calls` migrations (~1.5h) · AFK
- [ ] F4.2 Run create + step/tool/model logging write path (~1.5h) · AFK
- [ ] F4.3 Run status + error capture on the run (~1h) · AFK
- [ ] F4.4 Run inspector view (read-only trace render) (~1.5h) · AFK

## F5 — Agent Harness ReAct loop
Type: AFK · Blocked by: F3, F4

**What to build:** The ReAct-style tool-use loop, a tool registry with permission
classes (`read`/`enrich`/`reason`/`draft`/`write_internal`/`admin`), and one `echo`
tool. Every step writes to the Run Ledger via F4.

**Acceptance criteria:**
- [ ] A run executes a multi-step loop that calls the model via the gateway and at least one registered tool
- [ ] Tool registry enforces permission classes (a tool above the run's allowed class is refused and logged)
- [ ] The full run (steps, tool calls, model calls, status) appears in the run inspector

**Tasks:**
- [ ] F5.1 ReAct loop (observation → model → tool → repeat, with stop condition) (~2h) · AFK
- [ ] F5.2 Tool registry + permission classes + class enforcement (~1.5h) · AFK
- [ ] F5.3 `echo` reference tool + wire loop end-to-end to ledger (~1h) · AFK

**FOUNDATION DEMO:** type a question in `/chat`, the harness runs a multi-step loop,
calls the model through the gateway and the echo tool, writes the whole trace to the Run
Ledger, and you can inspect it. Nothing sourcing-specific yet — the spine works.

---

# A. CITED SOURCING MEMORY ANSWER

Port the existing SQLite memory brain (`src/`) into the hosted app and expose it through
Research Chat. **Highest-risk reuse in the project** — isolated here with a parity test.

## A1 — Memory schema + ingest ported to Postgres/pgvector
Type: AFK · Blocked by: F2

**What to build:** Port `source_records` and `memory_chunks` to Postgres with a pgvector
embedding column, plus the ingest + chunking path from `src/ingest.ts` / `src/chunk.ts`.
Carry ADR-0001 permission metadata at the source and chunk level.

**Acceptance criteria:**
- [ ] Source records and chunks persist in Postgres with permission/source metadata
- [ ] Ingesting a sample file produces chunks with citations
- [ ] Embedding column is pgvector, not a text blob

**Tasks:**
- [ ] A1.1 `source_records` + `memory_chunks` migration with pgvector + permission cols (~1.5h) · AFK
- [ ] A1.2 Port ingest (source record creation, content hashing, dedupe) (~2h) · AFK
- [ ] A1.3 Port chunking + citation construction (~1.5h) · AFK

## A2 — Embeddings + cited retrieval ported
Type: AFK · Blocked by: A1

**What to build:** Replace the in-process text-blob cosine (`src/embeddings.ts`) with
pgvector similarity search, and port the cited-retrieval path. Permission filter applies
before retrieval (ADR-0001).

**Acceptance criteria:**
- [ ] Retrieval uses pgvector similarity, filtered by the caller's allowed sources
- [ ] Retrieved chunks carry citations
- [ ] A restricted source never surfaces to a caller without access

**Tasks:**
- [ ] A2.1 Embedding generation through the Model Gateway → pgvector column (~2h) · AFK
- [ ] A2.2 pgvector similarity query with pre-retrieval permission filter (~2h) · AFK

## A3 — Answer logic ported with parity test
Type: HITL · Blocked by: A2

**What to build:** Port the intent classification + accepted/gap fact logic from
`src/answer.ts` (420 LOC). Gate the port behind a **parity test** that runs the same
question set against the old SQLite engine and the new Postgres engine and compares
answers. HITL because a human signs off on acceptable parity deltas.

**Acceptance criteria:**
- [ ] Cited Sourcing Memory Answer produced from Postgres with intent + gaps
- [ ] Parity test compares SQLite vs Postgres answers on a fixed question set
- [ ] Human-reviewed parity report: deltas are explained and accepted

**Tasks:**
- [ ] A3.1 Port intent classification + fact selection (~2h) · AFK
- [ ] A3.2 Port gap-fact handling + no-memory/no-relevant-memory paths (~1.5h) · AFK
- [ ] A3.3 Parity harness: question set + SQLite-vs-Postgres diff report (~2h) · AFK
- [ ] A3.4 Parity review gate + log accepted deltas (~1h) · HITL

## A4 — File/export memory import path
Type: AFK · Blocked by: A1

**What to build:** App-side file/export import (roadmap: ingestion is file/export only).
Imported sources become source records with citations.

**Acceptance criteria:**
- [ ] A user can import a file/export from the app and see it become source records
- [ ] Import surfaces per-file success/skip with reasons (no silent skips)

**Tasks:**
- [ ] A4.1 Import endpoint + source-record creation from upload (~2h) · AFK
- [ ] A4.2 Import result UI with per-file status/skip reasons (~1.5h) · AFK

## A5 — search_memory tool
Type: AFK · Blocked by: A2, F5

**What to build:** A `search_memory` tool (class `read`) that wraps cited retrieval and
plugs into the harness registry, replacing the echo tool for memory questions.

**Acceptance criteria:**
- [ ] `search_memory` returns cited chunks/facts to the loop
- [ ] Tool call + result recorded in the Run Ledger

**Tasks:**
- [ ] A5.1 `search_memory` tool wrapping retrieval + register it (~1.5h) · AFK

## A6 — Research Chat answers from memory
Type: HITL · Blocked by: A3, A5, F1

**What to build:** Minimal Research Chat UI that triggers an agent run, shows run
status/result inline, and renders the cited answer with gaps. HITL for one chat-layout
design review.

**Acceptance criteria:**
- [ ] A Sourcing Director asks a question and gets a cited answer in chat
- [ ] Run status shows while the run executes; result renders with citations + gaps
- [ ] A link opens the run inspector for that answer

**Tasks:**
- [ ] A6.1 Research Chat UI: message list + input + run trigger (~2h) · AFK
- [ ] A6.2 Inline run status + cited answer + gaps render (~1.5h) · AFK
- [ ] A6.3 Chat-layout design review pass (~1h) · HITL

## A7 — Memory management page (add/correct)
Type: AFK · Blocked by: A3

**What to build:** Memory management page to add a memory note and correct existing
memory (the memory-correction path from `src/`).

**Acceptance criteria:**
- [ ] A user can add a memory note from the app; it becomes retrievable
- [ ] A user can correct memory; corrections affect future answers

**Tasks:**
- [ ] A7.1 `add_memory_note` tool + write path (~1.5h) · AFK
- [ ] A7.2 Memory management page: list + add + correct (~2h) · AFK

**FEATURE A DEMO:** import sourcing notes, ask a question in chat, get a cited answer
with gaps, inspect the run, and correct memory.

---

# B. SOURCING STATE MODEL

Give the agent enough sourcing state to act like a Sourcing Director. Each entity is
added here (not in a day-1 schema).

## B1 — Organizations & Contacts
Type: AFK · Blocked by: F2

**What to build:** `organizations` and `contacts` schema + read tools (`get_contact`,
`get_organization`) wired into the harness.

**Acceptance criteria:**
- [ ] Organizations and Contacts persist and link (an Org can contain Contacts)
- [ ] `get_contact` / `get_organization` return records to the loop and log to the ledger

**Tasks:**
- [ ] B1.1 `organizations` + `contacts` migration (~1.5h) · AFK
- [ ] B1.2 `get_contact` + `get_organization` tools + register (~1.5h) · AFK

## B2 — Target Personas
Type: AFK · Blocked by: B1

**What to build:** `target_personas` (role/profile patterns per Org) + association to
Organizations.

**Acceptance criteria:**
- [ ] Target Personas persist and associate to an Organization
- [ ] Personas are retrievable for use by artifact generators later

**Tasks:**
- [ ] B2.1 `target_personas` migration + association (~1.5h) · AFK

## B3 — Outreach History, Outcomes, Follow-Up state
Type: AFK · Blocked by: B1

**What to build:** `outreach_history`, `outreach_outcomes`, `followup_sequence` state +
the `list_outreach_history` read tool. Sourcing Lead is a state of a Contact, not a new
table (per CONTEXT.md).

**Acceptance criteria:**
- [ ] Outreach history and outcomes persist against a Contact
- [ ] Follow-Up Sequence state can be read and updated
- [ ] `list_outreach_history` returns history to the loop

**Tasks:**
- [ ] B3.1 `outreach_history` + `outreach_outcomes` migrations (~1.5h) · AFK
- [ ] B3.2 `followup_sequence` state migration (~1h) · AFK
- [ ] B3.3 `list_outreach_history` + `record_outreach_outcome` + `update_followup_sequence` tools (~2h) · AFK

## B4 — Contact / Organization detail page
Type: HITL · Blocked by: B3

**What to build:** Detail page showing an Org/Contact with relevant history, outcomes,
and artifacts. HITL for one design review.

**Acceptance criteria:**
- [ ] A user inspects an Org or Contact and sees history, outcomes, follow-up state, artifacts
- [ ] Page links to the runs that produced those artifacts

**Tasks:**
- [ ] B4.1 Detail page data loaders + layout (~2h) · AFK
- [ ] B4.2 History/outcomes/artifacts panels (~2h) · AFK
- [ ] B4.3 Detail-page design review pass (~1h) · HITL

## B5 — Manual Reply Capture
Type: AFK · Blocked by: B3

**What to build:** Manual Reply Capture (record reply text/notes/outcome) that can
trigger a next-action suggestion run.

**Acceptance criteria:**
- [ ] A user records a reply/outcome; it updates state and can trigger a suggestion
- [ ] The suggestion run appears in the Run Ledger

**Tasks:**
- [ ] B5.1 Manual Reply Capture form + write to outcomes/state (~1.5h) · AFK
- [ ] B5.2 Reply → next-action suggestion run trigger (~1.5h) · AFK

---

# C. ENRICHMENT

External sourcing context with provenance and usage tracking. Apollo + general Web
Enrichment only; LinkedIn-specific enrichment is out of scope.

## C1 — Apollo search/enrich tools
Type: AFK · Blocked by: F5, B1

**What to build:** `search_apollo` + `enrich_apollo_contact` tools (class `enrich`) with
provider usage tracking and citation records on results.

**Acceptance criteria:**
- [ ] Agent searches Apollo and enriches a Contact; results carry source citations
- [ ] Apollo credit usage is recorded in the Run Ledger
- [ ] Enrichment without provenance is rejected

**Tasks:**
- [ ] C1.1 Apollo client + usage tracking plumbing (~2h) · AFK
- [ ] C1.2 `search_apollo` tool + citation records (~1.5h) · AFK
- [ ] C1.3 `enrich_apollo_contact` tool + write to Contact with provenance (~2h) · AFK

## C2 — Web Enrichment tools (with Apify provider)
Type: AFK · Blocked by: F5, B1

**What to build:** `web_enrich_company` + `web_enrich_contact` tools using a general web
provider (Apify allowed as a provider for general web enrichment), with usage tracking
and citations.

**Acceptance criteria:**
- [ ] Agent enriches a company/contact from the web with cited sources
- [ ] Web call usage recorded in the Run Ledger
- [ ] No LinkedIn-specific enrichment path exists

**Tasks:**
- [ ] C2.1 Web provider client (Apify) + usage tracking (~2h) · AFK
- [ ] C2.2 `web_enrich_company` tool + citations (~1.5h) · AFK
- [ ] C2.3 `web_enrich_contact` tool + citations (~1.5h) · AFK

**FEATURE C DEMO:** research an Organization, enrich potential Contacts, keep citations,
and see Apollo/web usage in the Run Ledger.

---

# D. SOURCING ARTIFACT GENERATION

The review-ready outputs. Needs memory (A) + state (B) + enrichment (C).

## D1 — Artifact system + panel
Type: HITL · Blocked by: A6, F4

**What to build:** Generic artifact persistence (`artifacts` table), the
`create_draft_artifact` / `revise_draft_artifact` / `save_artifact` tools, and an
Artifact panel in chat. HITL for the artifact panel design + the artifact shape
decision.

**Acceptance criteria:**
- [ ] Artifacts persist, link to the run that produced them, and render in a panel
- [ ] Draft → revise → save lifecycle works from chat
- [ ] A saved artifact can become memory

**Tasks:**
- [ ] D1.1 `artifacts` migration + create/revise/save tools (~2h) · AFK
- [ ] D1.2 Artifact panel UI + draft/revise/save flow (~2h) · AFK
- [ ] D1.3 Save-artifact-to-memory path (~1h) · AFK
- [ ] D1.4 Artifact shape + panel design review (~1h) · HITL

## D2 — Validation: citation checker + duplicate Contact check
Type: AFK · Blocked by: D1, B1

**What to build:** Citation checker (every claim in an artifact maps to a cited source)
and Duplicate Contact check. Validation failures are visible, never silent.

**Acceptance criteria:**
- [ ] Citation checker flags any uncited claim in a generated artifact
- [ ] Duplicate Contact check flags likely dupes before save
- [ ] Both surface results to the user; failures are recorded in the Run Ledger

**Tasks:**
- [ ] D2.1 Citation checker over artifact claims (~2h) · AFK
- [ ] D2.2 Duplicate Contact check (~1.5h) · AFK

## D3 — Research/Persona/Lead-List artifacts
Type: AFK · Blocked by: D1, D2, C1, C2, B2

**What to build:** Generators for Organization Research Brief, Target Persona Brief, and
Sourcing Lead List, drawing on memory + enrichment + state, with citations + dup checks.

**Acceptance criteria:**
- [ ] Each artifact generates from memory + enrichment with citations
- [ ] Lead List runs the Duplicate Contact check
- [ ] Each renders in the artifact panel and is savable

**Tasks:**
- [ ] D3.1 Organization Research Brief generator (~2h) · AFK
- [ ] D3.2 Target Persona Brief generator (~1.5h) · AFK
- [ ] D3.3 Sourcing Lead List generator + dup check wiring (~2h) · AFK

## D4 — Strategy / Standing / Run Summary artifacts
Type: AFK · Blocked by: D1, B3

**What to build:** Generators for Outreach Strategy, Current Sourcing Standing Summary,
and Run Summary.

**Acceptance criteria:**
- [ ] Outreach Strategy generates from a Contact/Org's state + history
- [ ] Current Sourcing Standing Summary answers "where do we stand" from state
- [ ] Run Summary summarizes a run from the Run Ledger

**Tasks:**
- [ ] D4.1 Outreach Strategy generator (~2h) · AFK
- [ ] D4.2 Current Sourcing Standing Summary generator (~2h) · AFK
- [ ] D4.3 Run Summary generator from ledger (~1.5h) · AFK

**FEATURE D DEMO:** produce the committed artifact set from memory + enrichment, with
citations and duplicate checks, all savable and traceable.

---

# E. ROUTINES & MANUAL RUNS

Saved repeatable processes, run manually (ADR-0003 — no scheduler).

## E1 — Routine + Playbook model
Type: AFK · Blocked by: F5

**What to build:** Routine/Playbook persistence + `configure_routine` tool. Playbooks
are reusable sourcing instructions usable by chat or a Routine.

**Acceptance criteria:**
- [ ] A Routine with Playbook fields persists
- [ ] `configure_routine` creates/edits a Routine

**Tasks:**
- [ ] E1.1 `routines` + `playbooks` migration (~1.5h) · AFK
- [ ] E1.2 `configure_routine` tool (~1.5h) · AFK

## E2 — Routine page + manual run
Type: HITL · Blocked by: E1, D3

**What to build:** Routine setup page and a manual "Run now" that executes through the
harness; results show in Research Chat + the Run Ledger. HITL for routine-page design.

**Acceptance criteria:**
- [ ] A user saves a Routine, runs it manually, and sees results/artifacts in chat
- [ ] The routine run is fully recorded in the Run Ledger

**Tasks:**
- [ ] E2.1 Routine setup page (config form) (~2h) · AFK
- [ ] E2.2 Manual "Run now" → harness run + results in chat (~2h) · AFK
- [ ] E2.3 Routine-page design review (~1h) · HITL

---

# G. FEEDBACK LOOP & DEMO HARDENING

Close the learning loop and make the demo dependable. Needs A–E.

## G1 — Human feedback → memory/state write-back
Type: AFK · Blocked by: D1, A7

**What to build:** Accept/reject/edit/note feedback on artifacts; feedback writes back
to memory/state. Includes `flag_knowledge_gap`.

**Acceptance criteria:**
- [ ] A user can accept/reject/edit/note an artifact; the feedback persists
- [ ] Feedback is written to memory/state and affects future runs
- [ ] `flag_knowledge_gap` records a gap that surfaces in later answers

**Tasks:**
- [ ] G1.1 `human_feedback` migration + accept/reject/edit/note capture (~2h) · AFK
- [ ] G1.2 Feedback → memory/state write-back (~2h) · AFK
- [ ] G1.3 `flag_knowledge_gap` tool + surface in answers (~1.5h) · AFK

## G2 — Service usage & run status visibility
Type: AFK · Blocked by: F4, C1, C2

**What to build:** Surface run status and provider/model usage (Apollo credits, web
calls, model usage) from the Run Ledger in the UI.

**Acceptance criteria:**
- [ ] Run status is visible per run
- [ ] Apollo/web/model usage is visible and traceable to runs

**Tasks:**
- [ ] G2.1 Usage rollup query over the ledger (~1.5h) · AFK
- [ ] G2.2 Usage + run-status UI surface (~1.5h) · AFK

## G3 — Seeded demo + end-to-end smoke path
Type: AFK · Blocked by: all above

**What to build:** A seeded demo scenario and an end-to-end smoke test covering the full
loop: imported memory → chat/routine run → enrichment → artifacts → review →
outcome/feedback → memory update.

**Acceptance criteria:**
- [ ] One command seeds a believable demo dataset
- [ ] An end-to-end smoke path runs the full loop and asserts the key steps
- [ ] The demo is repeatable from a clean database

**Tasks:**
- [ ] G3.1 Demo seed/fixture dataset (~2h) · AFK
- [ ] G3.2 End-to-end smoke test of the full loop (~2h) · AFK
- [ ] G3.3 Demo runbook + reset script (~1h) · AFK

**FINAL DEMO:** the full sourcing loop, dependable and repeatable.

---

## Counts (rough)

- Slices: 27 (across Foundation + A–E + G)
- Tasks: ~80 at ~1–2hr each
- HITL gates: 7 (parity sign-off, 4 design reviews, artifact shape, routine page)

## Open questions for review

1. **Granularity** — are ~80 tasks at 1–2hr the right grain, or do you want slices
   merged (fewer, thicker) or split further?
2. **Memory port (A1–A3)** — this is the biggest risk. Is the parity-test gate (A3.4)
   the right safeguard, or do you want a lighter/heavier check?
3. **HITL/AFK** — the 4 design reviews (chat, detail page, artifact panel, routine page)
   are marked HITL. Collapse into one "design pass" slice, or keep per-surface?
4. **Parallelism** — B and C can run in parallel after A. Want me to annotate a
   suggested 2-person split, or keep it dependency-only?
5. **Publish** — when approved, want these pushed to the issue tracker via `/to-issues`
   step 5, or stay local md?
