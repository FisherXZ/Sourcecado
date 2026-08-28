# Legal artifact source safety

Status: active-stack engineering reference for issue #39.

Sourcecado treats a legal document as evidence, not a ready-to-use agreement, until every named party, date, term, and approval fact about it is verified. `coworker/legal_artifacts.py` is the classifier: it reads a document's own body for parties, dates, and terms, and checks a caller-declared approval record for freshness -- it never trusts a filename or unverified metadata for any of that.

## Lifecycle status

Every legal artifact carries a declared `status`: `draft`, `approved_template`, `executed`, or `stale`. Status is always a caller-supplied fact; the classifier never promotes a document to `approved_template` by pattern-matching its wording. A status the caller didn't declare, or declared with an unrecognized value, resolves to `unverified` -- deliberately distinct from `draft`, so an unplaced document reads as unsafe rather than as merely in progress.

`ready_to_use` can only be `True` when `status` is `approved_template` **and** every verification facet reads `present`. Draft, executed, stale, and unverified artifacts are never `ready_to_use`, no matter how clean the body reads -- this is what keeps `stale` from being confused with `approved_template`.

## Verification facets

Four facets are checked per `classify()` call, each landing on one word from a shared evidence vocabulary (`present`, `absent`, `partial`, `missing`, `expired` -- the same words `coworker/run_evidence.py` uses for run records, kept as a separate type here since that module's semantics are specific to agent runs):

- **parties** -- extracted from the body's own "between X and Y" language, not the filename. The expected party missing entirely is `absent`; an extra unplaceholdered party alongside the expected one is `partial`; no party language at all is `missing`.
- **dates** -- a real date or an explicit placeholder token (`[EFFECTIVE DATE]`) in the body; otherwise `missing`.
- **terms** -- a term/duration marker or placeholder in the body; otherwise `missing`.
- **approval** -- a caller-supplied record (`approved_by`, `approved_at`, `authorized`). Missing entirely is `missing`; recorded by someone not marked `authorized` is `absent`; recorded before the artifact's last body revision is `expired` (stale approval).

Any facet short of `present` blocks `ready_to_use` and produces a reason like `parties:expected_party_not_named:...` or `approval:approval_superseded_by_later_revision`.

## Knowledge gaps

`knowledge_gap_fields()` turns a not-ready assessment into the same shape as the codebase's other knowledge gaps (see `PersonStore.record_reply_gap`): a `kind`, the worst `evidence` value across facets, the specific reasons, and a question for a human -- never the artifact's own legal language. `attach_gap()` files that onto a person via the existing `PersonStore.upsert_attachment(record_type="knowledge_gap", ...)` mechanism, keyed on artifact id and revision so re-classifying an unchanged artifact does not duplicate the gap.

## What this module does not do

It does not decide who counts as an authorized approver (`approval["authorized"]` is a caller-declared fact), and it does not author or judge legal language -- only structural placeholders (`[COUNTERPARTY NAME]`, `[EFFECTIVE DATE]`) and generic markers. Recording real approval, and authoring the canonical Berkeley Codeology template text, are human/counsel actions outside this module's scope.

It is not yet wired into any live path -- `coworker/drive.py`'s existing `_legal_source_safety` hotfix and `coworker/drive_evidence.py`'s `normalize()` are unchanged. Wiring `classify()` into the Drive-read and tool-call path is follow-up integration work.
