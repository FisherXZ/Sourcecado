import importlib.util
from dataclasses import replace

import pytest

from coworker.context_projection import (
    CategoryBudget,
    ContextAuthority,
    ContextCategory,
    ContextSensitivity,
    ContextSourceRef,
    ContextState,
    DEFAULT_PROJECTION_POLICY,
    ProjectionIdentity,
    ProjectionItem,
    ProjectionPolicy,
    prepare_context_projection,
)

def test_context_projection_proposal_module_exists():
    assert importlib.util.find_spec("coworker.context_projection") is not None


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("persona_id", "buddy"),
        ("session_id", "session-other"),
        ("person_id", "person-other"),
        ("target", "different target"),
        ("prompt_version", "sourcing-director-v2"),
        ("effective_tools_hash", "tools-other"),
    ],
)
def test_prepared_projection_fails_closed_when_binding_changes(
    field, replacement
):
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )
    prepared = prepare_context_projection(identity=identity, items=())

    assert prepared.reuse_for(identity) is prepared
    with pytest.raises(ValueError, match="context projection binding mismatch"):
        prepared.reuse_for(replace(identity, **{field: replacement}))


def test_projection_order_is_category_state_authority_freshness_then_id():
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )

    def ref(source_id):
        return ContextSourceRef(
            id=source_id,
            provider="sourcecado",
            locator=f"sourcecado://{source_id}",
            observed_at="2026-08-27T10:00:00+00:00",
            modified_at="2026-08-27T10:00:00+00:00",
            fresh_until="2026-11-25T10:00:00+00:00",
        )

    items = (
        ProjectionItem(
            id="session-next",
            category=ContextCategory.SESSION_WORKING,
            text="Next: review the draft.",
            tokens=20,
            state=ContextState.CURRENT,
            authority=ContextAuthority.DIRECTOR,
            updated_at="2026-08-27T10:05:00+00:00",
            session_id="session-ada",
            person_id="person-ada",
            source_refs=(ref("message-9"),),
        ),
        ProjectionItem(
            id="evidence-stale",
            category=ContextCategory.PERSON_EVIDENCE,
            text="Older role evidence.",
            tokens=20,
            state=ContextState.STALE,
            authority=ContextAuthority.CONNECTOR,
            updated_at="2026-07-01T10:00:00+00:00",
            person_id="person-ada",
            source_refs=(ref("apollo-old"),),
        ),
        ProjectionItem(
            id="sequence",
            category=ContextCategory.SEQUENCE_STATE,
            text="Sequence: Open.",
            tokens=20,
            state=ContextState.CURRENT,
            authority=ContextAuthority.SOURCECADO_RECORD,
            updated_at="2026-08-27T10:00:00+00:00",
            person_id="person-ada",
            source_refs=(ref("person-v3"),),
        ),
        ProjectionItem(
            id="evidence-conflict",
            category=ContextCategory.PERSON_EVIDENCE,
            text="Two live role claims disagree.",
            tokens=20,
            state=ContextState.CONFLICTING,
            authority=ContextAuthority.CONNECTOR,
            updated_at="2026-08-27T10:00:00+00:00",
            person_id="person-ada",
            claim_key="person.role",
            truncated=True,
            source_refs=(ref("apollo-new"), ref("gmail-signature")),
        ),
        ProjectionItem(
            id="preference",
            category=ContextCategory.OPERATOR_PREFERENCE,
            text="Prefer concise drafts.",
            tokens=20,
            state=ContextState.CURRENT,
            authority=ContextAuthority.DIRECTOR,
            updated_at="2026-08-26T10:00:00+00:00",
            source_refs=(ref("memory-1"),),
        ),
    )

    prepared = prepare_context_projection(identity=identity, items=items)

    assert tuple(item.id for item in prepared.items) == (
        "preference",
        "sequence",
        "evidence-conflict",
        "evidence-stale",
        "session-next",
    )
    assert DEFAULT_PROJECTION_POLICY.total_tokens == 2_048
    conflict = next(item for item in prepared.items if item.id == "evidence-conflict")
    assert conflict.claim_key == "person.role"
    assert conflict.truncated is True
    assert conflict.source_refs[0].fresh_until == "2026-11-25T10:00:00+00:00"
    assert {
        budget.category: (budget.max_tokens, budget.max_item_tokens)
        for budget in DEFAULT_PROJECTION_POLICY.category_budgets
    } == {
        ContextCategory.OPERATOR_PREFERENCE: (256, 64),
        ContextCategory.SEQUENCE_STATE: (256, 256),
        ContextCategory.PERSON_EVIDENCE: (1_024, 160),
        ContextCategory.SESSION_WORKING: (512, 128),
    }


@pytest.mark.parametrize(
    "item",
    [
        ProjectionItem(
            id="other-person",
            category=ContextCategory.PERSON_EVIDENCE,
            text="Unrelated person evidence.",
            tokens=20,
            state=ContextState.CURRENT,
            authority=ContextAuthority.CONNECTOR,
            updated_at="2026-08-27T10:00:00+00:00",
            person_id="person-other",
            source_refs=(
                ContextSourceRef(
                    id="source-1",
                    provider="gmail",
                    locator="gmail://source-1",
                    observed_at="2026-08-27T10:00:00+00:00",
                    modified_at="2026-08-27T10:00:00+00:00",
                ),
            ),
        ),
        ProjectionItem(
            id="other-session",
            category=ContextCategory.SESSION_WORKING,
            text="Another session's next step.",
            tokens=20,
            state=ContextState.CURRENT,
            authority=ContextAuthority.DIRECTOR,
            updated_at="2026-08-27T10:00:00+00:00",
            person_id="person-ada",
            session_id="session-other",
            source_refs=(
                ContextSourceRef(
                    id="message-1",
                    provider="sourcecado",
                    locator="session://session-other/message-1",
                    observed_at="2026-08-27T10:00:00+00:00",
                    modified_at=None,
                ),
            ),
        ),
        ProjectionItem(
            id="scoped-preference",
            category=ContextCategory.OPERATOR_PREFERENCE,
            text="This is not actually global.",
            tokens=20,
            state=ContextState.CURRENT,
            authority=ContextAuthority.DIRECTOR,
            updated_at="2026-08-27T10:00:00+00:00",
            person_id="person-ada",
            source_refs=(
                ContextSourceRef(
                    id="memory-1",
                    provider="sourcecado",
                    locator="memory://1",
                    observed_at="2026-08-27T10:00:00+00:00",
                    modified_at=None,
                ),
            ),
        ),
    ],
)
def test_projection_rejects_cross_scope_item_before_ranking(item):
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )

    with pytest.raises(ValueError, match="context item scope mismatch"):
        prepare_context_projection(identity=identity, items=(item,))


def test_projection_rejects_restricted_item_before_ranking():
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )
    restricted = ProjectionItem(
        id="resume",
        category=ContextCategory.PERSON_EVIDENCE,
        text="Restricted resume excerpt.",
        tokens=20,
        state=ContextState.CURRENT,
        authority=ContextAuthority.CONNECTOR,
        updated_at="2026-08-27T10:00:00+00:00",
        person_id="person-ada",
        sensitivity=ContextSensitivity.RESTRICTED,
        source_refs=(
            ContextSourceRef(
                id="resume-source",
                provider="drive",
                locator="drive://resume-source",
                observed_at="2026-08-27T10:00:00+00:00",
                modified_at="2026-08-27T10:00:00+00:00",
            ),
        ),
    )

    with pytest.raises(ValueError, match="restricted context item"):
        prepare_context_projection(identity=identity, items=(restricted,))


def test_projection_truncates_whole_items_and_reports_content_free_diagnostics():
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )
    source = ContextSourceRef(
        id="memory",
        provider="sourcecado",
        locator="memory://1",
        observed_at="2026-08-27T10:00:00+00:00",
        modified_at=None,
    )
    older = ProjectionItem(
        id="older",
        category=ContextCategory.OPERATOR_PREFERENCE,
        text="PRIVATE OLDER PREFERENCE",
        tokens=15,
        state=ContextState.CURRENT,
        authority=ContextAuthority.DIRECTOR,
        updated_at="2026-08-26T10:00:00+00:00",
        source_refs=(source,),
    )
    newer = replace(
        older,
        id="newer",
        text="PRIVATE NEWER PREFERENCE",
        updated_at="2026-08-27T10:00:00+00:00",
    )
    oversized = ProjectionItem(
        id="oversized",
        category=ContextCategory.PERSON_EVIDENCE,
        text="PRIVATE OVERSIZED EVIDENCE",
        tokens=21,
        state=ContextState.CURRENT,
        authority=ContextAuthority.CONNECTOR,
        updated_at="2026-08-27T10:00:00+00:00",
        person_id="person-ada",
        source_refs=(source,),
    )
    policy = ProjectionPolicy(
        version="context-projection-test",
        total_tokens=40,
        category_budgets=(
            CategoryBudget(ContextCategory.OPERATOR_PREFERENCE, 20, 20),
            CategoryBudget(ContextCategory.PERSON_EVIDENCE, 20, 20),
        ),
    )

    first = prepare_context_projection(
        identity=identity,
        items=(older, oversized, newer),
        policy=policy,
    )
    second = prepare_context_projection(
        identity=identity,
        items=(newer, older, oversized),
        policy=policy,
    )

    assert tuple(item.id for item in first.items) == ("newer",)
    assert first.items == second.items
    assert first.diagnostics == second.diagnostics
    assert first.diagnostics.policy_version == "context-projection-test"
    assert first.diagnostics.selected_item_ids == ("newer",)
    assert first.diagnostics.total_tokens == 15
    assert {
        row.category: (row.selected_count, row.omitted_count, row.used_tokens)
        for row in first.diagnostics.categories
    } == {
        ContextCategory.OPERATOR_PREFERENCE: (1, 1, 15),
        ContextCategory.PERSON_EVIDENCE: (0, 1, 0),
    }
    encoded = repr(first.diagnostics)
    assert "PRIVATE" not in encoded


@pytest.mark.parametrize(
    ("state", "refs", "message"),
    [
        (ContextState.CURRENT, (), "source reference is required"),
        (ContextState.STALE, (), "source reference is required"),
        (
            ContextState.CONFLICTING,
            (
                ContextSourceRef(
                    id="only-one",
                    provider="gmail",
                    locator="gmail://only-one",
                    observed_at="2026-08-27T10:00:00+00:00",
                    modified_at=None,
                ),
            ),
            "conflicting context needs at least two source references",
        ),
    ],
)
def test_evidence_state_requires_inspectable_source_references(state, refs, message):
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )
    item = ProjectionItem(
        id="evidence",
        category=ContextCategory.PERSON_EVIDENCE,
        text="Claim or conflict.",
        tokens=20,
        state=state,
        authority=ContextAuthority.CONNECTOR,
        updated_at="2026-08-27T10:00:00+00:00",
        person_id="person-ada",
        source_refs=refs,
    )

    with pytest.raises(ValueError, match=message):
        prepare_context_projection(identity=identity, items=(item,))


def test_missing_state_is_explicit_without_inventing_a_source_reference():
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id="person-ada",
        target="research dinner",
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )
    missing = ProjectionItem(
        id="gap-email",
        category=ContextCategory.PERSON_EVIDENCE,
        text="Missing: verified email address.",
        tokens=12,
        state=ContextState.MISSING,
        authority=ContextAuthority.SOURCECADO_RECORD,
        updated_at="2026-08-27T10:00:00+00:00",
        person_id="person-ada",
        source_refs=(),
    )

    prepared = prepare_context_projection(identity=identity, items=(missing,))

    assert prepared.items == (missing,)


def test_overall_token_budget_still_caps_custom_category_budgets():
    identity = ProjectionIdentity(
        persona_id="sourcing",
        session_id="session-ada",
        person_id=None,
        target=None,
        prompt_version="sourcing-director-v1",
        effective_tools_hash="tools-v1",
    )
    preference = ProjectionItem(
        id="preference",
        category=ContextCategory.OPERATOR_PREFERENCE,
        text="Prefer concise drafts.",
        tokens=15,
        state=ContextState.CURRENT,
        authority=ContextAuthority.DIRECTOR,
        updated_at="2026-08-27T10:00:00+00:00",
        source_refs=(
            ContextSourceRef(
                id="memory-1",
                provider="sourcecado",
                locator="memory://1",
                observed_at="2026-08-27T10:00:00+00:00",
                modified_at=None,
            ),
        ),
    )
    policy = ProjectionPolicy(
        version="overall-budget-test",
        total_tokens=10,
        category_budgets=(
            CategoryBudget(ContextCategory.OPERATOR_PREFERENCE, 20, 20),
        ),
    )

    prepared = prepare_context_projection(
        identity=identity,
        items=(preference,),
        policy=policy,
    )

    assert prepared.items == ()
    assert prepared.diagnostics.total_tokens == 0
    assert prepared.diagnostics.categories[0].omitted_count == 1
