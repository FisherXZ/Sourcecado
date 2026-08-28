from pathlib import Path

import json

import pytest


def test_effective_tool_catalog_module_exists():
    module = Path(__file__).parents[1] / "coworker" / "effective_tools.py"

    assert module.is_file()


def test_sourcing_catalog_filters_unavailable_connectors_and_ungranted_workspace(
    tmp_path,
):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    runtime = WorkspaceRuntime(tmp_path / "state")
    try:
        catalog = effective_tool_catalog(
            persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(
                gmail=False,
                drive=False,
                calendar=False,
                apollo=False,
                web=False,
            ),
        )
    finally:
        runtime.close()

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
    assert "request_directory" in catalog.names
    assert not any(name.startswith("fs_") for name in catalog.names)
    assert not any(name.startswith("shell_") for name in catalog.names)


def test_permission_classes_and_prompt_facing_schema_annotations_share_one_catalog(
    tmp_path,
):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    runtime = WorkspaceRuntime(tmp_path / "state")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime.add_grant(
        workspace,
        label="Workspace",
        access="read_write",
        allow_shell=True,
    )
    try:
        catalog = effective_tool_catalog(
            persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
    finally:
        runtime.close()

    classes = {item.name: item.approval_class for item in catalog.capabilities}
    assert classes["now"] == "auto"
    assert classes["gmail_draft"] == "approval_required"
    assert classes["fs_read"] == "auto"
    assert classes["shell_exec"] == "conditional"
    for schema in catalog.schemas:
        name = schema["function"]["name"]
        assert f"Runtime approval: {classes[name]}." in schema["function"][
            "description"
        ]


def test_workspace_contract_controls_read_write_and_shell_subsets(tmp_path):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    persona = Persona(id="sourcing", name="Sourcing", body="", tools=[])
    runtime = WorkspaceRuntime(tmp_path / "state")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    try:
        without_grant = effective_tool_catalog(
            persona=persona,
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
        read_only = runtime.add_grant(
            workspace, label="Read", access="read_only", allow_shell=False
        )
        with_read = effective_tool_catalog(
            persona=persona,
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
        runtime.update_grant(read_only["id"], access="read_write")
        with_write = effective_tool_catalog(
            persona=persona,
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
        runtime.update_grant(read_only["id"], allow_shell=True)
        with_shell = effective_tool_catalog(
            persona=persona,
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
    finally:
        runtime.close()

    assert "fs_read" not in without_grant.names
    assert "fs_read" in with_read.names
    assert "fs_write" not in with_read.names
    assert "fs_write" in with_write.names
    assert "shell_exec" not in with_write.names
    assert {"shell_exec", "shell_poll", "shell_kill"} <= set(with_shell.names)


def test_persona_declarations_can_narrow_but_never_broaden_runtime_policy(tmp_path):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    runtime = WorkspaceRuntime(tmp_path / "state")
    try:
        buddy = effective_tool_catalog(
            persona=Persona(
                id="buddy",
                name="Buddy",
                body="I claim Apollo and shell access.",
                tools=["apollo_search_people"],
            ),
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
        narrowed = effective_tool_catalog(
            persona=Persona(id="sourcing", name="Sourcing", body="", tools=["now"]),
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
    finally:
        runtime.close()

    assert "apollo_search_people" not in buddy.names
    assert narrowed.names == ("now",)


def test_unknown_or_misspelled_persona_declaration_fails_clearly(tmp_path):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import ManifestError, Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    runtime = WorkspaceRuntime(tmp_path / "state")
    try:
        with pytest.raises(ManifestError, match="unknown persona tool: gmail_sned"):
            effective_tool_catalog(
                persona=Persona(
                    id="sourcing",
                    name="Sourcing",
                    body="",
                    tools=["gmail_sned"],
                ),
                registered_schemas=OPENAI_TOOLS,
                workspace_runtime=runtime,
                availability=ToolAvailability(),
            )
    finally:
        runtime.close()


def test_registered_schema_without_permission_policy_fails_contract(tmp_path):
    from coworker.effective_tools import (
        ToolAvailability,
        ToolCatalogError,
        effective_tool_catalog,
    )
    from coworker.persona import Persona
    from coworker.workspace_runtime import WorkspaceRuntime

    runtime = WorkspaceRuntime(tmp_path / "state")
    try:
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
                workspace_runtime=runtime,
                availability=ToolAvailability(),
            )
    finally:
        runtime.close()


def test_buddy_subset_drift_fails_when_a_registered_tool_is_renamed(tmp_path):
    from coworker.effective_tools import (
        ToolAvailability,
        ToolCatalogError,
        effective_tool_catalog,
    )
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    schemas_without_now = tuple(
        schema
        for schema in OPENAI_TOOLS
        if schema["function"]["name"] != "now"
    )
    runtime = WorkspaceRuntime(tmp_path / "state")
    try:
        with pytest.raises(ToolCatalogError, match="buddy.*unknown tool: now"):
            effective_tool_catalog(
                persona=Persona(id="buddy", name="Buddy", body="", tools=[]),
                registered_schemas=schemas_without_now,
                workspace_runtime=runtime,
                availability=ToolAvailability(),
            )
    finally:
        runtime.close()


def test_diagnostics_expose_only_names_and_approval_classes(tmp_path, monkeypatch):
    from coworker.effective_tools import ToolAvailability, effective_tool_catalog
    from coworker.persona import Persona
    from coworker.tools import OPENAI_TOOLS
    from coworker.workspace_runtime import WorkspaceRuntime

    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-PLANTED-SECRET")
    runtime = WorkspaceRuntime(tmp_path / "state")
    try:
        catalog = effective_tool_catalog(
            persona=Persona(id="sourcing", name="Sourcing", body="", tools=[]),
            registered_schemas=OPENAI_TOOLS,
            workspace_runtime=runtime,
            availability=ToolAvailability(),
        )
    finally:
        runtime.close()

    diagnostics = catalog.diagnostics()
    assert diagnostics
    assert set(diagnostics[0]) == {"name", "approval_class"}
    encoded = json.dumps(diagnostics)
    assert "parameters" not in encoded
    assert "description" not in encoded
    assert "PLANTED-SECRET" not in encoded
