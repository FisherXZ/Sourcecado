"""Memory rows carry the approved context-projection category contract.

`context-projection-v1` (issue #58) gives every memory row a category, a scope,
a Source Reference, an updated time, a freshness window, a sensitivity, and a
conflict key. Only a row the director explicitly classified as a global operator
preference may become model context; everything else is withheld.
"""

from datetime import UTC, datetime, timedelta

import pytest

from coworker.context_projection import (
    ContextAuthority,
    ContextCategory,
    ContextSensitivity,
    ContextState,
)
from coworker.store import (
    MEMORY_CATEGORY_PREFERENCE,
    MEMORY_CATEGORY_UNCLASSIFIED,
    MEMORY_CLASSIFIED,
    MEMORY_NEEDS_REVIEW,
    ConversationStore,
    projection_tokens,
)


def test_a_new_remember_write_is_ambiguous_and_waits_for_review(tmp_path):
    store = ConversationStore(tmp_path)

    item = store.remember("Prefer outreach drafts under 140 words.")

    assert item["category"] == MEMORY_CATEGORY_UNCLASSIFIED
    assert item["classification_status"] == MEMORY_NEEDS_REVIEW
    assert item["person_id"] is None
    assert item["session_id"] is None
    assert item["sensitivity"] == "standard"
    assert item["source_ref"] == "sourcecado:memory/1"
    assert item["updated_at"]


def test_an_ambiguous_write_never_reaches_model_context(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Prefer outreach drafts under 140 words.")

    assert store.memory_projection_items() == ()


def test_the_director_classifying_a_preference_puts_it_in_context(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Prefer outreach drafts under 140 words.")

    classified = store.memory_classify(1)

    assert classified is not None
    assert classified["category"] == MEMORY_CATEGORY_PREFERENCE
    assert classified["classification_status"] == MEMORY_CLASSIFIED
    items = store.memory_projection_items()
    assert len(items) == 1
    projected = items[0]
    assert projected.category is ContextCategory.OPERATOR_PREFERENCE
    assert projected.authority is ContextAuthority.DIRECTOR
    assert projected.state is ContextState.CURRENT
    assert projected.sensitivity is ContextSensitivity.STANDARD
    assert projected.person_id is None
    assert projected.session_id is None
    assert "Prefer outreach drafts under 140 words." in projected.text
    assert projected.source_refs[0].id == "sourcecado:memory/1"
    assert projected.source_refs[0].provider == "sourcecado"


def test_classifying_a_missing_row_reports_nothing_to_classify(tmp_path):
    store = ConversationStore(tmp_path)

    assert store.memory_classify(9) is None


def test_a_person_scoped_row_cannot_become_a_global_preference(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Ada moved to Analytic in June.", person_id="per_ada")

    with pytest.raises(ValueError, match="scoped"):
        store.memory_classify(1)
    assert store.memory_projection_items() == ()


def test_a_session_scoped_row_cannot_become_a_global_preference(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Working the Rippling list this session.", session_id="main")

    with pytest.raises(ValueError, match="scoped"):
        store.memory_classify(1)
    assert store.memory_projection_items() == ()


def test_a_restricted_row_is_withheld_even_once_classified(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Prefer outreach drafts under 140 words.")
    store.remember("Restricted note.", sensitivity="restricted")
    store.memory_classify(1)
    store.memory_classify(2)

    projected = store.memory_projection_items()

    # Non-vacuous: the standard preference is present, the restricted one is not.
    assert [item.id for item in projected] == ["memory:1"]


def test_two_preferences_on_one_claim_key_are_conflicting_with_both_sources(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Keep outreach drafts under 140 words.")
    store.remember("Keep outreach drafts under 90 words.")
    store.memory_classify(1, claim_key="draft-length")
    store.memory_classify(2, claim_key="draft-length")

    projected = store.memory_projection_items()

    assert len(projected) == 2
    for item in projected:
        assert item.state is ContextState.CONFLICTING
        assert len(item.source_refs) == 2
    assert {ref.id for ref in projected[0].source_refs} == {
        "sourcecado:memory/1",
        "sourcecado:memory/2",
    }


def test_a_preference_past_its_freshness_window_is_labeled_stale(tmp_path):
    store = ConversationStore(tmp_path)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    store.remember("Prefer 140-word drafts.", fresh_until=past)
    store.memory_classify(1)

    projected = store.memory_projection_items()

    assert len(projected) == 1
    assert projected[0].state is ContextState.STALE


def test_a_long_preference_is_clipped_to_the_per_item_cap_and_marked(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember(" ".join(["word"] * 200))
    store.memory_classify(1)

    projected = store.memory_projection_items()

    assert len(projected) == 1
    item = projected[0]
    assert item.truncated is True
    assert item.tokens <= 64
    assert item.tokens == projection_tokens(item.text)
    assert item.text.endswith(")")
    assert "sourcecado:memory/1" in item.text


def test_the_budget_unit_counts_utf8_bytes_in_threes():
    assert projection_tokens("") == 0
    assert projection_tokens("abc") == 1
    assert projection_tokens("abcd") == 2
    assert projection_tokens("é") == 1


def test_the_backlog_counts_what_is_waiting_and_what_is_live(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Prefer outreach drafts under 140 words.")
    store.remember("Ada moved to Analytic in June.")
    store.memory_classify(1)

    backlog = store.memory_backlog()

    assert backlog["needs_review"] == 1
    assert backlog["classified"] == 1
    assert [row["id"] for row in backlog["items"]] == [2]
    assert backlog["items"][0]["content"] == "Ada moved to Analytic in June."


def test_rewriting_a_classified_preference_sends_it_back_for_review(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Keep outreach drafts under 140 words.")
    store.memory_classify(1)
    assert len(store.memory_projection_items()) == 1

    updated = store.memory_update(1, "Send without asking first.")

    assert updated is not None
    assert updated["classification_status"] == MEMORY_NEEDS_REVIEW
    assert store.memory_projection_items() == ()


def test_forgetting_a_row_drains_it_from_the_backlog(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Obsolete note.")

    assert store.memory_forget(1) is True
    assert store.memory_backlog()["needs_review"] == 0
