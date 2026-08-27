from coworker.skills import BUILTIN_SKILLS, SkillLoader, catalog_text, parse_skill
from coworker.store import ConversationStore
from coworker.tools import execute


def test_builtin_weekly_sourcing_skill():
    md = BUILTIN_SKILLS / "weekly-sourcing" / "SKILL.md"
    skill = parse_skill(md)
    assert skill.name == "weekly-sourcing"
    assert "why-now" in skill.description or "why-now" in skill.instructions
    assert "Never send" in skill.instructions or "never send" in skill.instructions.lower()


def test_load_skill_returns_full_body(tmp_path):
    loader = SkillLoader([BUILTIN_SKILLS])
    ok, result = execute("load_skill", {"name": "weekly-sourcing"}, skills=loader)
    assert ok is True
    assert result["name"] == "weekly-sourcing"
    assert "shortlist" in result["instructions"].lower() or "why-now" in result["instructions"].lower()


def test_load_skill_missing(tmp_path):
    loader = SkillLoader([tmp_path])
    ok, result = execute("load_skill", {"name": "nope"}, skills=loader)
    assert ok is False
    assert "nope" in result["error"]


def test_catalog_text_lists_builtin():
    text = catalog_text(SkillLoader([BUILTIN_SKILLS]))
    assert "weekly-sourcing" in text
    assert "load_skill" in text


def test_product_validation_skills_are_discoverable_and_outcome_bound():
    loader = SkillLoader([BUILTIN_SKILLS])

    outreach = loader.get("outreach-campaign")
    pitch = loader.get("company-pitch-package")

    assert outreach is not None
    assert pitch is not None
    assert "sent" in outreach.instructions.lower()
    assert "send receipt" in outreach.instructions.lower()
    assert "editable" in pitch.instructions.lower()
    assert "never claim the deck was edited" in pitch.instructions.lower()


def test_product_validation_skills_declare_real_tool_boundaries():
    loader = SkillLoader([BUILTIN_SKILLS])
    outreach = loader.get("outreach-campaign")
    pitch = loader.get("company-pitch-package")

    assert outreach is not None
    assert pitch is not None
    assert {"apollo_search_people", "gmail_draft", "gmail_send"} <= set(
        outreach.allowed_tools
    )
    assert {"drive_search", "drive_read", "web_search"} <= set(
        pitch.allowed_tools
    )
    assert "gmail_send" not in pitch.allowed_tools
