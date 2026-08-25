"""Inbox — parked approvals. First resolve wins.

Copied shape from OpenWorker inbox: pending → resolved once. Slice 14 uses this
when the live card is gone; the WS card still works in parallel.
"""

from __future__ import annotations

import secrets
from typing import Any

from coworker.store import ConversationStore


class Inbox:
    def __init__(self, store: ConversationStore) -> None:
        self.store = store

    def park(self, name: str, arguments: dict[str, Any], *, item_id: str | None = None) -> dict[str, Any]:
        return self.store.park_inbox(item_id or secrets.token_hex(8), name, arguments)

    def pending(self) -> list[dict[str, Any]]:
        return self.store.list_inbox(pending_only=True)

    def get(self, item_id: str) -> dict[str, Any] | None:
        return self.store.get_inbox(item_id)

    def resolve(self, item_id: str, decision: str) -> dict[str, Any] | None:
        if decision not in ("allow", "deny"):
            return None
        return self.store.resolve_inbox(item_id, decision)
