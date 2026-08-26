# Golden Workflow Baseline — 2026-08-26

Branch: `codex/product-validation-sprint`  
App: local FastAPI sidecar + React/Vite browser surface  
Model: configured DeepSeek provider  
Mode: real connectors and private sources; read-only product run; no enrichment,
draft creation, sending, or Drive writes

This report intentionally omits private contact details and source bodies. The
full evidence remains in the local Sourcecado transcripts and event ledger.

## Verdict

Both v0 skills can eventually produce a useful review package, but neither can
complete its job in one autonomous run. Both expanded research until manually
stopped, then required a second user turn instructing the agent to stop using
tools and synthesize the preserved evidence.

This is a productive failure. The sourcing reasoning is promising; the mature
agent operating-system behavior is not yet present.

## Outreach campaign

### Execution

- The skill loaded successfully through the live skill catalog.
- The first run gathered 33 visible Drive evidence items and completed eleven
  displayed actions over roughly 74 seconds before manual cancellation.
- Cancellation produced a durable interrupted receipt and preserved completed
  tool work.
- A second turn requested convergence from existing evidence. It made two more
  source reads, then produced the director-review package.
- Reloading the app preserved the transcript, evidence, interrupted receipt,
  final response, and complete terminal state.

### Product result

The final package was useful. It:

- selected the current-semester SOP and template over older duplicates;
- found stale campaign-cycle language in the template;
- distinguished warm relationship follow-ups from cold outreach;
- produced a bounded five-contact review set with why-now, missing fields, and
  relationship risks;
- declined to spend Apollo credits, draft, or send without review;
- surfaced incomplete source coverage and requested concrete director choices.

### Failures

- Research did not naturally converge to the requested shortlist.
- The original prompt was appended to an existing QA thread after root restore
  selected the prior destination rather than the newly created chat.
- The final deliverable exists only as assistant prose, not as a campaign
  artifact or durable campaign state.
- No source-scoped campaign object bounded the Drive and Gmail searches.
- The run needed a human-authored second turn to reserve time/context for the
  deliverable.

## Company pitch package

### Execution

- The skill loaded successfully in a clean new session.
- The first run expanded to 155 visible Drive evidence items and nineteen
  actions over roughly 58 seconds before manual cancellation.
- Granola failed with an internal task-group error.
- Both web-search attempts failed because the Tavily key was unavailable to the
  sidecar, despite the other connector preflight being healthy.
- Cancellation preserved all completed evidence and emitted an interrupted
  receipt.
- A second no-more-tools turn eventually produced the review package.
- Reloading the app restored the complete package and terminal state.

### Product result

The final partial package was useful and honest. It:

- stated immediately that no editable deck had been created because no
  slide-write capability exists;
- recovered a pitch script, historical company work, and prior relationship
  clues;
- produced a company brief, proposed value exchange, tiered ask,
  slide-by-slide content package, talking points, knowledge gaps, and follow-up
  email draft;
- marked unverified company facts as training knowledge after web search failed;
- surfaced conflicting project duration/pricing and an unconfirmed warm contact;
- made no file, draft, or send claim without a receipt.

### Failures

- Global Drive discovery produced an evidence flood instead of a narrow Zoox
  source set.
- The existing company deck lived behind a Figma link the local toolset could
  not read.
- The candidate Google Slides template was located but not opened, copied, or
  edited.
- There is no first-class editable deck artifact.
- The final answer required a second user turn and a long synthesis delay.
- Web and Granola connector readiness was not truthful at preflight.

## Durability and performance evidence

- Interrupted and completed turns survive app reload.
- Tool results and final answers survive the interruption/resume sequence.
- The outreach transcript produced roughly 1,900 persisted assistant-delta
  events; the pitch transcript produced roughly 3,300.
- Local event logs reached approximately 710 KB and 1.2 MB respectively for
  these two validation conversations.

Persisting every streamed delta gives excellent replay fidelity but is too
expensive as the long-term transcript representation for artifact-heavy work.

## Acceptance status

| Outcome | Baseline status |
|---|---|
| Outreach shortlist and review gate | Useful partial pass |
| Reviewed/enriched/sent campaign | Not attempted; blocked on director choices and later write approvals |
| Company brief and slide content package | Useful partial pass |
| Editable company deck | Fail: capability absent |
| Interrupt/reload recovery | Pass |
| Scheduled execution of the same skill | Deferred until interactive convergence works |
| First-class artifacts and durable workflow state | Fail |

## Immediate product lesson

Sourcecado does not primarily need more generic tools. It needs a skill-run
contract that bounds sources, reserves a synthesis phase, checkpoints useful
work, creates addressable artifacts, and can later be invoked unchanged from
the scheduler.
