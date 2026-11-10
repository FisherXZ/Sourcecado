"""Validated per-run model-facing tool catalogs."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from coworker.persona import ManifestError, Persona
from coworker.permissions import model_approval_class


class ToolCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolAvailability:
    gmail: bool = True
    drive: bool = True
    calendar: bool = True
    apollo: bool = True
    web: bool = True


@dataclass(frozen=True, slots=True)
class ToolCapability:
    name: str
    approval_class: str


@dataclass(frozen=True, slots=True)
class EffectiveToolCatalog:
    schemas: tuple[dict[str, Any], ...]
    capabilities: tuple[ToolCapability, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.capabilities)

    def diagnostics(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {"name": item.name, "approval_class": item.approval_class}
            for item in self.capabilities
        )


_GMAIL = frozenset({"gmail_search", "gmail_read", "gmail_draft", "gmail_send", "crm_refresh_mail"})
_DRIVE = frozenset({"drive_search", "drive_list_folder", "drive_read"})
_CALENDAR = frozenset(
    {"calendar_list", "calendar_create", "calendar_update"}
)
_APOLLO = frozenset({"apollo_search_people", "apollo_enrich_contact"})
_WEB = frozenset({"web_search"})
_BUDDY_BASE = frozenset(
    {
        "now",
        "remember",
        "memory_update",
        "memory_forget",
        "gmail_draft",
    }
)


def _schema_name(schema: object) -> str:
    if not isinstance(schema, dict):
        raise ToolCatalogError("registered tool schema must be an object")
    function = schema.get("function")
    if not isinstance(function, dict):
        raise ToolCatalogError("registered tool schema requires function metadata")
    name = str(function.get("name") or "")
    if not name:
        raise ToolCatalogError("registered tool schema requires a name")
    return name


def _registry(
    schemas: Iterable[dict[str, Any]],
) -> tuple[tuple[str, dict[str, Any]], ...]:
    rows: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for schema in schemas:
        name = _schema_name(schema)
        if name in seen:
            raise ToolCatalogError(f"duplicate registered tool: {name}")
        seen.add(name)
        rows.append((name, schema))
    return tuple(rows)


def _availability_names(
    registry_names: set[str],
    availability: ToolAvailability,
) -> set[str]:
    allowed = set(registry_names)
    for enabled, group in (
        (availability.gmail, _GMAIL),
        (availability.drive, _DRIVE),
        (availability.calendar, _CALENDAR),
        (availability.apollo, _APOLLO),
        (availability.web, _WEB),
    ):
        if not enabled:
            allowed.difference_update(group)
    return allowed


def effective_tool_catalog(
    *,
    persona: Persona,
    registered_schemas: Iterable[dict[str, Any]],
    availability: ToolAvailability,
) -> EffectiveToolCatalog:
    registry = _registry(registered_schemas)
    registry_names = {name for name, _schema in registry}
    unknown = [name for name in persona.tools if name not in registry_names]
    if unknown:
        raise ManifestError(f"unknown persona tool: {unknown[0]}")

    if persona.id == "sourcing":
        persona_base = set(registry_names)
    elif persona.id == "buddy":
        missing_policy_names = sorted(_BUDDY_BASE - registry_names)
        if missing_policy_names:
            raise ToolCatalogError(
                "buddy policy references unknown tool: " + missing_policy_names[0]
            )
        persona_base = set(_BUDDY_BASE & registry_names)
    else:
        persona_base = set()
    if persona.tools:
        persona_base.intersection_update(persona.tools)

    effective_names = persona_base & _availability_names(
        registry_names, availability
    )
    schemas: list[dict[str, Any]] = []
    capabilities: list[ToolCapability] = []
    for name, raw_schema in registry:
        if name not in effective_names:
            continue
        approval_class = model_approval_class(name)
        if approval_class is None:
            if name.startswith("mcp__"):
                continue
            raise ToolCatalogError(
                f"registered tool {name} has no permission policy"
            )
        schema = deepcopy(raw_schema)
        function = schema["function"]
        description = str(function.get("description") or name).rstrip()
        function["description"] = (
            f"{description} Runtime approval: {approval_class}."
        )
        schemas.append(schema)
        capabilities.append(ToolCapability(name, approval_class))
    return EffectiveToolCatalog(tuple(schemas), tuple(capabilities))
