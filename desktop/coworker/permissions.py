"""Permission engine — allow / deny / ask for each tool.

Copied shape from OpenWorker `coworker/permissions.py`: the engine only *decides*;
the loop waits when `needs_user`. Slice 4: `now` auto-runs, drafts ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
    }
)
ASK = frozenset(
    {
        "gmail_draft",
        "gmail_send",
        "apollo_enrich_contact",
        "calendar_create",
        "calendar_update",
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
    }
)
_MCP_WRITE = re.compile(r"write|create|delete|update", re.I)


@dataclass
class Decision:
    allowed: bool
    needs_user: bool = False
    reason: str = ""


def decide(name: str) -> Decision:
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
