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

_WORKSPACE_AUTO = frozenset(
    {
        "fs_stat",
        "fs_list",
        "fs_find",
        "fs_search",
        "fs_read",
        "request_directory",
        "shell_poll",
        "shell_kill",
    }
)
_WORKSPACE_APPROVAL = frozenset({"fs_trash", "shell_write_stdin"})
_WORKSPACE_CONDITIONAL = WORKSPACE_TOOL_NAMES - _WORKSPACE_AUTO - _WORKSPACE_APPROVAL


@dataclass
class Decision:
    allowed: bool
    needs_user: bool = False
    reason: str = ""
    risk_class: str | None = None
    execution_target: str | None = None
    command_fingerprint: str | None = None


def model_approval_class(name: str) -> str | None:
    """Content-free schema-level guidance derived from runtime policy.

    ``conditional`` means the concrete arguments, grant, or target determine
    whether the call is automatic or approval-gated.
    """
    if name in AUTO:
        return "auto"
    if name in ASK:
        return "approval_required"
    if name in _WORKSPACE_AUTO:
        return "auto"
    if name in _WORKSPACE_APPROVAL:
        return "approval_required"
    if name in _WORKSPACE_CONDITIONAL:
        return "conditional"
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
