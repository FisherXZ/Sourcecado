# Sourcecado Course Plan

Status: Current course design. Repo-specific Guided Tickets are intentionally deferred until the course lead declares the local app frozen outside this document.

## Course promise

Students change Sourcecado. They read the working product, change it, verify it, review each other's work, and leave merged improvements behind. They do not build a toy agent or a separate capstone app.

## Program shape

The course runs for nine weeks:

- **Weeks 1–5:** Teaching Weeks with active building. Each week introduces one engineering layer through the active Sourcecado code while students continue real Guided Tickets or Build Projects.
- **Weeks 6–9:** Open Build Weeks. Students continue Build Projects, review pull requests, integrate changes, and recover from failures. No new lesson deck is introduced.
- **Week 6:** Includes the mandatory Eval Checkpoint inside the first Open Build Week.
- **Course Demo:** Happens after the nine-week program.

Teaching and building run together. During Weeks 1–5, each lesson is applied to live repository work. Students keep branches and draft pull requests while the teaching sequence moves.

## The codebase students learn

The course teaches the active local product only:

- React/Vite interface and Tauri desktop shell
- local `/v1` application boundary and Python/FastAPI backend
- SQLite indexes, append-only JSONL events, person files, and local state
- model messages, provider adapters, streaming, the agent loop, and tool execution
- Gmail, Drive, and Calendar through student-owned Google OAuth connections
- permissions, approval gates, source references, knowledge gaps, and the Run Ledger
- Python tests, GUI tests, TypeScript typecheck/build, and GitHub Actions CI

The retired hosted Next.js/Postgres application is historical reference, not course architecture. The course does not claim that Sourcecado currently has Postgres, shared user login, RLS, team tenancy, or a lint CI gate.

## Student environment

Each student works in a Personal Integration Environment:

- the course lead issues one Student Model Key with an individual usage limit;
- the student connects their primary Google account through Sourcecado's OAuth flow;
- mandatory coursework follows the Zero-Spend Rule;
- paid accounts, purchased credits, and Apollo enrichment are not prerequisites;
- code that exercises external effects must preserve Sourcecado's approval and redaction policies.

## Contribution workflow

1. The course lead assigns a real Guided Ticket from the live Sourcecado repository.
2. The student reads the nearby product language, code, and tests before proposing a change.
3. The student creates one branch and uses AI-Assisted Engineering Practice to investigate, plan, implement, and draft verification.
4. The student adds or updates appropriate automated tests and runs the relevant verification commands.
5. If agent behavior changed, the student performs a Live Behavior Demonstration.
6. The student opens a pull request containing an AI Accountability Note.
7. At least one other student gives Peer Approval after inspecting the change and evidence.
8. CI must be green.
9. The course lead performs the Course Lead Merge.

No student commits directly to `main`, self-merges, or merges a peer's change.

## Five Teaching Weeks with Active Building

Full audience-facing slide copy is maintained in [TEACHING_DECKS.md](TEACHING_DECKS.md).

### Week 1 — Work like an AI-assisted engineering team

**Outcome:** Students can take a real ticket from assignment to a reviewable pull request without trusting AI blindly.

**Core topics:**

- issue → branch → change → automated tests → pull request → Peer Approval → Course Lead Merge
- what AI is useful for: code discovery, explanation, planning, and test drafting
- prompts, context management, skills, connectors, and verification
- reading the diff and explaining every changed behavior
- credential, personal-data, and private-club-data boundaries
- CI as evidence: Python tests, GUI tests, typecheck, and build

**Deck outline:**

1. Sourcecado — Week 1: Work like an engineering team
2. A change is a team event, not a private coding session
3. The Sourcecado contribution path
4. AI helps with discovery and drafts; the student owns the result
5. Skills, connectors, prompts, and context are different tools
6. A pull request explains the problem, change, and verification
7. The Merge Gate and AI Accountability Note
8. First Guided Ticket

### Week 2 — Trace a full-stack product change

**Outcome:** Students can trace one visible behavior through the active application and change a bounded part without guessing at the other layers.

**Core path:**

`React/Tauri → local /v1 API → validation → FastAPI → SQLite/JSONL → response → loading/error UI`

**Core topics:**

- a feature includes validation, persistence, response contracts, UI state, and error recovery
- SQLite schema evolution and backward-compatible local-state changes
- local API authentication versus Google connector OAuth versus action approval
- loading, empty, success, invalid-input, server-error, and stale-state behavior
- testing the backend and GUI contract

**Deck outline:**

1. Sourcecado — Week 2: Follow one feature through the app
2. One click crosses several system boundaries
3. The active Sourcecado request path
4. Local state: SQLite indexes and append-only records
5. Authentication, connector authorization, and approval are different
6. The frontend must represent failure honestly
7. Tests and CI cover the active stack
8. Product Engineering Guided Ticket

### Week 3 — Understand and change the agent loop

**Outcome:** Students can trace one Sourcecado turn from user message through model and tool activity to its terminal result.

**Core topics:**

- an agent is a loop, not only a chatbot
- LLM messages and tool schemas
- the Thought → Action → Observation learning model
- Sourcecado's message → provider → tool request → tool result → provider → answer path
- provider adapters and typed streaming events
- step limits, cancellation, partial results, provider failures, and recovery
- what is persisted and what is not hidden reasoning

The conceptual progression adapts the fundamentals from the [Hugging Face Agents Course](https://huggingface.co/learn/agents-course/) to Sourcecado's actual runtime rather than teaching several unrelated frameworks.

**Deck outline:**

1. Sourcecado — Week 3: How the agent works
2. An agent repeatedly decides, acts, and observes
3. Messages give the model context; tools give it actions
4. Thought → Action → Observation
5. Trace the Sourcecado turn loop
6. Provider adapters and streaming
7. Stop conditions, cancellation, and recovery
8. Agent Engineering Guided Ticket

### Week 4 — Make tools, memory, and evidence trustworthy

**Outcome:** Students can explain why a model request does not automatically become an action and how Sourcecado preserves evidence about what happened.

**Core topics:**

- a tool contract: name, purpose, input schema, permission class, and result
- validation, permission decisions, approval, execution, and failure recording
- read actions, internal writes, external effects, and credit-sensitive actions
- operator memory versus person-centered Sourcing Memory
- Source Material, Source Reference, Artifact, Knowledge Gap, and Living Brief
- Run Ledger versus content-free telemetry versus behavioral evaluation
- choosing a Build Project and defining its live behavior scenario

**Deck outline:**

1. Sourcecado — Week 4: Tools, memory, and evidence
2. Tools let the model request real work
3. A strict tool contract constrains probabilistic behavior
4. The model asks; Sourcecado's policy decides
5. Memory has an operator layer and a person-file layer
6. Evidence must remain traceable to Source Material
7. Ledger, telemetry, tests, and evals answer different questions
8. Choose the Build Project

### Week 5 — Evaluate agent behavior and keep building

**Outcome:** Students can define a specific agent-behavior scenario, inspect operational evidence, and use the result to choose the next implementation change while continuing active Build Project work.

**Core topics:**

- automated tests versus live behavior demonstrations versus repeatable evals
- defining scenario inputs, expected tool use, blocked actions, evidence, and terminal results
- Run Ledger activity versus content-free telemetry
- baseline and candidate behavior
- turning a failed run into a specific engineering change
- preparing the mandatory Week 6 Eval Checkpoint

**Deck outline:**

1. Sourcecado — Week 5: Evaluate, learn, and keep building
2. Passing tests is necessary but not sufficient for agent behavior
3. Tests, demonstrations, and evals answer different questions
4. Inspect the Run Ledger and telemetry without confusing them
5. A useful eval scenario is specific
6. Run → inspect → change → repeat
7. Prepare the Week 6 Eval Checkpoint
8. Continue the next mergeable Build Project slice

## Four Open Build Weeks

### Week 6 — Eval Checkpoint and open build

- continue implementation; no new lesson deck;
- run the defined live behavior scenario;
- inspect the result and relevant Run Ledger activity;
- record what worked, what failed, and what the next implementation change must address.

### Week 7 — Integrate

- connect the feature across its required product boundaries;
- resolve peer review and integration failures;
- keep the pull request reviewable rather than accumulating an unbounded diff.

### Week 8 — Make it hold up

- exercise empty, failed, denied, stale, interrupted, and recovery states;
- add or update automated tests for discovered failures;
- repeat the live behavior scenario when agent behavior changed.

### Week 9 — Merge readiness and handoff

- satisfy the Merge Gate;
- finish Peer Approval and course-lead review;
- document known limits, operating assumptions, and the next useful ticket;
- prepare the Course Demo outside the nine-week program.

## Course Demo

Each Build Project demonstrates:

1. the real product problem;
2. the changed feature working;
3. automated verification;
4. the live behavior scenario when agent behavior changed;
5. one failure encountered and the resulting design change;
6. the handoff: known limits and the next useful product ticket.

## Ticket timing

This document defines the Ticket Blueprint, learning categories, and review rubric. Actual repo-specific Guided Tickets are written only after the course lead declares the local app frozen outside this session.

Real Product Tickets may vary in scope. There is no artificial line-count, file-count, or one-week size rule. The ticket must still state acceptance criteria, non-goals, dependencies, and verification clearly enough for a student and reviewer to know whether the change is ready.

## Learning categories for future tickets

- AI-assisted workflow and product-language alignment
- pull-request, CI, documentation, and accessibility improvements
- full-stack application behavior across backend and GUI
- local-state compatibility and recovery
- provider, streaming, and turn-loop behavior
- tool schemas, permissions, approval safety, and connector behavior
- person files, source references, evidence, and memory
- Run Ledger, telemetry, and behavioral evaluation

These are categories, not a ticket backlog. Product needs after the freeze determine the actual tickets.

## Review rubric

A reviewer and the course lead check:

- **Intent:** Does the change solve the ticket's user problem without expanding beyond its boundaries?
- **Correctness:** Do the normal path and relevant failure paths behave truthfully?
- **Verification:** Did the student add or update appropriate automated tests and run the stated commands?
- **Agent behavior:** If applicable, did the Live Behavior Demonstration exercise the changed behavior successfully?
- **Safety:** Does the change preserve approval, credential, privacy, source, and external-effect boundaries?
- **Clarity:** Can another engineer understand the diff, rationale, and known limits?
- **AI accountability:** Does the PR state what AI helped with, what was verified, and what remains uncertain?
- **Collaboration:** Is CI green and has at least one peer given meaningful approval?
