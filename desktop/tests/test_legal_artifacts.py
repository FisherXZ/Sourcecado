"""Legal artifact source-safety classification.

Covers issue #39: Sourcecado must treat legal documents as evidence, not
ready-to-use agreements, until every named party, date, term, and approval
status is verified from the artifact's own body -- never from its filename
or from caller-supplied metadata alone.

The fixture bodies model the quarantined `Codeology NDA Template` scenario
(desktop/tests/test_drive.py, docs/qa/2026-08-26-pr35-full-user-trail.md,
docs/runbooks/p0-source-safety-remediation.md) with obviously fictional
party names -- no real company or agreement text.
"""

from __future__ import annotations

import pytest

from coworker.legal_artifacts import (
    ArtifactStatus,
    attach_gap,
    classify,
    knowledge_gap_fields,
    resolve_status,
)
from coworker.people import PersonStore

EXPECTED_PARTY = "Berkeley Codeology"

_FRESH_APPROVAL = {
    "approved_by": "counsel@bcodeology.example",
    "approved_at": "2026-08-15T00:00:00Z",
    "authorized": True,
}


def _valid_body() -> str:
    return (
        "NONDISCLOSURE AGREEMENT between Berkeley Codeology and "
        "[COUNTERPARTY NAME]. Effective Date: [EFFECTIVE DATE]. "
        "Term of this Agreement shall be [TERM LENGTH]."
    )


def _mismatched_party_body() -> str:
    # Models the stale NDA: filename says Codeology, body names two
    # unrelated fictional parties instead.
    return (
        "NONDISCLOSURE AGREEMENT between Northwind Distribution and "
        "Ridgeline Ventures. Effective Date: [EFFECTIVE DATE]. "
        "Term of this Agreement shall be [TERM LENGTH]."
    )


def _partial_party_body() -> str:
    return (
        "NONDISCLOSURE AGREEMENT between Berkeley Codeology, Ridgeline "
        "Ventures, and [COUNTERPARTY NAME]. Effective Date: [EFFECTIVE DATE]. "
        "Term of this Agreement shall be [TERM LENGTH]."
    )


def _missing_date_body() -> str:
    return (
        "NONDISCLOSURE AGREEMENT between Berkeley Codeology and "
        "[COUNTERPARTY NAME]. Term of this Agreement shall be [TERM LENGTH]."
    )


def _missing_term_body() -> str:
    return (
        "NONDISCLOSURE AGREEMENT between Berkeley Codeology and "
        "[COUNTERPARTY NAME]. Effective Date: [EFFECTIVE DATE]."
    )


def _person(people: PersonStore):
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="Lovelace",
        title="Founder",
        company="Analytic",
    )
    return people.get(person["person_id"])


# --- resolve_status ------------------------------------------------------


def test_resolve_status_defaults_undeclared_status_to_unverified():
    assert resolve_status(None) is ArtifactStatus.UNVERIFIED
    assert resolve_status("") is ArtifactStatus.UNVERIFIED
    assert resolve_status("not a real status") is ArtifactStatus.UNVERIFIED


def test_resolve_status_recognizes_declared_states():
    assert resolve_status("draft") is ArtifactStatus.DRAFT
    assert resolve_status("APPROVED_TEMPLATE") is ArtifactStatus.APPROVED_TEMPLATE
    assert resolve_status("executed") is ArtifactStatus.EXECUTED
    assert resolve_status("stale") is ArtifactStatus.STALE


# --- classify: the four required cases ------------------------------------


def test_classify_flags_filename_body_party_mismatch():
    assessment = classify(
        artifact_id="drive:nda-1",
        title="Codeology NDA Template",
        body=_mismatched_party_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    # non-vacuous: the body was actually parsed before we judge readiness
    parties = assessment["facets"]["parties"]
    assert parties["evidence"] == "absent"
    assert "northwind distribution" in parties["reason"].lower()
    assert "ridgeline ventures" in parties["reason"].lower()

    assert assessment["ready_to_use"] is False
    assert any(reason.startswith("parties:") for reason in assessment["reasons"])


def test_classify_flags_partial_party_match():
    assessment = classify(
        artifact_id="drive:nda-partial",
        title="Codeology NDA Template",
        body=_partial_party_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    parties = assessment["facets"]["parties"]
    assert parties["evidence"] == "partial"
    assert "ridgeline ventures" in parties["reason"].lower()
    assert assessment["ready_to_use"] is False


# --- no declared counterparty: the title becomes the expectation ----------
#
# This is the shape every connector read hands in. Without it the parties
# facet had one reachable value and a stale NDA graded the same as a clean
# template. The title may expose a disagreement. It may never grant anything.


def test_classify_grades_body_parties_against_the_title_when_none_is_declared():
    """The filename/body mismatch stays visible to a caller with no expectation.

    Title names one organization, body names two others. That is the exact
    failure issue #39 exists to catch, and a connector read never declares a
    counterparty of its own.
    """
    assessment = classify(
        artifact_id="drive:nda-undeclared",
        title="Codeology NDA Template",
        body=_mismatched_party_body(),
        status=None,
        expected_party="",
    )

    parties = assessment["facets"]["parties"]
    assert parties["evidence"] == "absent"
    assert parties["reason"].startswith("body_parties_absent_from_title:")
    assert "Northwind Distribution" in parties["reason"]
    assert "Ridgeline Ventures" in parties["reason"]
    assert assessment["status"] == "unverified"
    assert assessment["ready_to_use"] is False


def test_classify_reads_a_body_that_names_the_titles_organization_as_present():
    """The inverse guard: a document that agrees with its own title is not flagged.

    A checker that grades every legal file `absent` is the same noise the
    two-string hotfix was, and noise gets disabled.
    """
    assessment = classify(
        artifact_id="drive:nda-agrees",
        title="Berkeley Codeology NDA Template",
        body=_valid_body(),
        status=None,
        expected_party="",
    )

    parties = assessment["facets"]["parties"]
    assert parties["evidence"] == "present"
    assert parties["reason"] == "title_party_named_in_body"


def test_classify_flags_an_extra_party_the_title_does_not_name():
    assessment = classify(
        artifact_id="drive:nda-extra",
        title="Ridgeline Ventures NDA Template",
        body=_mismatched_party_body(),
        status=None,
        expected_party="",
    )

    parties = assessment["facets"]["parties"]
    assert parties["evidence"] == "partial"
    assert "Northwind Distribution" in parties["reason"]
    assert "Ridgeline Ventures" not in parties["reason"]


def test_classify_cannot_compare_parties_that_are_all_placeholders():
    """An unfilled template names no real party, so there is nothing to compare."""
    body = (
        "NONDISCLOSURE AGREEMENT between [DISCLOSER] and [RECIPIENT]. "
        "Effective Date: [EFFECTIVE DATE]. Term of this Agreement shall be "
        "[TERM LENGTH]."
    )
    assessment = classify(
        artifact_id="drive:nda-blank",
        title="Codeology NDA Template",
        body=body,
        status=None,
        expected_party="",
    )

    parties = assessment["facets"]["parties"]
    assert parties["evidence"] == "missing"
    assert parties["reason"].startswith("no_expected_party_declared:")


def test_classify_never_grants_ready_to_use_on_a_title_match_alone():
    """A filename is never enough to make a document usable.

    Every facet reads clean and the caller declares `approved_template` with a
    fresh authorized approval. The only expectation the parties facet had was
    the title, so the artifact is still withheld. Without this gate the title
    becomes a way to earn readiness by naming a file well.
    """
    assessment = classify(
        artifact_id="drive:nda-title-only",
        title="Berkeley Codeology NDA Template",
        body=_valid_body(),
        status="approved_template",
        expected_party="",
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    # non-vacuous: nothing else is blocking readiness here
    assert all(
        facet["evidence"] == "present" for facet in assessment["facets"].values()
    )
    assert assessment["status"] == "approved_template"

    assert assessment["ready_to_use"] is False
    assert "parties:expectation_taken_from_title_not_declared" in assessment["reasons"]


def test_knowledge_gap_question_names_the_party_disagreement():
    """The question a human reads has to say what is actually wrong.

    `evidence` is the most severe facet, and a connector read never carries an
    approval record, so it reads `missing` for every artifact. The question is
    what distinguishes a mismatch from an ordinary unverified file.
    """
    mismatch = classify(
        artifact_id="drive:nda-undeclared",
        title="Codeology NDA Template",
        body=_mismatched_party_body(),
        status=None,
        expected_party="",
    )
    agrees = classify(
        artifact_id="drive:nda-agrees",
        title="Berkeley Codeology NDA Template",
        body=_valid_body(),
        status=None,
        expected_party="",
    )

    mismatch_gap = knowledge_gap_fields(mismatch)
    agrees_gap = knowledge_gap_fields(agrees)

    assert mismatch_gap["question"] != agrees_gap["question"]
    assert "Northwind Distribution" in mismatch_gap["question"]
    assert "Ridgeline Ventures" in mismatch_gap["question"]
    assert agrees_gap["question"].startswith("Verify parties, dates, terms")


def test_classify_flags_missing_dates():
    assessment = classify(
        artifact_id="drive:nda-2",
        title="Codeology NDA Template",
        body=_missing_date_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    # non-vacuous: parties verified clean, proving the mismatch above wasn't
    # what tripped this assessment
    assert assessment["facets"]["parties"]["evidence"] == "present"
    dates = assessment["facets"]["dates"]
    assert dates["evidence"] == "missing"
    assert dates["reason"] == "no_date_found"

    assert assessment["ready_to_use"] is False
    assert any(reason.startswith("dates:") for reason in assessment["reasons"])


def test_classify_flags_missing_terms():
    assessment = classify(
        artifact_id="drive:nda-3",
        title="Codeology NDA Template",
        body=_missing_term_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    assert assessment["facets"]["parties"]["evidence"] == "present"
    assert assessment["facets"]["dates"]["evidence"] == "present"
    terms = assessment["facets"]["terms"]
    assert terms["evidence"] == "missing"
    assert terms["reason"] == "no_term_length_found"
    assert assessment["ready_to_use"] is False


def test_classify_flags_stale_approval():
    stale_approval = {
        "approved_by": "counsel@bcodeology.example",
        "approved_at": "2026-07-01T00:00:00Z",
        "authorized": True,
    }
    assessment = classify(
        artifact_id="drive:nda-4",
        title="Codeology NDA Template",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=stale_approval,
        # Document body changed after the approval date -- the approval no
        # longer covers what is actually in the file.
        modified_time="2026-08-10T00:00:00Z",
    )

    # non-vacuous: every body-read facet verified clean; only approval failed
    assert assessment["facets"]["parties"]["evidence"] == "present"
    assert assessment["facets"]["dates"]["evidence"] == "present"
    assert assessment["facets"]["terms"]["evidence"] == "present"
    approval = assessment["facets"]["approval"]
    assert approval["evidence"] == "expired"
    assert approval["reason"] == "approval_superseded_by_later_revision"

    assert assessment["ready_to_use"] is False
    assert any(reason.startswith("approval:") for reason in assessment["reasons"])


def test_classify_missing_approval_record():
    assessment = classify(
        artifact_id="drive:nda-5",
        title="Codeology NDA Template",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=None,
        modified_time="2026-08-01T00:00:00Z",
    )

    assert assessment["facets"]["approval"]["evidence"] == "missing"
    assert assessment["ready_to_use"] is False


def test_classify_unauthorized_approver_is_not_a_real_approval():
    assessment = classify(
        artifact_id="drive:nda-6",
        title="Codeology NDA Template",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval={
            "approved_by": "random-teammate@example.com",
            "approved_at": "2026-08-15T00:00:00Z",
            "authorized": False,
        },
        modified_time="2026-08-01T00:00:00Z",
    )

    approval = assessment["facets"]["approval"]
    assert approval["evidence"] == "absent"
    assert approval["reason"] == "approval_not_by_authorized_reviewer"
    assert assessment["ready_to_use"] is False


def test_classify_verifies_a_valid_reviewed_template():
    """A checker that refuses everything is useless -- prove a genuinely
    valid, reviewed template actually clears every facet."""
    assessment = classify(
        artifact_id="drive:nda-valid",
        title="Codeology NDA Template (reviewed)",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    assert assessment["facets"]["parties"] == {
        "evidence": "present",
        "reason": "parties_verified",
    }
    assert assessment["facets"]["dates"] == {
        "evidence": "present",
        "reason": "date_found",
    }
    assert assessment["facets"]["terms"] == {
        "evidence": "present",
        "reason": "term_found",
    }
    assert assessment["facets"]["approval"] == {
        "evidence": "present",
        "reason": "approval_verified",
    }
    assert assessment["ready_to_use"] is True
    assert assessment["reasons"] == []
    assert knowledge_gap_fields(assessment) is None


def test_classify_does_not_treat_unparseable_approval_timestamp_as_verified():
    """A ready-to-use label requires a real, comparable approval time.

    The stale-approval check only works if `approved_at` is an actual
    timestamp. A nonempty fake string is not an approval.
    """
    assessment = classify(
        artifact_id="drive:nda-valid",
        title="Codeology NDA Template (reviewed)",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval={
            "approved_by": "counsel@bcodeology.example",
            "approved_at": "not-a-timestamp",
            "authorized": True,
        },
        modified_time="2026-08-01T00:00:00Z",
    )

    assert assessment["facets"]["parties"]["evidence"] == "present"
    assert assessment["facets"]["dates"]["evidence"] == "present"
    assert assessment["facets"]["terms"]["evidence"] == "present"
    approval = assessment["facets"]["approval"]
    assert approval["evidence"] == "missing"
    assert approval["reason"] == "approval_unparseable_date"
    assert assessment["ready_to_use"] is False
    assert "approval:approval_unparseable_date" in assessment["reasons"]


def test_classify_does_not_treat_approval_as_fresh_without_artifact_modified_time():
    """Without a comparable artifact revision time, staleness cannot be checked.

    Omitting `modified_time` is not evidence that the body is still the
    one that was approved.
    """
    assessment = classify(
        artifact_id="drive:nda-valid",
        title="Codeology NDA Template (reviewed)",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time=None,
    )

    assert assessment["facets"]["parties"]["evidence"] == "present"
    assert assessment["facets"]["dates"]["evidence"] == "present"
    assert assessment["facets"]["terms"]["evidence"] == "present"
    approval = assessment["facets"]["approval"]
    assert approval["evidence"] == "missing"
    assert approval["reason"] == "approval_missing_modified_time"
    assert assessment["ready_to_use"] is False
    assert "approval:approval_missing_modified_time" in assessment["reasons"]


def test_classify_does_not_treat_string_false_as_authorized():
    """Authorization is a boolean fact, not a nonempty string.

    The string "false" is truthy in Python, but it is not an authorized
    review. Only an actual True mark may clear this facet.
    """
    assessment = classify(
        artifact_id="drive:nda-valid",
        title="Codeology NDA Template (reviewed)",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval={
            "approved_by": "counsel@bcodeology.example",
            "approved_at": "2026-08-15T00:00:00Z",
            "authorized": "false",
        },
        modified_time="2026-08-01T00:00:00Z",
    )

    assert assessment["facets"]["parties"]["evidence"] == "present"
    assert assessment["facets"]["dates"]["evidence"] == "present"
    assert assessment["facets"]["terms"]["evidence"] == "present"
    approval = assessment["facets"]["approval"]
    assert approval["evidence"] == "absent"
    assert approval["reason"] == "approval_not_by_authorized_reviewer"
    assert assessment["ready_to_use"] is False
    assert "approval:approval_not_by_authorized_reviewer" in assessment["reasons"]


# --- status gates readiness independent of facet verification -------------


def test_undeclared_status_blocks_readiness_even_with_clean_facets():
    assessment = classify(
        artifact_id="drive:nda-7",
        title="Codeology NDA Template",
        body=_valid_body(),
        status=None,
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    # non-vacuous: every facet genuinely verified clean
    assert all(
        facet["evidence"] == "present" for facet in assessment["facets"].values()
    )
    assert assessment["status"] == "unverified"
    assert assessment["ready_to_use"] is False
    assert "status:unverified" in assessment["reasons"]


def test_stale_status_is_never_offered_as_ready_regardless_of_facets():
    """The quarantine case: even a clean-reading body must not earn
    ready_to_use once a human has marked the artifact stale."""
    assessment = classify(
        artifact_id="drive:nda-8",
        title="Codeology NDA Template",
        body=_valid_body(),
        status="stale",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    assert all(
        facet["evidence"] == "present" for facet in assessment["facets"].values()
    )
    assert assessment["status"] == "stale"
    assert assessment["ready_to_use"] is False
    assert "status:stale" in assessment["reasons"]


def test_executed_status_is_never_offered_as_a_reusable_template():
    assessment = classify(
        artifact_id="drive:nda-9",
        title="Codeology NDA (executed)",
        body=_valid_body(),
        status="executed",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    assert assessment["status"] == "executed"
    assert assessment["ready_to_use"] is False


# --- knowledge gap wiring onto the existing PersonStore mechanism ---------


def test_attach_gap_files_a_knowledge_gap_using_the_existing_attachment_type(tmp_path):
    people = PersonStore(tmp_path)
    ada = _person(people)
    assessment = classify(
        artifact_id="drive:nda-1",
        title="Codeology NDA Template",
        body=_mismatched_party_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )
    # non-vacuous: confirm classify() actually found the mismatch before we
    # check that the gap-filing path reacted to it
    assert assessment["facets"]["parties"]["evidence"] == "absent"

    gap = attach_gap(
        people,
        ada["person_id"],
        assessment,
        actor="director",
    )

    assert gap is not None
    assert gap["type"] == "knowledge_gap"
    assert gap["fields"]["kind"] == "legal_artifact_not_ready"
    assert gap["fields"]["evidence"] == "absent"
    assert gap["fields"]["artifact_id"] == "drive:nda-1"

    person = people.get(ada["person_id"], expand_sources=True)
    assert len(person["knowledge_gaps"]) == 1
    assert person["knowledge_gaps"][0]["id"] == gap["id"]


def test_attach_gap_is_idempotent_for_the_same_artifact_and_revision(tmp_path):
    people = PersonStore(tmp_path)
    ada = _person(people)
    assessment = classify(
        artifact_id="drive:nda-1",
        title="Codeology NDA Template",
        body=_mismatched_party_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    first = attach_gap(people, ada["person_id"], assessment, actor="director")
    second = attach_gap(people, ada["person_id"], assessment, actor="director")

    assert first["id"] == second["id"]
    assert len(people.get(ada["person_id"], expand_sources=True)["knowledge_gaps"]) == 1


def test_attach_gap_returns_none_when_the_artifact_is_ready(tmp_path):
    people = PersonStore(tmp_path)
    ada = _person(people)
    assessment = classify(
        artifact_id="drive:nda-valid",
        title="Codeology NDA Template (reviewed)",
        body=_valid_body(),
        status="approved_template",
        expected_party=EXPECTED_PARTY,
        approval=_FRESH_APPROVAL,
        modified_time="2026-08-01T00:00:00Z",
    )

    gap = attach_gap(people, ada["person_id"], assessment, actor="director")

    assert gap is None
    assert people.get(ada["person_id"], expand_sources=True)["knowledge_gaps"] == []
