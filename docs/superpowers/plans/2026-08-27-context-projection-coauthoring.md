# Bounded context projection v1 — Fisher approval packet

Date: 2026-08-27
Status: **UNAPPROVED PROPOSAL — not active model context**
Issue: #58
Stacked on: approved `sourcing-director-v1` activation / PR #95
Proposed policy version after approval: `context-projection-v1`

Nothing in this document or the inactive `coworker.context_projection` module is
loaded by the prompt runtime. The active `saved_memory`, `skill_catalog`, and
`person_file` sections remain unchanged until Fisher approves this complete
category contract.

## Current context inventory

### Global saved memory

`ConversationStore.memories` currently stores only `id`, free-form `content`, and
`created_at`. It has no category, person, session, source reference, updated time,
freshness, sensitivity, or conflict state.

Every normal model request calls `list_memories()` in ascending id order, joins
every row as `[#id] content`, then clips the combined string at 4,000 characters.
This is a bounded full-store dump, not retrieval: it favors the oldest rows,
can cut a row mid-content, and cannot distinguish an operator preference from a
fact about one Person. `MEMORY.md` is still generated as a local index but is not
the active prompt source after the `sourcing-director-v1` activation.

### Person File

`PersonStore` owns the durable Person, Target, Sequence state, outcome, timeline,
Artifacts, Knowledge Gaps, Source References, versions, and the exact
`session_people` binding. Restricted Source References are already omitted by
default unless the existing PersonStore grant allows them.

The active prompt reads only the bound Person, builds a Living Brief, and includes
up to the twelve newest timeline summaries. It does not include stable Source
References, timestamps, Person version, current Sequence state, outcome,
attachment conflict state, or explicit freshness. It never queries another
Person, which is the isolation invariant to preserve.

### Session-local state

The current session's entire persisted message history is sent to the provider
separately from the system prompt. There is no bounded, typed working-state
projection for the current request, director corrections, pending decisions,
completed Artifacts, or immediate next step. Active desktop turns do not yet use
the eval harness's compaction implementation.

Queue records, approvals, run events, and tool receipts are durable but are not
classified into session-local context.

### Prompt and tool identity

The active prompt is `sourcing-director-v1`. Static prompt order and budgets are
deterministic. Dynamic prompt order is currently saved memory, skill catalog,
then Person File. Content-free prompt diagnostics record the prompt version,
ordered sections, sizes, and hash. Effective tools are assembled per app/session
but do not yet participate in a prepared context identity.

## Recommended category contract

Exactly four categories may enter `context-projection-v1`. `legacy_unclassified`
is a migration state, not a fifth context category.

### 1. Global operator preference — `operator_preference`

Durable, person-independent working preferences explicitly stated by the
Sourcing Director: preferred draft tone or length, stable formatting choices,
and recurring personal workflow defaults.

Rules:

- Must originate from an explicit director statement and carry a stable
  Sourcecado memory Source Reference.
- Must not contain Person facts, Target facts, connector evidence, Sequence
  state, permissions, or product policy.
- Has no `person_id` or `session_id`.
- Does not become higher authority than Sourcecado policy; it remains untrusted
  evidence under the approved prompt.
- Does not expire by age. It becomes stale only when the director updates,
  supersedes, or removes it.

Examples that belong: “Prefer outreach drafts under 140 words.”
Examples that do not: “Ada works at Analytic,” “send without asking,” or “this
session is preparing Ada's draft.”

### 2. Sequence state — `sequence_state`

The canonical current operating state for the bound Person: Person id/version,
Target, Sequence state (`Open`, `In conversation`, or `Done`), recorded Outreach
Outcome, and the version-checked state needed to continue safely.

Rules:

- Comes only from the latest visible PersonStore record, never from a memory row
  or connector claim.
- Requires the exact bound `person_id`; an unbound session has no Sequence item.
- Uses a stable Source Reference such as `person:<id>@version:<n>`.
- The latest Person version is current; earlier versions are stale.
- A connector disagreement is Person evidence, not permission to overwrite the
  canonical Sequence state.

### 3. Person evidence — `person_evidence`

Visible, sourced claims and Knowledge Gaps attached to the bound Person: identity
and company context, Apollo/Gmail/Drive/Calendar/web/Granola evidence, handoff
facts, Artifacts, Source References, and Outreach Outcomes that help the current
run.

Rules:

- Candidate collection begins from exactly one bound Person File. Unrelated
  People are excluded before ranking, truncation, or summarization.
- Every current or stale claim has at least one stable Source Reference.
- Every conflict has at least two Source References and retains the competing
  visible claims; projection never silently chooses a winner.
- A missing item names a real required field or Knowledge Gap and may have no
  source; it never invents a placeholder value.
- Restricted records must already have been removed by the existing PersonStore
  visibility contract. If a restricted item reaches projection anyway,
  preparation fails closed. This proposal adds no grant hierarchy.
- Raw connector payloads, credentials, authorization material, and hidden
  reasoning never become projection text.

### 4. Session-local working context — `session_working`

Bounded state needed to continue this session: the current director request,
director corrections and decisions, pending approval/task ids, relevant completed
Artifact/tool receipt ids, immediate next step, and unresolved work.

Rules:

- Requires the exact `session_id`. When person-scoped, its `person_id` must also
  match the projection binding.
- Uses stable session message, event, approval, or run Source References.
- Is not durable operator preference or Person evidence merely because it was
  said in chat. Promotion requires the existing explicit memory or Board write.
- Resolved transient items drop from the next projection rather than becoming
  stale clutter.
- The full transcript remains canonical history until compaction; this category
  is the small mechanical working set, not a second transcript summary.

## Source Reference and evidence-state contract

Every Source Reference has:

- stable `id`;
- `provider` (`sourcecado`, `apollo`, `gmail`, `drive`, `calendar`, `web`, or
  `granola`);
- inspectable `locator` that contains no credential;
- `observed_at`;
- source `modified_at` when available;
- `fresh_until` when the source or Sourcecado freshness policy supplies one.

Every projected item has a stable item id, optional `claim_key`, category,
bounded excerpt, token count, authority, `updated_at`, sensitivity, Source
References, and exactly one evidence state:

- `current` — latest visible, unsuperseded evidence and not past `fresh_until`.
  Current means “best current evidence,” not guaranteed truth.
- `stale` — explicitly superseded/deleted, from an earlier Person version, or
  past its freshness window. It remains labeled; it never masquerades as current.
- `conflicting` — two or more visible live sources disagree on one `claim_key`.
  Preserve the competing claims and references together.
- `missing` — a required field or named Knowledge Gap is absent. State the gap,
  never a guessed value.

Freshness defaults:

- Director preferences: no time expiry; explicit supersession only.
- Sequence state: version-based; latest Person version only.
- Historical mail, meetings, sends, outcomes, and completed Artifacts: no time
  expiry, but deleted/superseded sources become stale.
- Mutable profile claims such as current role, title, company, or public contact
  availability: stale after 90 days unless the source provides an earlier
  `fresh_until` or newer evidence revalidates it.
- Session working items: current while unresolved in this exact session; removed
  after resolution.

## Projection identity and reuse

A prepared projection is bound to all six fields:

1. persona id;
2. session id;
3. bound Person id, or explicit `null` for an unbound session;
4. current Target, or explicit `null`;
5. prompt version;
6. SHA-256 of the ordered effective tool names and schemas.

The projection content hash additionally covers the policy version, ordered item
ids, item content hashes, states, and Source References. Reuse for a different
persona, session, Person, Target, prompt version, or effective tool catalog raises
a binding error before any model request.

Diagnostics may record identity fields already present in the run, policy
version, selected item ids, category token/count totals, omission counts, and
binding/content hashes. Diagnostics never contain excerpts, source bodies,
preferences, Person facts, or session text.

## Token budget and deterministic selection

`context-projection-v1` has a fixed **2,048-token** budget with no borrowing
between categories:

| Order | Category | Category cap | Per-item cap |
|---:|---|---:|---:|
| 1 | Global operator preference | 256 | 64 |
| 2 | Sequence state | 256 | 256 |
| 3 | Person evidence | 1,024 | 160 |
| 4 | Session-local working context | 512 | 128 |

At activation, token accounting uses one versioned, provider-independent,
conservative counter: `ceil(len(rendered_utf8_bytes) / 3)`. It is an explicit
budget unit, not a claim about provider billing tokens. The existing approved
6,000-character combined memory/Person dynamic envelope remains a second hard
cap; whichever cap is reached first wins. Skill catalog and static prompt budgets
remain unchanged.

Eligibility is checked before ranking. A scope or restricted-source violation
fails the entire preparation rather than quietly leaking or hiding an adapter
bug. Within each category, sort by:

1. evidence state: `conflicting`, `missing`, `current`, `stale`;
2. authority: director, Sourcecado record, direct connector, derived summary;
3. `updated_at`, newest first;
4. stable item id, ascending.

The adapter clips each excerpt at a deterministic token/word boundary to the
per-item cap and marks it truncated. The projector then selects whole items in
rank order until the category cap or overall character cap is reached. It never
cuts a Source Reference or half an item. Unused category budget stays unused so
an oversized evidence category cannot unpredictably consume preferences or
working state.

## Safe migration default

Add category/scope/source metadata to the memory store only during activation.
Every existing memory row migrates to `legacy_unclassified` with
`classification_status = needs_review`.

Recommended migration rules:

- Do not infer categories from free-form text.
- Do not inject `legacy_unclassified` rows into normal model context.
- Let the director classify a true global preference, move a Person fact through
  the existing Board contract, keep session-only state in its session, or delete
  obsolete content.
- Preserve existing ids, timestamps, Markdown files, and audit history.
- New `remember` writes become global preferences only when the director
  explicitly asks for a person-independent preference to persist. Ambiguous
  writes remain `needs_review`; Person facts route to the Person File.

This temporarily withholds ambiguous legacy rows but prevents existing Person
facts from silently becoming global after migration.

## Compaction reuse contract

Compaction consumes the same prepared projection; it does not build a second
Person summary.

1. Prepare and bind the projection from canonical stores before the model request.
2. Compaction may replace only an older, complete transcript span with a bounded
   session summary while preserving atomic tool-call groups.
3. Reattach the unchanged prepared projection after the compaction summary and
   revalidate all six identity fields.
4. Person evidence, Sequence state, preferences, and Source References remain in
   the projection rather than being rewritten by the summarizer.
5. The compaction summary cannot promote text into memory, update a Person File,
   change an evidence state, or manufacture a Source Reference.
6. On the next model request, rebuild the projection if the Person version,
   Target, prompt version, effective tools, or canonical source state changed.

This follows the useful donor pattern of compacting only the outbound transcript
view while leaving canonical history untouched, but the identity and category
contract remains Sourcecado-owned.

## One Fisher decision

Recommended approval answer:

> **Approve `context-projection-v1` as written: the four exact categories and
> scope rules; current/stale/conflicting/missing evidence states; 90-day mutable
> profile freshness default; stable Source References; fail-closed restricted and
> cross-person handling; the 2,048-token no-borrow budget plus existing 6,000-char
> cap; deterministic ranking/truncation; `legacy_unclassified` migration; exact
> six-field identity; and reuse of the same projection through compaction.**

Approval is all-or-revise for this packet because category, migration, budget,
and compaction rules interact. No category behavior becomes active before this
answer is recorded.

## Exact activation step after approval

1. Add the approved category/migration columns and migrate every current memory
   row to `legacy_unclassified` without injecting it.
2. Build store adapters that emit already-scoped `ProjectionItem` values from
   global preferences, the exact bound PersonStore record/events/visible
   attachments, and current session records.
3. Add the conservative token counter, bounded excerpt renderer, conflict grouping,
   and freshness classification with RED-first migration/cross-person/restart/
   stale/conflict/oversize/missing tests.
4. Replace active `saved_memory` plus `person_file` assembly with one
   `context_projection` dynamic section while preserving the approved skill and
   total prompt caps.
5. Record content-free projection diagnostics on the run and pass the exact
   prepared projection into active compaction.
6. Add end-to-end tests proving another session/Person cannot reuse it and normal
   model requests no longer receive the full memory store.
