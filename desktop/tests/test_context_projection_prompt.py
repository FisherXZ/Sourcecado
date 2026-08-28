"""The saved-memory prompt section is a bounded projection, not a store dump.

Before context-projection-v1 every memory row was joined oldest-first and the
whole string clipped at 4,000 characters, which could cut a row in half and
could not tell an operator preference from a fact about one Person. The section
now carries only classified operator preferences, selected under
`DEFAULT_PROJECTION_POLICY`.
"""

from coworker import server
from coworker.context_projection import DEFAULT_PROJECTION_POLICY, ContextCategory
from coworker.persona import load_persona
from coworker.store import ConversationStore, projection_tokens


def _saved_memory(assembled) -> str | None:
    marker = "## Saved memory\n\n"
    if marker not in assembled.text:
        return None
    return assembled.text.split(marker, 1)[1].split("\n\n##", 1)[0]


def _preference_budget():
    return next(
        budget
        for budget in DEFAULT_PROJECTION_POLICY.category_budgets
        if budget.category is ContextCategory.OPERATOR_PREFERENCE
    )


def test_an_unclassified_row_never_reaches_the_prompt(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("WITHHELD LEGACY SENTINEL")
    store.remember("Keep outreach drafts under 140 words.")
    store.memory_classify(2)

    assembled = server.system_prompt_assembly(store, load_persona("sourcing"))

    section = _saved_memory(assembled)
    # Non-vacuous: the classified preference is there, the unclassified one is not.
    assert section is not None
    assert "Keep outreach drafts under 140 words." in section
    assert "WITHHELD LEGACY SENTINEL" not in assembled.text


def test_the_section_is_absent_when_nothing_is_classified(tmp_path):
    store = ConversationStore(tmp_path)
    store.remember("Codeology sources design-adjacent engineers first.")

    assembled = server.system_prompt_assembly(store, load_persona("sourcing"))

    assert _saved_memory(assembled) is None
    assert "saved_memory" not in [
        item.section_id for item in assembled.diagnostics.dynamic_context_sections
    ]


def test_the_preference_category_never_borrows_another_categorys_budget(tmp_path):
    store = ConversationStore(tmp_path)
    for index in range(40):
        store.remember(f"Preference {index}: " + "w" * 120)
        store.memory_classify(index + 1)
    budget = _preference_budget()

    assembled = server.system_prompt_assembly(store, load_persona("sourcing"))

    section = _saved_memory(assembled)
    assert section is not None
    used = sum(projection_tokens(line) for line in section.splitlines())
    assert used <= budget.max_tokens
    # The whole-projection budget is eight times the category cap. Staying under
    # the category cap is the claim; staying under 2,048 alone would not be.
    assert budget.max_tokens < DEFAULT_PROJECTION_POLICY.total_tokens


def test_every_projected_preference_is_a_whole_item(tmp_path):
    store = ConversationStore(tmp_path)
    for index in range(12):
        store.remember(f"Preference {index}: " + "w" * 60)
        store.memory_classify(index + 1)

    assembled = server.system_prompt_assembly(store, load_persona("sourcing"))

    section = _saved_memory(assembled)
    assert section is not None
    lines = section.splitlines()
    assert lines
    for line in lines:
        assert line.startswith("[#")
        assert "sourcecado:memory/" in line
        assert line.endswith(")")
        assert projection_tokens(line) <= _preference_budget().max_item_tokens


def test_the_combined_memory_and_person_envelope_is_still_six_thousand_chars(tmp_path):
    from coworker.people import PersonStore

    store = ConversationStore(tmp_path)
    for index in range(40):
        store.remember(f"Preference {index}: " + "w" * 120)
        store.memory_classify(index + 1)
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
        target="research dinner",
    )
    people.bind_session("session-ada", person["person_id"])
    for index in range(12):
        people.append_event(
            person["person_id"],
            source="gmail",
            kind="mail",
            summary=f"event-{index}-" + "p" * 300,
        )

    assembled = server.system_prompt_assembly(
        store,
        load_persona("sourcing"),
        people=people,
        session_id="session-ada",
    )

    sections = {
        item.section_id: item for item in assembled.diagnostics.dynamic_context_sections
    }
    assert "saved_memory" in sections
    assert "person_file" in sections
    combined = sections["saved_memory"].chars + sections["person_file"].chars
    assert combined <= 6_000


def test_a_person_bound_session_still_gets_only_global_preferences(tmp_path):
    from coworker.people import PersonStore

    store = ConversationStore(tmp_path)
    store.remember("Keep outreach drafts under 140 words.")
    store.memory_classify(1)
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(
        apollo_id="ada",
        first_name="Ada",
        last_name_obfuscated="L",
        title="Founder",
        company="Analytic",
        target="research dinner",
    )
    people.bind_session("session-ada", person["person_id"])

    assembled = server.system_prompt_assembly(
        store,
        load_persona("sourcing"),
        people=people,
        session_id="session-ada",
    )

    section = _saved_memory(assembled)
    assert section is not None
    assert "Keep outreach drafts under 140 words." in section
