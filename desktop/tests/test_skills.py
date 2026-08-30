from coworker.skills import BUILTIN_SKILLS, SkillLoader, catalog_text, parse_skill
from coworker.permissions import decide
from coworker.store import ConversationStore
from coworker.tools import OPENAI_TOOLS, execute


def _weekly_sourcing_skill():
    md = BUILTIN_SKILLS / "weekly-sourcing" / "SKILL.md"
    return parse_skill(md)


def test_builtin_weekly_sourcing_skill_uses_the_current_person_centered_job():
    skill = _weekly_sourcing_skill()

    assert skill.name == "weekly-sourcing"
    for term in (
        "Target",
        "Person",
        "Person File",
        "Sequence",
        "Living Brief",
        "Outreach Outcome",
        "Source Reference",
        "Knowledge Gap",
    ):
        assert term in skill.instructions
    assert "Contact" not in skill.instructions
    assert "Sourcing Lead" not in skill.instructions
    assert "organization record" not in skill.instructions.lower()
    assert "company is context" in skill.instructions.lower()


def test_builtin_weekly_sourcing_skill_requires_evidence_and_names_uncertainty():
    instructions = _weekly_sourcing_skill().instructions.lower()

    assert "why-now" in instructions
    assert "source reference" in instructions
    assert "missing" in instructions
    assert "conflicting" in instructions
    assert "knowledge gap" in instructions


def test_builtin_weekly_sourcing_skill_preserves_manual_outreach_authority():
    instructions = _weekly_sourcing_skill().instructions.lower()

    assert "draft" in instructions
    assert "enrichment" in instructions
    assert "credit" in instructions
    assert "one person at a time" in instructions
    assert "gmail_send" in instructions
    assert "approved send" in instructions
    assert "per-message" in instructions
    assert "auto-send" in instructions


def test_load_skill_returns_full_body(tmp_path):
    loader = SkillLoader([BUILTIN_SKILLS])
    ok, result = execute("load_skill", {"name": "weekly-sourcing"}, skills=loader)
    assert ok is True
    assert result["name"] == "weekly-sourcing"
    assert "shortlist" in result["instructions"].lower() or "why-now" in result["instructions"].lower()


def test_loading_weekly_sourcing_cannot_broaden_tools_or_permissions():
    loader = SkillLoader([BUILTIN_SKILLS])
    tool_names_before = tuple(
        schema["function"]["name"] for schema in OPENAI_TOOLS
    )
    decisions_before = {
        name: decide(name)
        for name in (
            "load_skill",
            "gmail_draft",
            "apollo_enrich_contact",
            "gmail_send",
            "unlisted_skill_tool",
        )
    }

    ok, result = execute("load_skill", {"name": "weekly-sourcing"}, skills=loader)

    assert ok is True
    assert set(result) == {"name", "description", "instructions"}
    assert loader.get("weekly-sourcing").allowed_tools == []
    assert tuple(schema["function"]["name"] for schema in OPENAI_TOOLS) == tool_names_before
    assert {
        name: decide(name) for name in decisions_before
    } == decisions_before
    assert decide("gmail_send").needs_user is True
    assert decide("apollo_enrich_contact").needs_user is True
    assert decide("unlisted_skill_tool").allowed is False


def test_load_skill_missing(tmp_path):
    loader = SkillLoader([tmp_path])
    ok, result = execute("load_skill", {"name": "nope"}, skills=loader)
    assert ok is False
    assert "nope" in result["error"]


def test_catalog_text_lists_builtin():
    text = catalog_text(SkillLoader([BUILTIN_SKILLS]))
    assert "weekly-sourcing" in text
    assert "load_skill" in text


def test_catalog_projects_safe_inspectable_metadata_without_private_internals(tmp_path):
    skill_dir = tmp_path / "candidate-research"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: candidate-research
description: Build a source-backed candidate brief.
use-when: Use when the director asks to understand one candidate.
allowed-tools: shell_exec, gmail_send
---

1. Read the Person File.
2. Never send without approval.
3. Do not reveal /Users/operator/private/notes.md.
4. API_KEY=sk-live-PLANTED-SENTINEL
""",
        encoding="utf-8",
    )

    catalog = SkillLoader([tmp_path]).catalog()

    assert catalog == [
        {
            "name": "candidate-research",
            "purpose": "Build a source-backed candidate brief.",
            "use_when": "Use when the director asks to understand one candidate.",
            "source": "workspace",
            "status": "ready",
            "instructions": (
                "1. Read the Person File.\n"
                "2. Never send without approval.\n"
                "3. Do not reveal [REDACTED PATH].\n"
                "4. API_KEY=[REDACTED]"
            ),
        }
    ]
    payload = str(catalog)
    assert "/Users/operator" not in payload
    assert "sk-live-PLANTED-SENTINEL" not in payload
    assert "allowed_tools" not in payload
    assert "shell_exec" not in payload
    assert "gmail_send" not in payload


def test_builtin_catalog_names_safe_source_status_and_activation_guidance():
    [skill] = SkillLoader([BUILTIN_SKILLS]).catalog()

    assert skill["name"] == "weekly-sourcing"
    assert skill["source"] == "builtin"
    assert skill["status"] == "ready"
    assert skill["use_when"] == (
        "The director asks for a weekly sourcing check-in, who to work next, "
        "or a prioritized why-now review."
    )
    assert "Treat each Person as the unit of work" in skill["instructions"]
