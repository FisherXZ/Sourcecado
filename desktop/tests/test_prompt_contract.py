import importlib.util
from dataclasses import asdict
from hashlib import sha256

import pytest

from coworker.prompt_contract import PromptSection, assemble_prompt


def test_versioned_prompt_contract_module_exists():
    assert importlib.util.find_spec("coworker.prompt_contract") is not None


def test_prompt_sections_render_once_in_declared_order():
    assembled = assemble_prompt(
        version="sourcing-director-v1-proposal",
        sections=(
            PromptSection("identity", "Identity and principal", "First section."),
            PromptSection("evidence", "Evidence", "Second section."),
        ),
        budget_chars=200,
    )

    assert assembled.text == (
        "## Identity and principal\n\nFirst section.\n\n"
        "## Evidence\n\nSecond section."
    )
    assert assembled.diagnostics.section_ids == ("identity", "evidence")


def test_prompt_diagnostics_identify_the_assembly_without_storing_prose():
    assembled = assemble_prompt(
        version="sourcing-director-v1-proposal",
        sections=(PromptSection("identity", "Identity", "Private prompt prose."),),
        budget_chars=100,
    )

    fields = asdict(assembled.diagnostics)
    assert fields == {
        "version": "sourcing-director-v1-proposal",
        "section_ids": ("identity",),
        "chars": len(assembled.text),
        "sha256": sha256(assembled.text.encode("utf-8")).hexdigest(),
        "budget_chars": 100,
        "remaining_chars": 100 - len(assembled.text),
    }
    assert "Private prompt prose." not in repr(fields)


def test_prompt_assembly_fails_closed_when_static_budget_is_exceeded():
    with pytest.raises(ValueError, match="exceeds the 20-character budget"):
        assemble_prompt(
            version="sourcing-director-v1-proposal",
            sections=(PromptSection("identity", "Identity", "Long prompt body."),),
            budget_chars=20,
        )


def test_prompt_assembly_rejects_duplicate_section_identity():
    with pytest.raises(ValueError, match="duplicate prompt section id: identity"):
        assemble_prompt(
            version="sourcing-director-v1-proposal",
            sections=(
                PromptSection("identity", "Identity", "First."),
                PromptSection("identity", "Identity again", "Second."),
            ),
            budget_chars=200,
        )
