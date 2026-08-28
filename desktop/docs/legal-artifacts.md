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
- **approval** -- a caller-supplied record (`approved_by`, `approved_at`, `authorized`). Missing entirely is `missing`; `authorized` must be the boolean `true` (the string `"false"` is `absent`, not authorized); `approved_at` and the artifact's `modified_time` must both be real ISO-8601 timestamps so freshness can be compared (unparseable `approved_at` or omitted `modified_time` is `missing`); recorded before the artifact's last body revision is `expired` (stale approval).

Any facet short of `present` blocks `ready_to_use` and produces a reason like `parties:expected_party_not_named:...` or `approval:approval_superseded_by_later_revision`.

## Knowledge gaps

`knowledge_gap_fields()` turns a not-ready assessment into the same shape as the codebase's other knowledge gaps (see `PersonStore.record_reply_gap`): a `kind`, the worst `evidence` value across facets, the specific reasons, and a question for a human -- never the artifact's own legal language. `attach_gap()` files that onto a person via the existing `PersonStore.upsert_attachment(record_type="knowledge_gap", ...)` mechanism, keyed on artifact id and revision so re-classifying an unchanged artifact does not duplicate the gap.

## What this module does not do

It does not decide who counts as an authorized approver (`approval["authorized"]` is a caller-declared fact), and it does not author or judge legal language -- only structural placeholders (`[COUNTERPARTY NAME]`, `[EFFECTIVE DATE]`) and generic markers. Recording real approval, and authoring the canonical Berkeley Codeology template text, are human/counsel actions outside this module's scope.

## Where it runs

`coworker/drive.py`'s `_legal_source_safety` is the live call site. A Drive read that looks like a legal document (by filename or by the opening of its body) is classified, and the assessment is returned as the read result's `source_safety` field, alongside `legal_document: True`. That field is the same one `drive_evidence.normalize()` and `drive_ingestion` already read to mark a source `sensitive`, so it now carries facet evidence instead of a two-string name match.

A Drive read declares nothing. It has no lifecycle status, no approval record, and no expected counterparty, so `status` resolves to `unverified` and `ready_to_use` is `False` for every legal document read from Drive. The facets still vary per document: which parties the body names, whether it carries a date, whether it states a term. The body is untrusted external text and never gets to declare its own standing.

## The title as a last-resort expectation

`_verify_parties` normally checks the body's parties against a counterparty the caller declared. A connector read declares none, and with no expectation at all the facet had one reachable value: a stale NDA graded the same as a clean template, which is the failure this module exists to catch.

So when `expected_party` is empty, the title supplies the expectation. Title boilerplate is stripped first (`nda`, `agreement`, `template`, and the rest), leaving name-bearing tokens: "Codeology NDA Template" reduces to `codeology`. A body naming nobody the title names reads `absent`. A body naming the title's organization plus someone else reads `partial`. A body that agrees reads `present`.

The title can only expose a disagreement. It can never grant anything: `classify` withholds `ready_to_use` from any artifact whose expectation came from its own filename, and records `parties:expectation_taken_from_title_not_declared` when that is the only thing left blocking. Otherwise a well-named file would be a way to earn readiness.

## Where the gap gets filed

`coworker/drive_evidence.py`'s `attach()` files it. That is the one Drive path that already knows which person a source belongs to, which is what a knowledge gap needs. Attaching a source whose assessment is not ready writes the gap as a second attachment on the same person; the operator still gets the source reference they asked for. Both writes are keyed idempotently, so re-attaching the same revision adds neither. A `source_safety` payload with no `facets` -- a row stored before this shape existed -- is skipped rather than crashed on.

`attach_gap(people, person_id, result["source_safety"], actor=...)` remains callable directly by any other path that has a person in hand.

## Still to do

Nothing in the product records a declared status, an approval, or an expected counterparty for a Drive file yet, so no read can reach `ready_to_use`. Recording those is human work (see issue #39). `coworker/drive_ingestion.py`'s bulk folder job does not file gaps, because it is not person-scoped and there is no person to file against.
