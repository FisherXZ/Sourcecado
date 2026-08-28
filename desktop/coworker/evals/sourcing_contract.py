"""The real sourcing prompt and effective tool catalog, as an eval variant.

Behavioral evaluations must observe the shipped prompt and permission policy,
never a paraphrase of them. Every sourcing variant therefore assembles its
system prompt from ``SOURCING_DIRECTOR_V1`` and draws its tool catalog from
``effective_tool_catalog``. Nothing here asserts on prompt wording: the text is
carried and fingerprinted, not inspected.
"""

from __future__ import annotations

from coworker.effective_tools import (
    EffectiveToolCatalog,
    ToolAvailability,
    effective_tool_catalog,
)
from coworker.evals.models import EvalVariant, RunBudget
from coworker.persona import load_persona
from coworker.prompt_contract import (
    SOURCING_DIRECTOR_V1,
    AssembledSystemPrompt,
    assemble_system_prompt,
)
from coworker.tools import OPENAI_TOOLS

SOURCING_MODEL = "sourcecado-scenario-v1"


def sourcing_system_prompt() -> AssembledSystemPrompt:
    """The runtime system prompt a sourcing session starts from."""
    return assemble_system_prompt(definition=SOURCING_DIRECTOR_V1)


def sourcing_tool_catalog(
    availability: ToolAvailability | None = None,
) -> EffectiveToolCatalog:
    """The catalog the sourcing persona actually gets for a given availability.

    ``workspace_runtime=None`` mirrors a session with no granted directory, so
    the catalog holds connector and person-file tools only.
    """
    return effective_tool_catalog(
        persona=load_persona("sourcing"),
        registered_schemas=OPENAI_TOOLS,
        workspace_runtime=None,
        availability=availability or ToolAvailability(),
    )


def sourcing_variant(
    *,
    name: str,
    tools: tuple[str, ...],
    availability: ToolAvailability | None = None,
    run_budget: RunBudget | None = None,
) -> EvalVariant:
    """Build a variant whose tools are a real subset of the effective catalog."""
    catalog = sourcing_tool_catalog(availability)
    granted = set(catalog.names)
    missing = [tool for tool in tools if tool not in granted]
    if missing:
        raise ValueError(
            "scenario tools are outside the effective sourcing catalog: "
            + ", ".join(sorted(missing))
        )
    assembled = sourcing_system_prompt()
    return EvalVariant(
        name=name,
        prompt_version=assembled.diagnostics.prompt_version,
        system_prompt=assembled.text,
        tool_catalog=tools,
        provider="fake",
        model=SOURCING_MODEL,
        run_budget=run_budget or RunBudget(max_provider_calls=10),
    )
