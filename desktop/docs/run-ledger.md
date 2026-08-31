# The unified run ledger: one receipt per Agent Run

Status: active-stack engineering reference. Covers the read side of the durable
run store — the receipt projection, the query surface, and the retention bound.
Writing runs is `docs/agent-runs.md`; this document assumes it.

Not to be confused with `coworker/ledger.py`, which maps tool results into
person-file events. That is the person timeline. This is the run receipt.
Nothing in this document changes it.

Four modules, in dependency order:

| Module | Holds |
| --- | --- |
| `coworker/run_evidence.py` | The evidence vocabulary and the record analysis behind it. |
| `coworker/run_receipt.py` | The receipt projection. Pure: no store, no clock, no network. |
| `coworker/run_ledger.py` | The reader over the run store, the query, and retention. |
| `coworker/run_ledger_api.py` | The HTTP surface. No write verb. |

## What a receipt is

An Agent Run already has one identity for chat, queued, and scheduled work. A
receipt is that identity rendered as operational evidence: what ran, which
sources and artifacts it touched, which permissions it asked for, how recovery
behaved, which model attempts it used, and how it ended.

A receipt is not a transcript and not chain-of-thought. The rule that keeps it
from becoming one is mechanical rather than aspirational:

> A receipt renders only fields `coworker/run_receipt.py` names, and every named
> field is an identifier, an enum, a count, a timestamp, or a bounded reason the
> runtime itself wrote.

Message bodies, tool arguments, command output, prompts, raw provider errors,
credentials, and model reasoning have no field to land in. `_pick` copies named
scalars and never a nested structure, so a payload cannot ride out whole. This
is a second allowlist, independent of the write-time allowlist in
`agent_runs.CHECKPOINT_PAYLOAD_FIELDS`. Both must hold on their own: a read side
that leaned on the write side would keep passing its tests after the write side
changed.

Free text appears in exactly three places, all of them bounded and already
passed through `redact_secrets` when they were stored: `reason`,
`error_summary`, and reference titles. The ledger adds no free-text field of its
own. "Rationale summaries" in a receipt are those bounded reasons — the
assistant's prose stays in the transcript and the person file, and the receipt
points at the run rather than copying it.

## Evidence, never inference

Silence is not absence. Every section of a receipt carries an `evidence` value
from one closed vocabulary, and the two that matter most are the two that are
easiest to collapse.

| Value | Means |
| --- | --- |
| `present` | The record says so directly. |
| `absent` | It positively did not happen. |
| `partial` | Some of it settled and some did not. |
| `missing` | We do not know. The record has a hole, or the run is still open. |
| `ambiguous` | The run knows it does not know: an external effect never reported back. |
| `unsupported` | This build cannot express or verify the fact. Never "it did not happen". |
| `expired` | The evidence existed and retention aged it out on purpose. |

"No approval was requested" is `absent`. "We do not know whether an approval was
requested" is `missing`. They are different operator situations and they must
never render the same way.

The rule that separates them is one property of the record, computed before any
section is read. A run's checkpoint sequence is dense from 1 to
`checkpoint_sequence`, so a receipt can tell whether the record covers the run's
whole life. A facet with no evidence is `absent` only when all of the following
hold:

- the run is terminal, and
- the stored checkpoints are contiguous and end at `checkpoint_sequence`, and
- no checkpoint kind is unknown to this build, and
- no checkpoint is in `INCOMPLETE_RECORD_KINDS` — `process_interrupted` or
  `tool_outcome_unknown`, each of which means work happened that no checkpoint
  describes.

Otherwise the facet is `unsupported`, `missing`, or `expired`, in that order of
precedence. An unreadable record withholds a conclusion even where the receipt
does have entries to show: the entries stay visible, but the section reads
`unsupported`, because a checkpoint this build cannot interpret might have been
another tool call, another approval, or another model attempt.

Sections backed by the run row — sources, artifacts, usage, approvals, outcome —
are judged slightly differently from sections backed by checkpoints. The row is
written in the same transaction as every checkpoint, so pruning step detail does
not weaken what the row carries. Pruning turns a checkpoint-backed section
`expired`; it leaves a row-backed section alone.

## A ledger row is not authority

Criterion 7 is a safety property, not a convention. The run store records which
approval ids a run touched. It never records whether the owner said yes.

Every decision in a receipt is resolved against the owner-native approval
receipt — the Inbox item in `ConversationStore`. An approval id that store does
not know renders `missing`, never an allowance. A pending decision renders
`partial`. An expired one renders `expired` and carries no decision. Only a
`resolved` owner-native item with an `allow` or `deny` decision produces a
decision field at all, and an authorized action whose execution was interrupted
renders `ambiguous`, because the outcome is genuinely unknown.

Three structures hold this up:

1. `RunLedger` reads the run store. Its only write is `prune_checkpoints`, which
   deletes checkpoint rows and touches `agent_runs` never. It holds no lease and
   cannot acquire one.
2. `run_ledger_api` has no write verb. There is no route that creates a run,
   changes a state, or records a decision.
3. `_approval_entry` sources `decision`, `state`, and `actor` only from the
   owner-native record. A `status` field in the run store's own checkpoint
   payload can never become the decision.

## Retention

`CHECKPOINT_RETENTION_DAYS` bounds step detail. `RunLedger.enforce_retention`
deletes the checkpoints of runs that finished before the cutoff. It never
deletes a run row, so the person, session, source references, artifact
references, approval ids, usage, and outcome that other records point at all
survive; a pruned run stays inspectable and stays linkable.

Two kinds of run are skipped. A run whose record marks a hole keeps its detail,
because that detail is the only evidence that evidence is missing — pruning it
would silently turn `missing` into `absent`. A run carrying a checkpoint kind
this build cannot read is also skipped, for the same reason.

A pruned run is recognised without a schema column. Retention deletes a prefix
of a dense sequence, so surviving checkpoints that are contiguous and end at
`checkpoint_sequence` but start above 1 are pruned evidence, and no stored
checkpoints at all with a non-zero `checkpoint_sequence` means the whole prefix
went. Any other deviation is a damaged record, which reads `missing`.

## Query

`GET /v1/agent-runs/{run_id}` returns one receipt. `GET /v1/agent-runs` returns
a bounded page of pointers — identity, state, timestamps, counts, outcome status
— cheap enough to list and carrying no evidence of its own.

The filters are the four entry points an operator arrives from:

| Entry point | Filter |
| --- | --- |
| Chat activity | `session_id` |
| A scheduled receipt | `session_id` (the `sched-{id}` session) or `trigger=scheduled` |
| A person timeline | `person_id` |
| A diagnostic result | `run_id`, or the receipt route directly |

Filters are re-checked on the projection, so an exact `run_id` cannot bypass a
`person_id` filter. `limit` is clamped to `MAX_QUERY_LIMIT`. An unknown status
or trigger is a 400, not a silently wider result — refusing is honest where
ignoring would infer.

## Known gaps

- Nothing calls `enforce_retention` yet. It is a library call on purpose: the
  process that starts the backend owns startup work, and that wiring is a
  separate lane.
- `create_app` now opens an `AgentRunRepository` so the ledger has something to
  read. It deliberately does not register an owner or reclaim leases, which is
  the write-side startup work `docs/agent-runs.md` describes. That document's
  claim that nothing constructs a repository in the running application is now
  half stale, and belongs to the lane that owns it.
- A run whose record marks a hole is never pruned, so its checkpoints are
  unbounded. Those runs are rare, and the alternative loses the distinction
  between `missing` and `absent`.
- `redact_secrets` matches credential shapes and assignments. A bare
  high-entropy secret pasted into a source title is stored, and rendered, as
  written. That is inherited from the write path, not introduced here.
- The receipt exposes `goal_fingerprint`, a SHA-256 of the operator's words. It
  is local-only and already on the run row, but it is guessable for short goals.
