from coworker.skills import BUILTIN_SKILLS, SkillLoader, catalog_text, parse_skill
from coworker.store import ConversationStore
from coworker.tools import execute


def test_builtin_weekly_sourcing_skill():
    md = BUILTIN_SKILLS / "weekly-sourcing" / "SKILL.md"
    skill = parse_skill(md)
    assert skill.name == "weekly-sourcing"
    assert "why-now" in skill.description or "why-now" in skill.instructions
    assert "Outreach Drafts are for review" in skill.instructions
    assert "per-message Approved Send" in skill.instructions


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
