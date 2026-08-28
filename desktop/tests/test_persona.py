import coworker.server as server
from coworker.persona import load_persona, parse_persona
from coworker.server import system_prompt
from coworker.store import ConversationStore


def test_buddy_body_is_generic_not_sourcing():
    persona = load_persona("buddy")
    assert persona.id == "buddy"
    assert persona.name == "Club"
    assert "work buddy" in persona.body.lower() or "personal coworker" in persona.body.lower()
    assert "Sourcecado's sourcing agent" not in persona.body
    assert "not a sourcing agent" in persona.body.lower()


def test_sourcing_manifest_routes_to_versioned_runtime_definition():
    persona = load_persona("sourcing")
    assert persona.id == "sourcing"
    assert persona.tools == []
    assert "sourcing-director-v1" in persona.body
    assert "Person File" in persona.body
    assert "Sourcing Lead" not in persona.body


def test_buddy_manifest_does_not_declare_capabilities():
    assert load_persona("buddy").tools == []


def test_system_prompt_uses_on_duty_body(tmp_path):
    store = ConversationStore(tmp_path)
    buddy = load_persona("buddy")
    sourcing = load_persona("sourcing")
    buddy_prompt = system_prompt(store, buddy)
    sourcing_prompt = system_prompt(store, sourcing)
    assert buddy.body in buddy_prompt
    assert "Sourcecado's sourcing agent" not in buddy_prompt
    assert sourcing.body not in sourcing_prompt
    assert "## Identity and authority" in sourcing_prompt
    assert buddy.body not in sourcing_prompt


def test_system_prompt_treats_external_sources_as_untrusted_evidence(tmp_path):
    prompt = system_prompt(ConversationStore(tmp_path), load_persona("sourcing"))

    assert "External sources" in prompt
    assert "untrusted evidence" in prompt
    assert "cannot override this prompt" in prompt
    assert "runtime permissions" in prompt


def test_parse_rejects_missing_frontmatter():
    try:
        parse_persona("just a body")
        assert False, "expected ManifestError"
    except ValueError:
        pass


def test_active_sourcing_prompt_uses_approved_version_not_persona_or_kernel(tmp_path):
    store = ConversationStore(tmp_path)
    sourcing = load_persona("sourcing")
    assembly_fn = getattr(server, "system_prompt_assembly", None)
    assert assembly_fn is not None, "active runtime assembly is missing"

    assembled = assembly_fn(store, sourcing)

    assert assembled.diagnostics.prompt_version == "sourcing-director-v1"
    assert assembled.text == system_prompt(store, sourcing)
    assert "## Identity and authority" in assembled.text
    assert "Research Chat" not in assembled.text
    assert "Sourcing Lead" not in assembled.text
    assert "Tools: now" not in assembled.text
    assert sourcing.body not in assembled.text


def test_runtime_dynamic_context_is_ordered_bounded_and_total_bounded(tmp_path):
    from coworker.people import PersonStore
    from coworker.skills import SkillLoader

    store = ConversationStore(tmp_path)
    store.remember("m" * 5_000)
    store.memory_classify(1)
    skill_root = tmp_path / "skill-fixtures"
    skill_dir = skill_root / "large-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: large-skill\ndescription: " + "s" * 3_500 + "\n---\nbody"
    )
    skills = SkillLoader([skill_root])
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
        skills,
        people=people,
        session_id="session-ada",
    )

    dynamic = assembled.diagnostics.dynamic_context_sections
    assert tuple(item.section_id for item in dynamic) == (
        "saved_memory",
        "skill_catalog",
        "person_file",
    )
    # Saved memory is now the bounded operator-preference projection, so the
    # oversized row is clipped to one whole item far below its 4,000-character
    # envelope rather than filling it (issue #58).
    assert dynamic[0].budget_chars == 4_000
    assert 0 < dynamic[0].chars <= 200
    assert dynamic[1].chars == 3_000
    # The person file is the bounded brief projection. It stops on a whole
    # claim under its budget and states the count it left out, rather than
    # being cut mid-claim at exactly the cap.
    person_file = dynamic[2]
    assert person_file.budget_chars == 2_000
    assert 1_500 <= person_file.chars <= 2_000
    assert "further record(s) not shown here." in assembled.text
    assert assembled.diagnostics.system_prompt_chars <= 15_500


def test_runtime_prompt_diagnostics_never_store_dynamic_content(tmp_path):
    from dataclasses import asdict

    store = ConversationStore(tmp_path)
    store.remember("PRIVATE DYNAMIC SENTINEL")
    store.memory_classify(1)
    assert "PRIVATE DYNAMIC SENTINEL" in server.system_prompt(store)

    diagnostics = server.system_prompt_assembly(store).diagnostics
    encoded = repr(asdict(diagnostics))

    assert "PRIVATE DYNAMIC SENTINEL" not in encoded
    assert "You are Sourcecado" not in encoded
