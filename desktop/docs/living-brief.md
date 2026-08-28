# The living brief

Status: active-stack engineering reference for issue #70.

The living brief is one bounded projection of a person file. A successor should be able to read it and understand the relationship without opening raw tool JSON or reconstructing it from Gmail.

## One projection, two renderings

`coworker/brief.py` builds a `LivingBrief` from the person record, its timeline, and its attachments. `person_brief(people, person_id)` is the only entry point that reads the store.

Two functions render it. `brief_payload` produces the person-view JSON returned by `GET /v1/people/{id}`. `prompt_context` produces the `person_file` section of the system prompt for a person-bound chat. Neither renderer receives the person record or the timeline, so neither can state a fact the other does not have.

`prepare_context_projection` in `coworker/context_projection.py` is the boundary both renderings sit behind. It is not modified here; the brief consumes it.

## Claims and their state

Every claim is a `ProjectionItem`. Its `state` uses the projection's own vocabulary:

- `current` — backed by at least one source reference still inside the freshness window.
- `stale` — every backing source is older than `FRESH_FOR` (30 days).
- `conflicting` — the person record and a connector disagree on `email`, `title`, or `company`. The boundary requires at least two source references for this state.
- `missing` — a knowledge gap. This is the only state allowed to carry no source reference.

`truncated` and `sensitivity` are separate facets on the same item, so a shortened note is visibly shortened rather than silently cut.

## Source references and what they support

`brief_payload["source_refs"]` lists every source a selected claim points at. Each row carries `fresh` and an `evidence` value from `coworker/run_evidence.py`:

- `present` — read, or metadata read as intended.
- `partial` — a truncated read.
- `unsupported` — a body this build cannot extract.
- `missing` — a refresh that failed.

Two vocabularies, two questions. `ContextState` describes a claim. `Evidence` describes a source. Neither is redefined here.

## Isolation

Every claim carries the `person_id` of the row it was built from. `prepare_context_projection` raises on a scope mismatch, so an event or attachment belonging to another person cannot be rendered — it fails before any text exists, rather than being dropped by a filter a later refactor forgets.

Restricted source references never become a claim body. `PersonStore.get` withholds them and reports `restricted_source_count`; the brief turns that count into one `missing` claim. If a restricted item ever did reach the boundary, the boundary refuses it.

## Partial rather than empty

`person_brief(..., refresh=result)` takes the per-source result of a refresh. When a source fails, every claim that already existed is kept, `partial` is true, `partial_sources` names the source, and the source table gains a `missing` row for it. The brief never empties because one connector was unreachable.

Partiality is scoped to the refresh that reported it. A later `GET /v1/people/{id}` does not remember an earlier failure; it reports what the store holds.

## Bounds

The brief reads the newest `EVIDENCE_CAP` (12) timeline events. The projection then applies its own token budgets. Anything left out for either reason is counted in `omitted` and stated in both renderings, so a shortened brief never reads as a complete one.

## The successor handoff

Four fields: who this is, what we wanted, what happened, what they want.

`handoff_draft` returns the director's saved handoff when there is one, and otherwise a draft generated from the claims, naming the claim ids it was built from. `POST /v1/people/{id}/handoff` saves a reviewed handoff through the ordinary `PersonStore.patch`, so it takes an expected version, writes a receipt, and snapshots alongside the sources that existed when it was written. A revert restores the handoff and those sources together.
