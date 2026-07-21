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
- [x] `npm run dev` serves the app locally
- [x] `/chat` renders a placeholder; `/health` returns OK
- [x] README updated with run instructions

**Tasks:**
- [x] F1.1 Scaffold Next.js app shell + base layout/nav (~1h) · AFK
- [x] F1.2 Add `/chat` placeholder route + `/health` route (~1h) · AFK

## F2 — Postgres + pgvector available locally
Type: AFK · Blocked by: none

**What to build:** A reproducible local Postgres+pgvector via docker-compose, plus a DB
access layer and a migration runner that later slices add migrations to.

**Acceptance criteria:**
- [x] `docker compose up` starts Postgres with the `vector` extension enabled
- [x] App connects via env-configured connection string
- [x] Migration runner applies an empty baseline migration and is idempotent

**Tasks:**
- [x] F2.1 docker-compose Postgres + pgvector + `.env.example` (~1h) · AFK
- [x] F2.2 DB client/access layer + connection config (~1h) · AFK
- [x] F2.3 Migration runner + baseline migration (~1.5h) · AFK

## FD — Design Foundation (Warm Operator)
Type: AFK · Blocked by: F1 · Done: 2026-06-18

**What to build:** Wire DESIGN.md ("Warm Operator") into the app — Tailwind v4 tokens
+ self-hosted General Sans / Geist Mono, a reusable primitive kit
(`src/components/ui`: AppShell, Button, StatusPill/Tag, DataTable, Input/Toggle, Card,
EmptyState), retrofit of the F1 shell, and a `/styleguide` catalog. All later UI slices
build on these primitives instead of restyling. See
`docs/superpowers/plans/2026-06-18-design-foundation-retrofit.md`.

**Acceptance criteria:**
- [x] DESIGN.md tokens are the single source; no raw hex in `src/components` or `src/app/*.tsx`
- [x] Primitive kit exists with render tests; existing backend suite stays green
- [x] F1 shell renders on AppShell; UI renamed to Sourcecado; `/styleguide` matches the approved preview

**Tasks:**
- [x] FD.1 Design tokens + font wiring (~1h) · AFK
- [x] FD.2 UI test infra + Button/StatusPill (~1.5h) · AFK
- [x] FD.3 Input/Toggle/Card/EmptyState (~1h) · AFK
- [x] FD.4 DataTable (~1.5h) · AFK
- [x] FD.5 AppShell (~1.5h) · AFK
- [x] FD.6 F1 retrofit + Sourcecado rename (~1h) · AFK
- [x] FD.7 /styleguide catalog page (~1h) · AFK

## F3 — Model Gateway with usage logging
Type: AFK · Blocked by: F2 · Done: 2026-06-23 (PR #7 stack)

**What to build:** The single `callModel()` entry point (ADR-0004). Records named model
task, prompt/version name, usage, and errors to a `model_calls` table. Returns a real
provider response.

**Acceptance criteria:**
- [x] All model calls in the codebase go through the gateway (lint/grep check)
- [x] A call writes a `model_calls` row with task name, prompt/version, tokens, status
- [x] Provider error is captured, not thrown raw, and recorded

**Tasks:**
- [x] F3.1 `model_calls` migration (task name, prompt/version, usage, status, error) (~1h) · AFK
- [x] F3.2 `callModel()` with named tasks + prompt/version naming (~1.5h) · AFK
- [x] F3.3 Structured-output parse helper + error capture + usage counters (~1.5h) · AFK

## F4 — Run Ledger spine
Type: AFK · Blocked by: F2 · Done: 2026-06-23 (PR #7 stack)

**What to build:** The Run Ledger tables and write path (ADR-0002): `runs`,
`run_steps`, `tool_calls`, plus linkage to `model_calls`. A run inspector view renders
the trace for one run.

**Acceptance criteria:**
- [x] Starting a run creates a `runs` row; each step/tool/model call is recorded
- [x] Final run status (success/error) is persisted
- [x] Run inspector renders the full trace (steps, tool calls, model calls, usage) for a run id (`/runs/[id]`, gated by `SOURCECADO_ENABLE_RUN_INSPECTOR`)

**Tasks:**
- [x] F4.1 `runs` + `run_steps` + `tool_calls` migrations (~1.5h) · AFK
- [x] F4.2 Run create + step/tool/model logging write path (~1.5h) · AFK
- [x] F4.3 Run status + error capture on the run (~1h) · AFK
- [x] F4.4 Run inspector view (read-only trace render) (~1.5h) · AFK (build with src/components/ui primitives)

## F5 — Agent Harness ReAct loop
Type: AFK · Blocked by: F3, F4 · Done: 2026-06-23

**What to build:** The ReAct-style tool-use loop, a tool registry with permission
classes (`read`/`enrich`/`reason`/`draft`/`write_internal`/`admin`), and one `echo`
tool. Every step writes to the Run Ledger via F4. See
`docs/superpowers/specs/2026-06-23-f5-agent-harness-react-loop-design.md` and
`docs/superpowers/plans/2026-06-23-f5-agent-harness-react-loop.md`.

**Acceptance criteria:**
- [x] A run executes a multi-step loop that calls the model via the gateway and at least one registered tool
- [x] Tool registry enforces permission classes (a tool above the run's allowed class is refused and logged)
- [x] The full run (steps, tool calls, model calls, status) appears in the run inspector

**Tasks:**
- [x] F5.1 ReAct loop (observation → model → tool → repeat, with stop condition) (~2h) · AFK
- [x] F5.2 Tool registry + permission classes + class enforcement (~1.5h) · AFK
- [x] F5.3 `echo` reference tool + wire loop end-to-end to ledger (~1h) · AFK

**FOUNDATION DEMO:** type a question in `/chat`, the harness runs a multi-step loop,
calls the model through the gateway and the echo tool, writes the whole trace to the Run
Ledger, and you can inspect it. Nothing sourcing-specific yet — the spine works.

---

# A. CITED SOURCING MEMORY ANSWER

Port the existing SQLite memory brain (`src/`) into the hosted app and expose it through
Research Chat. **Highest-risk reuse in the project** — isolated here with a parity test.

> **Status: core delivered (2026-06-25).** Implemented per the reframed design
> `docs/superpowers/specs/2026-06-25-feature-a-cited-memory-answer-design.md` on branch
> `feat/a-memory-impl` → **PR #8**. Principal-reviewed (merge risk LOW), 258 tests pass,
> proven live on 3 complex queries (claude-sonnet-4-6). **Reframe vs this breakdown:** the
> A3 parity-test gate was **cut** (decision #6 — build/run/validate instead) and the model
> now synthesizes the prose (no `answer.ts` prose port); the in-app import UI (A4) and the
> memory-management page (A7.2) are **deferred** to later UI slices — the CLI `npm run
> ingest`/`refresh` and the `add_memory_note` tool cover those paths for now.

## A1 — Memory schema + ingest ported to Postgres/pgvector
Type: AFK · Blocked by: F2 · Done: 2026-06-25 (PR #8)

**What to build:** Port `source_records` and `memory_chunks` to Postgres with a pgvector
embedding column, plus the ingest + chunking path from `src/ingest.ts` / `src/chunk.ts`.
Carry ADR-0001 permission metadata at the source and chunk level.

**Acceptance criteria:**
- [x] Source records and chunks persist in Postgres with permission/source metadata (migration `002_memory.sql` + `source_permissions`)
- [x] Ingesting a sample file produces chunks with citations (`sourceId#chunk-N` / `#row-N`)
- [x] Embedding column is pgvector, not a text blob (`vector(1536)` + hnsw cosine index)

**Tasks:**
- [x] A1.1 `source_records` + `memory_chunks` migration with pgvector + permission cols (~1.5h) · AFK
- [x] A1.2 Port ingest (source record creation, content hashing, dedupe) (~2h) · AFK — `src/lib/memory/ingest.ts` + `scripts/ingest.ts`
- [x] A1.3 Port chunking + citation construction (~1.5h) · AFK — `src/lib/memory/chunk.ts`

## A2 — Embeddings + cited retrieval ported
Type: AFK · Blocked by: A1 · Done: 2026-06-25 (PR #8)

**What to build:** Replace the in-process text-blob cosine (`src/embeddings.ts`) with
pgvector similarity search, and port the cited-retrieval path. Permission filter applies
before retrieval (ADR-0001).

**Acceptance criteria:**
- [x] Retrieval uses pgvector similarity, filtered by the caller's allowed sources (cosine gated by lexical, permission filter in SQL WHERE)
- [x] Retrieved chunks carry citations
- [x] A restricted source never surfaces to a caller without access (proven by test)

**Tasks:**
- [x] A2.1 Embedding generation through the Model Gateway → pgvector column (~2h) · AFK — `src/lib/memory/embed.ts` (pluggable 1536-dim, gateway or hash fallback)
- [x] A2.2 pgvector similarity query with pre-retrieval permission filter (~2h) · AFK — `src/lib/memory/retrieve.ts` + `permissions.ts`

## A3 — Answer logic ported — REFRAMED (parity gate cut)
Type: ~~HITL~~ AFK · Blocked by: A2 · Done: 2026-06-25 (PR #8)

**What to build:** Port the intent classification + accepted/gap fact logic from
`src/answer.ts` (420 LOC). ~~Gate the port behind a parity test.~~ **Reframed (2026-06-25
spec, decision #6):** port the retrieval/ranking/fact-lifecycle behind `search_memory` and
let the **model synthesize** the prose (the deterministic `answer.ts` templates are the one
part NOT ported). **No parity harness/gate** — build, run on real data, validate. A
deterministic citation post-check replaces the prose-parity concern.

**Acceptance criteria:**
- [x] Cited Sourcing Memory Answer produced from Postgres with intent + gaps (model writes Answer/Evidence/Gaps/Next-Action from the tool bundle)
- [x] ~~Parity test compares SQLite vs Postgres answers~~ → **cut**; replaced by a citation post-check (cited ids must exist in the tool result) + live validation on 3 complex queries
- [x] ~~Human-reviewed parity report~~ → **cut** (no parity gate); principal review covered correctness instead

**Tasks:**
- [x] A3.1 Port intent classification + fact selection (~2h) · AFK — `src/lib/memory/rank.ts` (questionIntent/factIntentScore/rankRows) + `retrieve.ts`
- [x] A3.2 Port gap-fact handling + no-memory/no-relevant-memory paths (~1.5h) · AFK — `gapFacts` in `retrieve.ts` + refuse-on-empty in `MEMORY_INSTRUCTIONS`
- [x] ~~A3.3 Parity harness~~ — **cut** (decision #6)
- [x] ~~A3.4 Parity review gate~~ — **cut** (decision #6); citation post-check (`src/lib/memory/citations.ts`) is the deterministic guard

## A4 — File/export memory import path
Type: AFK · Blocked by: A1 · Done: 2026-06-25 (PR #9)

**What to build:** App-side file/export import (roadmap: ingestion is file/export only).
Imported sources become source records with citations.

> **Correction (2026-07-21):** this was marked deferred earlier the same day (2026-06-25,
> 14:06) but was actually built later that evening — `df7fd64` (19:28) added the in-memory
> `ingestFiles` core + `POST /api/memory/import`. The doc was never updated after. Not deferred.

**Acceptance criteria:**
- [x] A user can import a file/export from the app and see it become source records — `POST /api/memory/import`
- [x] Import surfaces per-file success/skip with reasons (no silent skips) — done in `ingestFolder`

**Tasks:**
- [x] A4.1 Import endpoint + source-record creation from upload (~2h) · AFK — `src/lib/memory/ingest.ts` (`ingestFiles`) + `src/app/api/memory/import/route.ts`
- [ ] A4.2 Import result UI with per-file status/skip reasons (~1.5h) · AFK — still no dedicated result UI; route returns per-file status but nothing renders it yet

## A5 — search_memory tool
Type: AFK · Blocked by: A2, F5 · Done: 2026-06-25 (PR #8)

**What to build:** A `search_memory` tool (class `read`) that wraps cited retrieval and
plugs into the harness registry, replacing the echo tool for memory questions.

**Acceptance criteria:**
- [x] `search_memory` returns cited chunks/facts to the loop (`{intent, acceptedFacts, gapFacts, chunks}`)
- [x] Tool call + result recorded in the Run Ledger

**Tasks:**
- [x] A5.1 `search_memory` tool wrapping retrieval + register it (~1.5h) · AFK — `src/lib/tools/search-memory.ts` + `memoryRegistry()`

## A6 — Research Chat answers from memory
Type: HITL · Blocked by: A3, A5, F1 · Core done: 2026-06-25 (PR #8); design review pending

**What to build:** Minimal Research Chat UI that triggers an agent run, shows run
status/result inline, and renders the cited answer with gaps. HITL for one chat-layout
design review.

**Acceptance criteria:**
- [x] A Sourcing Director asks a question and gets a cited answer in chat (reused `/chat` + memory run config; verified live)
- [x] Run status shows while the run executes; result renders with citations + gaps
- [x] A link opens the run inspector for that answer (`/runs/[id]`)

**Tasks:**
- [x] A6.1 Research Chat UI: message list + input + run trigger (~2h) · AFK — reused the F5 `/chat` (ChatClient) against the memory config
- [x] A6.2 Inline run status + cited answer + gaps render (~1.5h) · AFK — 4-section answer + `invalidCitations` from `/api/agent`
- [ ] A6.3 Verify Research Chat matches DESIGN.md + uses src/components/ui primitives (~1h) · HITL — **pending**, and now higher-stakes: the undocumented `rt-R1`–`rt-R6` runtime rewrite (2026-07-14/15, PRs #11–#16 — raw-SDK provider adapters, hand-rolled agent loop, tool orchestrator, context/prompt v5, SSE streaming rewire, server-side chat sessions) substantially changed this UI/streaming after this checkbox was written. No design doc exists for that rewrite; review it against DESIGN.md fresh rather than assuming this checkbox's original scope still applies.

## A7 — Memory management page (add/correct)
Type: AFK · Blocked by: A3 · Done: 2026-06-25 (PR #9)

**What to build:** Memory management page to add a memory note and correct existing
memory (the memory-correction path from `src/`).

> **Correction (2026-07-21):** A7.2 was marked deferred earlier the same day (2026-06-25,
> 14:06) but shipped later that night — `599792d` (memory sources backend) and `4cfd62b`
> (23:22, `/memory` page: sources list with archive/restore + add-note) both landed after
> the doc was written. Not deferred.

**Acceptance criteria:**
- [x] A user can add a memory note; it becomes retrievable (`add_memory_note` tool — immediately searchable)
- [x] A user can correct memory; corrections affect future answers (superseding note, no destructive edit)
- [x] …from a dedicated **app page** — `src/app/memory/MemoryClient.tsx` (list, archive/restore, add-note)

**Tasks:**
- [x] A7.1 `add_memory_note` tool + write path (~1.5h) · AFK — `src/lib/tools/add-memory-note.ts` + `src/lib/memory/notes.ts`
- [x] A7.2 Memory management page: list + add + correct (~2h) · AFK — `src/app/memory/MemoryClient.tsx` + `src/app/api/memory/sources/*` routes

**FEATURE A DEMO:** ~~import sourcing notes~~ (CLI `npm run ingest`), ask a question in chat,
get a cited answer with gaps, inspect the run, and correct memory (`add_memory_note`). ✅
Demonstrated live 2026-06-25 on 3 complex queries.

---

# B. SOURCING STATE MODEL

Give the agent enough sourcing state to act like a Sourcing Director. Each entity is
added here (not in a day-1 schema).

## B1 — Organizations & Contacts + identity resolution + Contact Profile Card
Type: AFK (mostly) · Blocked by: F2

> **Expanded scope (2026-07-21):** pulled forward and widened beyond the original
> read-only B1 to deliver a Contact Profile Card — a single readable card showing a
> person's identity, our relationship with them, key facts, and knowledge gaps, for the
> consulting/sourcing use case of building on past connections while creating new ones.
> Pulls `outreach_history` + `list_outreach_history` forward from B3 (see note there);
> B3's outcome-state-machine and follow-up-sequence tables stay deferred.

**What to build:**
- `organizations` + `contacts` + `contact_aliases` schema. Contacts require **name, role,
  and organization** at intake — not name alone — so a company can later be looked up for
  everyone known there.
- `get_contact` / `get_organization` read tools with resolution: exact canonical name →
  exact alias → else return an **ambiguous** result (candidates + distinguishing org/title)
  rather than guessing. This is a third result state alongside found/not-found, not an
  error. `get_organization` also lists known contacts at that org.
- `create_contact` (write_internal tool): the write path for a new connection surfaced in
  chat. Requires name + role + org (creating the org if new); if the Director gives an
  incomplete name, the agent asks for the missing field before writing rather than saving a
  thin record — if the Director explicitly doesn't know a field, it's saved and flagged as
  a Knowledge Gap rather than blocking. Doctrine addition to `context.ts`'s Sourcing
  doctrine section.
- Ambiguity resolution is chat-time only (a human is present to ask). Bulk import (file
  upload, CSV) has no one to ask mid-file: unambiguous exact matches auto-link, anything
  ambiguous or unmatched stays unlinked and becomes a Knowledge Gap for later review — no
  auto-merge, ever. **The review surface for those flagged mentions is an explicit
  fast-follow, not built in this pass** — revisit once real historical data is actually
  being imported and the volume of unresolved mentions is known.
- Contact Profile Card component: renders when `get_contact` resolves a person —
  **Identity** (name, org, role) · **Relationship** (past-collaborator flag + interaction
  timeline from `list_outreach_history`) · **Key Facts** (cited, from existing
  `search_memory`) · **Knowledge Gaps** (existing `gapFacts`, not currently surfaced
  visually anywhere).

**Acceptance criteria:**
- [ ] Organizations and Contacts persist and link (an Org can contain Contacts); every Contact has name, role, and org
- [ ] `get_contact` / `get_organization` return records to the loop and log to the ledger
- [ ] Two contacts sharing a name/alias never silently merge — `get_contact` returns `ambiguous` with candidates instead
- [ ] `create_contact` refuses to silently save a partial identity; missing fields are asked for or explicitly flagged as gaps, never dropped
- [ ] A Contact Profile Card renders identity + relationship timeline + cited facts + gaps in one place

**Tasks:**
- [x] B1.1 `organizations` + `contacts` (name, role, org_id) + `contact_aliases` migration (~2h) · AFK — `src/migrations/006_contacts.sql` (TDD, `tests/contacts-migrate.test.ts`)
- [x] B1.2 `get_contact` + `get_organization` tools — alias resolution, `ambiguous` result state, org→contacts listing (~2.5h) · AFK — `src/lib/contacts/resolve.ts` + `src/lib/tools/get-contact.ts` + `src/lib/tools/get-organization.ts`, registered in `memoryRegistry()`
- [x] B1.3 `create_contact` tool (write_internal) — requires name+role+org, gap-flags missing fields (~1.5h) · AFK — `src/lib/contacts/create.ts` + `src/lib/tools/create-contact.ts`, registered in `memoryRegistry()`
- [ ] B1.4 `outreach_history` migration — relationship timeline, pulled forward from B3 (~1.5h) · AFK
- [ ] B1.5 `list_outreach_history` tool (read) (~1h) · AFK
- [ ] B1.6 Contact Profile Card component (Identity/Relationship/Key Facts/Gaps) (~2h) · AFK
- [ ] B1.7 Sourcing doctrine update: gather name+role+org on new-connection intake (~1h) · AFK
- [ ] B1.8 Verify card matches DESIGN.md + uses src/components/ui primitives (~1h) · HITL

## B2 — Target Personas
Type: AFK · Blocked by: B1

**What to build:** `target_personas` (role/profile patterns per Org) + association to
Organizations.

**Acceptance criteria:**
- [ ] Target Personas persist and associate to an Organization
- [ ] Personas are retrievable for use by artifact generators later

**Tasks:**
- [ ] B2.1 `target_personas` migration + association (~1.5h) · AFK

## B3 — Outreach Outcomes, Follow-Up state
Type: AFK · Blocked by: B1

> **Note (2026-07-21):** `outreach_history` (migration) and `list_outreach_history` (read
> tool) were pulled forward into B1.4/B1.5 for the Contact Profile Card's relationship
> timeline — don't rebuild them here. What's left is the outcome-state-machine and
> follow-up-sequence scheduling, which is write/state work the card doesn't need.

**What to build:** `outreach_outcomes`, `followup_sequence` state + the write tools.
Sourcing Lead is a state of a Contact, not a new table (per CONTEXT.md).

**Acceptance criteria:**
- [ ] Outcomes persist against a Contact's outreach history
- [ ] Follow-Up Sequence state can be read and updated

**Tasks:**
- [ ] ~~B3.1 `outreach_history` migration~~ — done, see B1.4
- [ ] B3.1b `outreach_outcomes` migration (~1h) · AFK
- [ ] B3.2 `followup_sequence` state migration (~1h) · AFK
- [ ] ~~`list_outreach_history` tool~~ — done, see B1.5
- [ ] B3.3 `record_outreach_outcome` + `update_followup_sequence` tools (~1.5h) · AFK

## B4 — Contact / Organization detail page
Type: HITL · Blocked by: B3

**What to build:** Detail page showing an Org/Contact with relevant history, outcomes,
and artifacts. HITL for one design review.

**Acceptance criteria:**
- [ ] A user inspects an Org or Contact and sees history, outcomes, follow-up state, artifacts
- [ ] Page links to the runs that produced those artifacts

**Tasks:**
- [ ] B4.1 Detail page data loaders + layout (~2h) · AFK (build with src/components/ui primitives)
- [ ] B4.2 History/outcomes/artifacts panels (~2h) · AFK (build with src/components/ui primitives)
- [ ] B4.3 Verify Contact/Org detail matches DESIGN.md + uses primitives (~1h) · HITL

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
- [ ] D1.2 Artifact panel UI + draft/revise/save flow (~2h) · AFK (build with src/components/ui primitives)
- [ ] D1.3 Save-artifact-to-memory path (~1h) · AFK
- [ ] D1.4 Verify artifact panel matches DESIGN.md + uses primitives; confirm artifact shape (~1h) · HITL

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
- [ ] E2.1 Routine setup page (config form) (~2h) · AFK (build with src/components/ui primitives)
- [ ] E2.2 Manual "Run now" → harness run + results in chat (~2h) · AFK
- [ ] E2.3 Verify Routine setup page matches DESIGN.md + uses primitives (~1h) · HITL

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
- [ ] G2.2 Usage + run-status UI surface (~1.5h) · AFK (build with src/components/ui primitives)

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
