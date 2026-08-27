"""Sourcecado permission decisions for connector and local tools."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from coworker.workspace_runtime import WORKSPACE_TOOL_NAMES

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
        "calendar_list",
        "web_search",
        "board_get",
        "board_query",
        "board_upsert",
        "board_mutate",
    }
)
ASK = frozenset(
    {
        "gmail_draft",
        "gmail_send",
        "apollo_enrich_contact",
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
        "calendar_list",
        "apollo_search_people",
        "board_get",
        "board_query",
    }
)
_MCP_WRITE = re.compile(r"write|create|delete|update", re.I)


@dataclass
class Decision:
    allowed: bool
    needs_user: bool = False
    reason: str = ""
    risk_class: str | None = None
    execution_target: str | None = None
    command_fingerprint: str | None = None


def decide(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    workspace_runtime: Any = None,
    actor: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> Decision:
    if name in WORKSPACE_TOOL_NAMES:
        if workspace_runtime is None:
            return Decision(False, False, "workspace runtime is unavailable")
        outcome = workspace_runtime.decide_tool(
            name,
            arguments,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
        )
        return Decision(
            outcome.allowed,
            outcome.needs_approval,
            outcome.reason,
            outcome.risk_class.value,
            outcome.execution_target,
            outcome.command_fingerprint,
        )
    if name.startswith("mcp__"):
        last = name.rsplit("__", 1)[-1]
        if _MCP_WRITE.search(last):
            return Decision(False, False, "granola writes are out of v1")
        return Decision(True, False, "auto")
    if name in AUTO:
        return Decision(True, False, "auto")
    if name in ASK:
        return Decision(False, True, "consequential: waits for Allow or Deny")
    return Decision(False, False, f"unknown tool {name}")
