"""Legal artifact source-safety classification.

Sourcecado treats a legal document as evidence, not a ready-to-use
instrument, until every named party, date, term, and approval fact about it
is verified from the artifact's own body and its declared approval record --
never from its filename or from caller-supplied metadata alone. This is
what closes issue #39: a stale NDA whose *filename* says one thing and whose
*body* names different parties must never be classified `ready_to_use`.

An artifact's lifecycle `status` (draft, approved_template, executed,
stale) is always a caller-declared fact -- this module never promotes a
document to `approved_template` by pattern-matching its wording. A status
this module cannot establish is `unverified`, not `draft`: defaulting to a
friendlier-sounding state would let an unknown document drift toward
`approved_template` by omission. Only a document that is both declared
`approved_template` *and* clears every verification facet earns
`ready_to_use`.

This module has no Drive, HTTP, or PersonStore dependency of its own beyond
`attach_gap`'s thin use of `PersonStore.upsert_attachment`. A caller (Drive
read, meeting evidence, or a future ingestion path) hands in the text it
already extracted plus whatever approval record it has, and gets back a
verdict plus a knowledge-gap payload shaped like the codebase's other
knowledge gaps (see `PersonStore.record_reply_gap`).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from coworker.people import PersonStore


class ArtifactStatus(StrEnum):
    """Declared lifecycle stage of a legal artifact."""

    DRAFT = "draft"
    APPROVED_TEMPLATE = "approved_template"
    EXECUTED = "executed"
    STALE = "stale"
    # Undeclared or unrecognized. Deliberately distinct from DRAFT: an
    # artifact this module cannot place on the lifecycle is not "in
    # progress", it is unsafe to offer at all.
    UNVERIFIED = "unverified"


_DECLARABLE_STATUSES = frozenset(
    {
        ArtifactStatus.DRAFT,
        ArtifactStatus.APPROVED_TEMPLATE,
        ArtifactStatus.EXECUTED,
        ArtifactStatus.STALE,
    }
)


def resolve_status(raw_status: str | None) -> ArtifactStatus:
    """An undeclared or unrecognized status resolves to `unverified`."""
    candidate = str(raw_status or "").strip().lower()
    for status in _DECLARABLE_STATUSES:
        if candidate == status.value:
            return status
    return ArtifactStatus.UNVERIFIED


class Evidence(StrEnum):
    """How well one verification facet is supported.

    Vocabulary mirrors `coworker.run_evidence.Evidence`
    (present/absent/partial/missing/expired) so the same words mean the
    same thing codebase-wide. Kept as a separate type here -- that module's
    semantics (`analyze_record`, checkpoint sequences) are specific to
    agent-run records, not documents, so this module only reuses the
    vocabulary, not the run-analysis machinery.
    """

    PRESENT = "present"
    # A named fact was read from the record and it does not hold -- e.g. the
    # body names parties and none of them is the expected one.
    ABSENT = "absent"
    # Some of the facet checked out, some did not -- e.g. the expected party
    # is named, but so is an unexpected one.
    PARTIAL = "partial"
    # Should be readable from the record and isn't there at all.
    MISSING = "missing"
    # The fact held once but a later change invalidated it.
    EXPIRED = "expired"


# Same relative ordering as coworker.run_evidence._SEVERITY for the shared
# values: a clean, positive miss (ABSENT) is more trustworthy than an
# incomplete record (PARTIAL/EXPIRED/MISSING).
_SEVERITY = {
    Evidence.PRESENT: 0,
    Evidence.ABSENT: 1,
    Evidence.PARTIAL: 2,
    Evidence.EXPIRED: 3,
    Evidence.MISSING: 4,
}


def _most_severe(values: list[Evidence]) -> Evidence:
    return max(values, key=lambda value: _SEVERITY[value])


_PARTY_PATTERN = re.compile(
    r"\b(?:between|among)\s+(?P<parties>[^.\n]+)",
    re.IGNORECASE,
)
_PARTY_SPLIT_RE = re.compile(r",|\band\b", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"^\s*[\[{].{0,80}[\]}]\s*$")
_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}/\d{1,2}/\d{2,4}\b"
)
_DATE_PLACEHOLDER_RE = re.compile(r"[\[{][^\]}]*date[^\]}]*[\]}]", re.IGNORECASE)
_TERM_RE = re.compile(
    r"\bterm\s+of\s+(?:this\s+)?agreement\b"
    r"|\bshall\s+(?:remain\s+in\s+effect|continue)\s+for\b",
    re.IGNORECASE,
)
_TERM_PLACEHOLDER_RE = re.compile(r"[\[{][^\]}]*term[^\]}]*[\]}]", re.IGNORECASE)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _parse_timestamp(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_placeholder(name: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(name.strip()))


def _extract_parties(body: str) -> list[str]:
    match = _PARTY_PATTERN.search(body)
    if match is None:
        return []
    parts = [p.strip(" .") for p in _PARTY_SPLIT_RE.split(match.group("parties"))]
    return [p for p in parts if p]


def _verify_parties(body: str, expected_party: str) -> tuple[Evidence, str]:
    parties = _extract_parties(body)
    if not parties:
        return Evidence.MISSING, "no_named_parties_found"
    expected_norm = expected_party.strip().casefold()
    found_expected = any(p.casefold() == expected_norm for p in parties)
    unexpected = [p for p in parties if p.casefold() != expected_norm and not _is_placeholder(p)]
    if not found_expected:
        return Evidence.ABSENT, "expected_party_not_named:" + ", ".join(parties)
    if unexpected:
        return Evidence.PARTIAL, "unexpected_named_party:" + ", ".join(unexpected)
    return Evidence.PRESENT, "parties_verified"


def _verify_dates(body: str) -> tuple[Evidence, str]:
    if _DATE_RE.search(body) or _DATE_PLACEHOLDER_RE.search(body):
        return Evidence.PRESENT, "date_found"
    return Evidence.MISSING, "no_date_found"


def _verify_terms(body: str) -> tuple[Evidence, str]:
    if _TERM_RE.search(body) or _TERM_PLACEHOLDER_RE.search(body):
        return Evidence.PRESENT, "term_found"
    return Evidence.MISSING, "no_term_length_found"


def _verify_approval(
    approval: dict[str, Any] | None, *, modified_time: str | None
) -> tuple[Evidence, str]:
    if not approval:
        return Evidence.MISSING, "no_approval_recorded"
    if approval.get("authorized") is not True:
        return Evidence.ABSENT, "approval_not_by_authorized_reviewer"
    approved_at_raw = _clean(approval.get("approved_at"))
    if not approved_at_raw:
        return Evidence.MISSING, "approval_missing_date"
    approved_at = _parse_timestamp(approved_at_raw)
    if approved_at is None:
        return Evidence.MISSING, "approval_unparseable_date"
    modified = _parse_timestamp(modified_time)
    if modified is None:
        return Evidence.MISSING, "approval_missing_modified_time"
    # A body revised after its approval date means the approval no longer
    # covers what is actually in the file.
    if modified > approved_at:
        return Evidence.EXPIRED, "approval_superseded_by_later_revision"
    return Evidence.PRESENT, "approval_verified"


def classify(
    *,
    artifact_id: str,
    title: str,
    body: str,
    status: str | None,
    expected_party: str,
    approval: dict[str, Any] | None = None,
    modified_time: str | None = None,
) -> dict[str, Any]:
    """Verify one legal artifact's body against its declared facts.

    Reads `body` for parties, dates, and terms -- never trusts `title` or
    caller metadata for those three, so a filename/body mismatch cannot
    hide behind a confident-sounding name. `status` and `approval` are
    facts a body cannot self-attest, so they come from the caller as
    declared; this function only checks whether that declaration still
    holds. `authorized` must be boolean `True`; `approved_at` and
    `modified_time` must both parse as ISO-8601 timestamps; an approval
    dated before the last body revision is stale, whoever recorded it.

    `ready_to_use` is True only when `status` is `approved_template` and
    every facet verifies `present`. Any other status -- draft, executed,
    stale, or unverified -- is never `ready_to_use`, regardless of how
    clean the body reads.
    """
    resolved_status = resolve_status(status)
    facets = {
        "parties": _verify_parties(body, expected_party),
        "dates": _verify_dates(body),
        "terms": _verify_terms(body),
        "approval": _verify_approval(approval, modified_time=modified_time),
    }
    all_present = all(evidence is Evidence.PRESENT for evidence, _ in facets.values())
    ready_to_use = resolved_status is ArtifactStatus.APPROVED_TEMPLATE and all_present
    reasons = [
        f"{facet}:{reason}"
        for facet, (evidence, reason) in facets.items()
        if evidence is not Evidence.PRESENT
    ]
    if resolved_status is not ArtifactStatus.APPROVED_TEMPLATE:
        reasons.insert(0, f"status:{resolved_status.value}")
    return {
        "artifact_id": artifact_id,
        "title": title,
        "status": resolved_status.value,
        "modified_time": modified_time,
        "ready_to_use": ready_to_use,
        "facets": {
            facet: {"evidence": evidence.value, "reason": reason}
            for facet, (evidence, reason) in facets.items()
        },
        "reasons": reasons,
    }


def knowledge_gap_fields(assessment: dict[str, Any]) -> dict[str, Any] | None:
    """The knowledge-gap payload for one not-ready assessment, or None.

    Shaped like the codebase's other knowledge gaps (see
    `PersonStore.record_reply_gap`): a `kind`, an `evidence` word from the
    shared vocabulary, and the specific question a human has to resolve --
    never the artifact's own legal language, since resolving that is
    counsel's job, not this module's.
    """
    if assessment["ready_to_use"]:
        return None
    facet_evidence = [Evidence(facet["evidence"]) for facet in assessment["facets"].values()]
    worst = _most_severe(facet_evidence)
    return {
        "kind": "legal_artifact_not_ready",
        "evidence": worst.value,
        "artifact_id": assessment["artifact_id"],
        "title": assessment["title"],
        "status": assessment["status"],
        "reasons": assessment["reasons"],
        "question": (
            f'Verify parties, dates, terms, and approval for "{assessment["title"]}" '
            "before it can be offered as ready to use."
        ),
    }


def attach_gap(
    people: PersonStore,
    person_id: str,
    assessment: dict[str, Any],
    *,
    actor: str,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """File the knowledge gap for one not-ready legal artifact on a person.

    Returns None -- and writes nothing -- when the artifact verified clean.
    Idempotent per artifact id and revision: re-classifying the same,
    unchanged artifact lands on the same attachment instead of duplicating
    the gap, mirroring `drive_evidence.attach`'s idempotency key shape.
    """
    gap = knowledge_gap_fields(assessment)
    if gap is None:
        return None
    modified_time = assessment.get("modified_time") or "unknown"
    idempotency_key = f"legal_artifact:{assessment['artifact_id']}:{modified_time}"
    return people.upsert_attachment(
        person_id,
        record_type="knowledge_gap",
        fields=gap,
        idempotency_key=idempotency_key,
        actor=actor,
        rationale_summary=f'Legal artifact "{assessment["title"]}" is not ready to use.',
        session_id=session_id,
        run_id=run_id,
    )
