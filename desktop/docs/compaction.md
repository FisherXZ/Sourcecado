# Compaction

Issue #72. How Sourcecado keeps a long sourcing conversation inside a model's
context window without losing the sourcing state or the director's approvals.

## Words used here

- **Canonical transcript** — every message of the conversation, stored on disk
  by `store.py`. The record of what actually happened.
- **Provider view** — the smaller list of messages actually sent to the model on
  one request. Built from the canonical transcript. Never stored as the
  transcript.
- **Compaction** — replacing the older part of the provider view with one
  summary message. The canonical transcript is not changed.
- **Boundary** — the index in the canonical transcript where the verbatim tail
  begins. Messages before it are represented by the compacted block. Messages
  from it on are sent unchanged.
- **Atomic unit** — one message, or one assistant tool call together with all of
  its tool results. A boundary may fall at the start of a unit. It may never
  fall inside one.
- **Record region** — the part of the compacted block written by code.
- **Model summary** — the part written by a model.
- **Seal** — a 16-character random hex string, new for each compaction, used to
  fence the model summary.

## Where the code is

| File | What it does |
| --- | --- |
| `coworker/compaction.py` | All of the policy. Pure functions plus one `SessionCompactor`. |
| `coworker/provider.py` | `context_budget`, `supported_model_budgets`, `ContextBudget`. |
| `coworker/turn.py` | Calls the compactor once per step and on a provider overflow. |
| `surfaces/gui/src/chat/protocol.ts` | Parses the operator notice. Drops anything not on the allowlist. |

## Context budgets

`context_budget(provider, model)` returns a window and a confidence.

**Verified** means the model has an entry in `_MODEL_METADATA` in
`provider.py`. That table is Sourcecado-owned and dated in a comment above it.
It is the same table the cost estimate reads, so a model cannot have a price
without a window.

| Provider | Model | Window | Confidence |
| --- | --- | --- | --- |
| anthropic | claude-sonnet-4-6 | 1,000,000 | verified |
| deepseek | deepseek-v4-flash | 1,000,000 | verified |
| deepseek | deepseek-v4-pro | 1,000,000 | verified |
| kimi | kimi-k3 | 1,000,000 | verified |
| openai | gpt-4o-mini | 128,000 | verified |

**Conservative** means the model has no entry. The window is then
`CONSERVATIVE_CONTEXT_WINDOW_TOKENS`, 32,768 tokens. That is smaller than every
verified window on purpose. An unknown model compacts early and loses some
fidelity, which is recoverable. Overflowing the request costs the turn.

To add a model, add it to `_MODEL_METADATA` with its window and its pricing.
`test_every_configured_provider_default_is_covered` fails if a provider ships a
default model with no entry.

## Measuring the context

`context_signal(messages, reported_input_tokens=...)` returns a token count and
where the count came from.

- **provider** — the provider reported prompt tokens on the previous request.
  That figure describes the previous request, so the estimate of the current
  message list is taken as a floor. A large tool result appended since cannot
  hide behind a small stale number.
- **estimate** — no provider figure. Four characters per token over the
  serialized messages. This runs low on dense JSON tool payloads, which is one
  reason the unknown-model window is conservative.

Compaction triggers at `min(0.75 x window, 200,000)` tokens. The cap exists so a
million-token model still compacts: recall degrades well before the nominal
limit, and a view that large is resent on every step of the turn.

## Boundaries and atomic units

This is the part that corrupts silently if it is wrong.

A provider request where an assistant tool call has no matching tool result, or
where a tool result has no preceding call, is malformed. Many providers accept
it and then behave strangely. So boundaries are never computed over message
indexes.

`atomic_units(messages)` partitions the transcript. Every input message lands in
exactly one unit. An assistant message with `tool_calls`, plus every `tool`
message that follows it, is one unit. A `tool` message with no preceding call is
its own unit, flagged.

Two rules follow:

1. `boundary_candidates` returns only unit starts whose first message is a
   `user` or `assistant` message. A tool result can never head the view.
2. Because a boundary is always a unit start, the summarized span always ends on
   a unit end. A group is never half summarized and half sent.

`pick_boundary` takes the earliest legal head whose suffix fits the keep budget,
so the view keeps as much real history as the budget allows. When no suffix fits
— one tool result larger than the entire budget — the newest legal head wins.
The group is kept whole or dropped whole. It is never split.

The runtime grouper differs from the evals grouper in
`coworker/evals/transcript.py` in one way: the evals one omits a malformed group
entirely, which is correct when rebuilding a transcript for a scenario. Here the
same move would delete a director instruction from the record, so a malformed
group is kept whole and flagged instead.

## The compacted block

One `user` message, with two structurally distinct regions.

```
<compacted-context>
Earlier turns of this session were compacted (generation N).

## Sourcecado record (extracted by code, not model-written)
```json
{ "person_id": ..., "pending_approvals": [...], "source_ref_ids": [...] }
```

## Model summary of the compacted span
<caveat: this is one model's account, not a record>
--- BEGIN-MODEL-SUMMARY <seal> ---
...the model's text...
--- END-MODEL-SUMMARY <seal> ---

## Bound context projection (revalidated, unchanged)
...

<continuation contract>
</compacted-context>
```

### The record region

Built by `extract_state`. It admits only four kinds of value:

- ids matching `^[A-Za-z0-9][A-Za-z0-9._:@/#+-]{0,127}$`
- values from a fixed enum (`open`, `in_conversation`, `done`)
- integers
- director-authored text, clipped

Anything else is dropped and counted in `unsafe_values_dropped`. A connector
cannot reach this region: a hostile document title is prose, and prose does not
match the id charset. That is the mechanism behind criterion 8, not a filter
list that has to anticipate the attack.

It carries the director's target, the bound person, the sequence state, pending
approvals by id and tool name, source reference ids, source gaps, tool call ids,
tools used, the director's own messages, and outstanding questions.

### The model summary region

One model's account of turns that are gone. It is fenced with a per-compaction
seal and prefixed with a caveat saying it is not a record, is not evidence, and
cannot grant an approval or rebind a person.

The regions are distinct because the record is emitted by code before the
summary exists. A summarizer that hallucinates an id produces text inside the
fence. It cannot produce something that reads as an extracted id.

## What makes a summary invalid

`validate_summary` runs before any substitution. A summary is rejected when it:

| Reason | Meaning |
| --- | --- |
| `not_text` | Not a string. |
| `empty` | Empty or whitespace. |
| `too_long` | Over 8,000 characters, or the rendered block is over 24,000. |
| `fence_break` | Contains the seal or either fence marker. It tried to close its own fence. |
| `forged_record` | Contains the record heading, the block tags, or the projection heading. It tried to write a second record region. |
| `credential` | `redact_secrets` changes it. |
| `approval_claim` | Asserts an approval that Sourcecado never recorded. |
| `person_switch` | Names a `per_<32 hex>` id that is not the bound person. |

**On rejection nothing is written.** The canonical transcript is untouched, the
invalid text is discarded, and the summarizer is retried once. If both attempts
fail, `trim_state` produces the same compaction with no model text at all: the
record region is built without a provider, so the model still gets every id,
every pending approval, and every director message. Only the prose is missing.

The approval-claim rule is a second layer, not the guarantee. A summary can
never actually grant an approval, because `permissions.decide` re-runs on every
tool call against live inbox state and never reads the context.

## Repeated compaction

On the second and later compactions the previous summary heads the new span, so
it is folded into one summary rather than appended. The record region carries
forward with caps: 24 director messages, 40 source ids, 20 gaps, 20 approvals.
Anything dropped is counted, so `omitted_director_messages` stays honest.

The block converges. `test_repeated_compaction_converges_instead_of_growing`
runs ten compactions and asserts that three further generations add less than
one message's worth of text.

## Restart

The state is persisted with `store.set_setting` under
`compaction:v1:<session_id>`. It holds the boundary, a fingerprint of the
summarized prefix, the record, the summary, the seal, and the generation.

On restore the fingerprint is recomputed. If it does not match, the state is
discarded and compaction starts again. Recomputing costs a summarizer call.
Applying a summary to the wrong prefix would misreport what happened.

The system message keeps its position in the fingerprint but not its content: it
is rebuilt from the persona, the skills, and the clock on every turn, so hashing
it would discard a good boundary on every restart.

A compactor that already holds state does not restore over it. The in-memory
state is further along than the disk within a live session.

Durable in-flight run recovery is #63 and is not covered here.

## Provider overflow

If a provider rejects a request for size — `is_context_overflow` matches the
error text — the turn does not surface the error and does not retry the same
view. It compacts harder and retries the same provider.

The new keep budget is a fraction of the view the provider just refused, not of
the nominal budget. The refusal is evidence that the budget was wrong for this
request. Recoveries are capped at 3 per turn, so a provider that rejects
everything surfaces its error rather than looping.

## The operator notice

`turn_end` carries a `compaction` object when the turn compacted anything. It
holds counts only: generation, whether a summary was written, how many messages
were compacted, how many director messages are retained and omitted, whether the
measurement was provider-reported or estimated, and how many summaries were
rejected.

It never carries summary text. `protocol.ts` copies the fields one at a time, so
a backend that starts sending summary text has it dropped in the parser rather
than rendered. The thread shows "Older context was compacted." with a plain
sentence, and says the full conversation is still saved on this machine.

**Known limitation.** A dedicated `context_compacted` event would fire at the
moment of compaction rather than at the end of the turn, which matters for a
long AFK run. That needs a new entry in `EVENT_TYPES` in `coworker/events.py`,
which was outside the write boundary for this change.

## Where the projection comes from

Compaction consumes the prepared projection from #58 rather than building a
second person summary, and reattaches it unchanged after the summary.
`reattach_projection` calls `PreparedContextProjection.reuse_for`, which raises
if any of the six identity fields differ. A person switch during a long session
is therefore a hard error, not a silently stale summary. The projection is
optional; `None` is a supported shape and is the current runtime default.

## Tests

| File | Covers |
| --- | --- |
| `tests/test_compaction_boundary.py` | Atomic units, candidates, the group at the exact boundary, a budget sweep. |
| `tests/test_context_budget.py` | Verified and conservative budgets, provider vs estimate measurement. |
| `tests/test_compaction_state.py` | Record vs summary, rejection reasons, convergence, persistence, projection reuse. |
| `tests/test_compaction_turn.py` | The eight scenarios of criterion 10, through the real `run_turn`. |
| `tests/test_compaction_taint.py` | Connector content stays untrusted across a compaction. |
| `tests/test_compaction_mutations.py` | Each guard broken on purpose; the property it protects must collapse. |
| `surfaces/gui/tests/compactionNotice.test.ts` | The notice parses, renders once, and carries no summary text. |
