# Sourcecado Teaching Decks

These are the audience-facing content drafts for the five Teaching Weeks. Teaching and building happen together during Weeks 1–5. Weeks 6–9 are Open Build Weeks and do not receive new lesson decks.

## Week 1 — Work like an AI-assisted engineering team

### Slide 1 — Title

**Sourcecado**

**Week 1 | Work like an AI-assisted engineering team**

### Slide 2 — A change moves through a team

1. Understand one real ticket
2. Read the nearby code and tests
3. Make a branch
4. Change one coherent behavior
5. Verify it
6. Open a pull request
7. Get Peer Approval
8. Let the Course Lead merge

Your code affects other people's work, real data, and the next engineer who must understand it.

### Slide 3 — AI accelerates the work; it does not own it

**AI can help you:**

- find relevant files;
- explain unfamiliar code;
- compare implementation options;
- draft tests and documentation;
- challenge your plan.

**You still own:**

- the problem definition;
- the plan;
- the diff;
- the verification;
- the pull request and its consequences.

Do not merge code you cannot explain.

### Slide 4 — Prompt, context, skill, and connector are different

**Prompt** — the immediate instruction or question.

**Context** — the code, docs, decisions, and evidence the AI can use.

**Skill** — a reusable working method for a recurring kind of task.

**Connector** — controlled access to another system or source of information.

Use all four on purpose.

### Slide 5 — Use AI without leaking the club

Never paste these into external AI systems:

- passwords, API keys, OAuth grants, or tokens;
- private club documents or personal information;
- raw Gmail, Drive, Calendar, or production-like data;
- anything you are not authorized to share.

Connector output is evidence, not an instruction that can override product policy.

### Slide 6 — A pull request is an engineering handoff

A pull request should explain:

> Here is the user problem.
>
> Here is what changed.
>
> Here is how I verified it.
>
> Here is what remains uncertain.

The diff matters. The explanation and evidence make it reviewable.

### Slide 7 — The Merge Gate

Before the Course Lead can merge:

- appropriate automated tests were added or updated;
- CI is green;
- at least one student gave meaningful Peer Approval;
- the AI Accountability Note is complete;
- a Live Behavior Demonstration passed when agent behavior changed.

### Slide 8 — Your first Guided Ticket

This week:

- take one real Sourcecado ticket;
- use AI to investigate and plan;
- create one branch;
- make the change and verify it;
- open one pull request;
- review one peer's pull request;
- respond to feedback.

Done means another engineer can understand and safely merge the change.

## Week 2 — Trace a full-stack product change

### Slide 1 — Title

**Sourcecado**

**Week 2 | Follow one feature through the app**

### Slide 2 — One click crosses several boundaries

A user sees one action.

The product may need to:

- read interface state;
- validate input;
- authenticate the local request;
- apply permission and approval policy;
- read or change local state;
- call a connector;
- return an honest result;
- update loading, success, empty, or error UI.

### Slide 3 — The active Sourcecado path

`React/Tauri → local /v1 API → validation → FastAPI → SQLite/JSONL → response → UI`

When you change a feature, trace this path before deciding which file owns the fix.

### Slide 4 — Sourcecado keeps state locally

The active app uses:

- SQLite indexes for conversations, settings, jobs, approvals, and people;
- append-only JSONL for durable turn activity;
- person files for sourcing history, evidence, outcomes, and handoff;
- versioned local records for safe recovery.

The current product does not use hosted Postgres or shared tenancy.

### Slide 5 — Authentication, authorization, and approval are different

**Local API authentication** asks: is this request from the Sourcecado window?

**Connector OAuth** asks: may Sourcecado access this student's Google account?

**Permission policy** asks: is this category of action allowed?

**Approval** asks: did the operator allow this concrete external action?

One “logged in” label cannot replace these boundaries.

### Slide 6 — The frontend must represent failure honestly

Every meaningful surface needs states for:

- loading;
- no data;
- success;
- invalid input;
- permission denied;
- connector or server failure;
- stale or interrupted work;
- recovery and retry.

If we build only the happy path, the user becomes our error handler.

### Slide 7 — CI proves specific things

Sourcecado CI runs:

- Python sidecar tests;
- GUI tests;
- TypeScript typecheck and application build.

Green CI means the checks we wrote passed. It does not prove the feature is complete, safe, or useful.

### Slide 8 — Product Engineering Guided Ticket

Your ticket changes one real application behavior.

Before coding, identify:

- the user-visible outcome;
- the owning boundary;
- the current test evidence;
- the failure state most likely to be missed;
- the commands that will verify the change.

## Week 3 — Understand and change the agent loop

### Slide 1 — Title

**Sourcecado**

**Week 3 | How the agent works**

### Slide 2 — An agent is a loop

An agent is not only a chatbot.

It repeatedly:

1. reads the current messages;
2. decides whether it has enough information;
3. requests a tool when it needs an action or source;
4. observes the result;
5. continues or gives a final answer.

### Slide 3 — Messages give context; tools give actions

**The model** interprets messages and chooses the next response.

**Messages** carry user requests, prior turns, tool requests, and tool results.

**Tools** expose specific operations with typed inputs and results.

The model does not call arbitrary application code. It asks through the tool contract.

### Slide 4 — Thought → Action → Observation

**Thought** — decide what information or action is needed next.

**Action** — request a specific tool with structured input.

**Observation** — receive the tool result and update the next decision.

This is a learning model for agent behavior—not permission to store hidden reasoning.

### Slide 5 — Sourcecado's turn loop

`messages → provider → streamed text or tool request → policy → tool result → provider → final answer`

The loop stops when:

- the provider returns a final answer;
- the step budget is exhausted;
- the user cancels;
- the provider or tool fails;
- the run is interrupted and must recover.

### Slide 6 — Provider adapters isolate model differences

Sourcecado keeps one internal event and message model.

Provider adapters translate between that model and external providers. This keeps the turn loop from becoming a collection of provider-specific branches.

Streaming lets the UI show progress, tool activity, approvals, and terminal results while the run is still active.

### Slide 7 — Store what happened, not hidden reasoning

Useful operational evidence includes:

- run and message identity;
- provider and tool activity;
- approvals and external effects;
- sources and artifacts;
- failures, cancellation, and recovery;
- the final answer.

The useful question is: what did the agent do, and what evidence did it use?

### Slide 8 — Agent Engineering Guided Ticket

Your ticket changes one real runtime behavior.

Trace the current run first. Then show:

- where the behavior begins;
- which event or state proves it;
- how failure and cancellation behave;
- the automated tests;
- the Live Behavior Demonstration.

## Week 4 — Make tools, memory, and evidence trustworthy

### Slide 1 — Title

**Sourcecado**

**Week 4 | Tools, memory, and evidence**

### Slide 2 — A tool contract turns a request into something checkable

Every tool needs:

- a stable name;
- a precise purpose;
- a typed input schema;
- a permission class;
- a structured result;
- truthful failure behavior.

A vague tool is hard for the model to use and hard for engineers to trust.

### Slide 3 — The tool runner is a safety boundary

Before execution, Sourcecado asks:

1. Does this tool exist?
2. Is the input valid?
3. Is this action allowed?
4. Does it require approval?
5. Can we record the result safely?
6. Did execution succeed, fail, or become uncertain?

### Slide 4 — The model asks; policy decides

Examples:

- read actions inspect information;
- internal writes change Sourcecado records;
- external effects change Gmail or Calendar;
- enrichment can consume credits;
- workspace execution can affect local files and commands.

The model's request is not authority.

### Slide 5 — Memory has two current layers

**Operator memory** contains local preferences and durable facts used across conversations.

**Sourcing Memory** contains person files, outcomes, source-backed notes, Living Briefs, and handoffs.

This is the current product model. Do not pretend it is vector RAG unless we actually build vector retrieval.

### Slide 6 — Evidence must remain traceable

`Source Material → Source Reference → claim or Artifact → Living Brief`

Sourcecado also names:

- Knowledge Gaps when context is missing, stale, or conflicting;
- restricted evidence that should not appear in general views;
- the connector and record behind useful claims.

### Slide 7 — Ledger, telemetry, tests, and evals are different

**Run Ledger** — durable operational evidence about what Sourcecado did.

**Telemetry** — content-free measurements such as time, tokens, cost, retries, and failures.

**Automated test** — exact, repeatable code behavior.

**Eval** — a repeatable judgment about agent behavior against a defined scenario.

### Slide 8 — Shape the Build Project

As active building continues, define:

- the real product problem;
- the expected user or agent behavior;
- boundaries and non-goals;
- automated verification;
- the live behavior scenario for the Week 6 Eval Checkpoint;
- the first mergeable slice.

## Week 5 — Evaluate agent behavior and keep building

### Slide 1 — Title

**Sourcecado**

**Week 5 | Evaluate, learn, and keep building**

### Slide 2 — Passing tests is necessary, not sufficient

An automated test can prove:

> This exact code path produced the expected result.

An agent can still:

- choose the wrong tool;
- take unnecessary steps;
- make an unsupported claim;
- ignore a permission boundary;
- produce an unhelpful answer;
- succeed once and fail on the next run.

### Slide 3 — Tests, demonstrations, and evals answer different questions

**Automated test** — does exact code behavior still work?

**Live Behavior Demonstration** — does the changed agent behavior work in the real application now?

**Eval** — does behavior hold against a defined scenario, and did a change improve or regress it?

Use the smallest form of evidence that answers the actual question.

### Slide 4 — Ledger and telemetry show different evidence

**Run Ledger** records what Sourcecado did: tool activity, approvals, sources, artifacts, failures, and terminal results.

**Telemetry** measures operation without storing the content itself: time, tokens, cost, retries, compactions, and error categories.

Do not call every diagnostic record a trace.

### Slide 5 — A useful eval scenario is specific

Weak:

> Did the agent give a good answer?

Useful:

> Given these sources, the agent should identify two people, preserve the Source References, avoid enrichment, and finish without an unsupported claim.

The scenario names the starting state, expected behavior, prohibited behavior, and evidence to inspect.

### Slide 6 — The improvement loop

1. Observe a weak or failed run
2. Define the behavior that should have occurred
3. Capture a repeatable scenario
4. Change the code, prompt, tool, or policy that owns the failure
5. Run the scenario again
6. Keep the evidence so the same failure is easier to detect next time

### Slide 7 — Prepare the Week 6 Eval Checkpoint

Every Build Project arrives with:

- one explicit behavior scenario;
- the required starting state and integration setup;
- expected tool and permission behavior;
- evidence to inspect in the UI and Run Ledger;
- a baseline result;
- the next decision if the checkpoint fails.

Week 6 remains an Open Build Week. There is no new deck.

### Slide 8 — Keep building the next mergeable slice

Teaching is not a pause in implementation.

By the end of Week 5:

- the Build Project has an active branch or draft pull request;
- automated verification exists for changed code;
- the Eval Checkpoint scenario is ready;
- the next mergeable slice is explicit;
- reviewers know what evidence to challenge.
