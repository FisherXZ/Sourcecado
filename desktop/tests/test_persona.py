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


def test_sourcing_pack_uses_repo_vocabulary():
    persona = load_persona("sourcing")
    assert persona.id == "sourcing"
    assert "Sourcecado's sourcing agent" in persona.body
    assert "Sourcing Lead" in persona.body
    assert "why-now" in persona.body


def test_system_prompt_uses_on_duty_body(tmp_path):
    store = ConversationStore(tmp_path)
    buddy = load_persona("buddy")
    sourcing = load_persona("sourcing")
    buddy_prompt = system_prompt(store, buddy)
    sourcing_prompt = system_prompt(store, sourcing)
    assert buddy.body in buddy_prompt
    assert "Sourcecado's sourcing agent" not in buddy_prompt
    assert sourcing.body in sourcing_prompt
    assert buddy.body not in sourcing_prompt


def test_parse_rejects_missing_frontmatter():
    try:
        parse_persona("just a body")
        assert False, "expected ManifestError"
    except ValueError:
        pass
