"""Sourcecado permission decisions for connector and local tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from coworker.crm_tools import CRM_READ_TOOLS, CRM_TOOL_NAMES
from coworker.consequential_policy import supported_mcp_read

AUTO = frozenset(
    {
        "now",
        "remember",
        "memory_update",
        "memory_forget",
        "apollo_search_people",
        "people_keep",
        "load_skill",
        "gmail_search",
        "gmail_read",
        "drive_search",
        "drive_list_folder",
        "drive_read",
        "drive_index_query",
        "calendar_list",
        "web_search",
        "board_get",
        "board_query",
        "board_search",
        "board_upsert",
        "board_mutate",
        *CRM_TOOL_NAMES,
        "monid_catalog",
        "monid_inspect",
        "monid_read_result",
        "monid_prepare_research",
    }
)
ASK = frozenset(
    {
        "gmail_draft",
        "gmail_send",
        "apollo_enrich_contact",
        "monid_research",
        "calendar_create",
        "calendar_update",
        "board_delete",
    }
)
# Read-only members of AUTO that can re-run without a second external effect.
# turn.py derives its retry allowlist from this; keep it a subset of AUTO so a
# retry that skips a fresh approval can never cover an ASK tool.
RETRY_SAFE = frozenset(
    {
        "now",
        "load_skill",
        "gmail_search",
        "gmail_read",
        "drive_search",
        "drive_list_folder",
        "drive_read",
        "drive_index_query",
        "calendar_list",
        "apollo_search_people",
        "board_get",
        "board_query",
        *CRM_READ_TOOLS,
        "monid_catalog",
        "monid_inspect",
        "monid_read_result",
        "board_search",
    }
)
_MCP_WRITE = re.compile(r"write|create|delete|update", re.I)


@dataclass
class Decision:
    allowed: bool
    needs_user: bool = False
    reason: str = ""


def model_approval_class(name: str) -> str | None:
    """Content-free schema-level guidance derived from runtime policy.

    ``conditional`` means the concrete arguments, grant, or target determine
    whether the call is automatic or approval-gated.
    """
    if name in AUTO:
        return "auto"
    if name in ASK:
        return "approval_required"
    if name.startswith("mcp__"):
        decision = decide(name)
        if decision.allowed:
            return "auto"
        if decision.needs_user:
            return "approval_required"
        return None
    return None


def decide(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> Decision:
    if name.startswith("mcp__"):
        last = name.rsplit("__", 1)[-1]
        if _MCP_WRITE.search(last):
            return Decision(False, False, "granola writes are out of v1")
        if not supported_mcp_read(name):
            return Decision(False, False, "external actions require a Sourcecado-owned reviewed approval")
        return Decision(True, False, "auto")
    if name in AUTO:
        return Decision(True, False, "auto")
    if name in ASK:
        return Decision(False, True, "consequential: waits for Allow or Deny")
    return Decision(False, False, f"unknown tool {name}")
