from pathlib import Path

import json

import pytest


def test_effective_tool_catalog_module_exists():
    module = Path(__file__).parents[1] / "coworker" / "effective_tools.py"

    assert module.is_file()


def test_sourcing_catalog_filters_unavailable_connectors():
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS

    catalog = effective_tool_catalog(
        persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
        registered_schemas=OPENAI_TOOLS,
        availability=ToolAvailability(
            gmail=False,
            drive=False,
            calendar=False,
            apollo=False,
            web=False,
        ),
    )

    assert {"now", "remember", "people_keep", "board_query"} <= set(catalog.names)
    assert not set(catalog.names).intersection(
        {
            "gmail_search",
            "gmail_draft",
            "drive_read",
            "calendar_list",
            "apollo_search_people",
            "apollo_enrich_contact",
            "web_search",
        }
    )


def test_no_catalog_offers_a_local_file_or_shell_tool():
    """A hosted server must not hand the model the host disk or a shell."""
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS

    registered = [schema["function"]["name"] for schema in OPENAI_TOOLS]
    for persona_id in ("sourcing", "buddy"):
        catalog = effective_tool_catalog(
            persona=Persona(id=persona_id, name=persona_id, body="", tools=[]),
            registered_schemas=OPENAI_TOOLS,
            availability=ToolAvailability(),
        )
        for names in (registered, catalog.names):
            assert not [n for n in names if n.startswith(("fs_", "shell_"))]
            assert "request_directory" not in names


def test_permission_classes_and_prompt_facing_schema_annotations_share_one_catalog():
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS

    catalog = effective_tool_catalog(
        persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
        registered_schemas=OPENAI_TOOLS,
        availability=ToolAvailability(),
    )

    classes = {item.name: item.approval_class for item in catalog.capabilities}
    assert classes["now"] == "auto"
    assert classes["gmail_draft"] == "approval_required"
    for schema in catalog.schemas:
        name = schema["function"]["name"]
        assert f"Runtime approval: {classes[name]}." in schema["function"][
            "description"
        ]


def test_persona_declarations_can_narrow_but_never_broaden_runtime_policy():
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS

    buddy = effective_tool_catalog(
        persona=Persona(
            id="buddy",
            name="Buddy",
            body="I claim Apollo and shell access.",
            tools=["apollo_search_people"],
        ),
        registered_schemas=OPENAI_TOOLS,
        availability=ToolAvailability(),
    )
    narrowed = effective_tool_catalog(
        persona=Persona(id="sourcing", name="Sourcing", body="", tools=["now"]),
        registered_schemas=OPENAI_TOOLS,
        availability=ToolAvailability(),
    )

    assert "apollo_search_people" not in buddy.names
    assert narrowed.names == ("now",)


def test_unknown_or_misspelled_persona_declaration_fails_clearly():
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import ManifestError, Persona
    from coworker.tools import OPENAI_TOOLS

    with pytest.raises(ManifestError, match="unknown persona tool: gmail_sned"):
        effective_tool_catalog(
            persona=Persona(
                id="sourcing",
                name="Sourcing",
                body="",
                tools=["gmail_sned"],
            ),
            registered_schemas=OPENAI_TOOLS,
            availability=ToolAvailability(),
        )


def test_registered_schema_without_permission_policy_fails_contract():
    from coworker.effective_tools import (
        ToolAvailability,
        ToolCatalogError,
        effective_tool_catalog,
    )
    from coworker.persona import Persona

    with pytest.raises(ToolCatalogError, match="no permission policy"):
        effective_tool_catalog(
            persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
            registered_schemas=(
                {
                    "type": "function",
                    "function": {
                        "name": "invented_tool",
                        "description": "not registered in policy",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ),
            availability=ToolAvailability(),
        )


def test_buddy_subset_drift_fails_when_a_registered_tool_is_renamed():
    from coworker.effective_tools import (
        ToolAvailability,
        ToolCatalogError,
        effective_tool_catalog,
    )
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS

    schemas_without_now = tuple(
        schema
        for schema in OPENAI_TOOLS
        if schema["function"]["name"] != "now"
    )
    with pytest.raises(ToolCatalogError, match="buddy.*unknown tool: now"):
        effective_tool_catalog(
            persona=Persona(id="buddy", name="Buddy", body="", tools=[]),
            registered_schemas=schemas_without_now,
            availability=ToolAvailability(),
        )


def test_diagnostics_expose_only_names_and_approval_classes(monkeypatch):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS

    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-PLANTED-SECRET")
    catalog = effective_tool_catalog(
        persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
        registered_schemas=OPENAI_TOOLS,
        availability=ToolAvailability(),
    )

    diagnostics = catalog.diagnostics()
    assert diagnostics
    assert set(diagnostics[0]) == {"name", "approval_class"}
    encoded = json.dumps(diagnostics)
    assert "parameters" not in encoded
    assert all(set(item) == {"name", "approval_class"} for item in diagnostics)
    assert "PLANTED-SECRET" not in encoded
