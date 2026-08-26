"""Inbox — parked approvals. First resolve wins.

Copied shape from OpenWorker inbox: pending → resolved once. Slice 14 uses this
when the live card is gone; the WS card still works in parallel.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from coworker.store import ConversationStore


@dataclass(frozen=True)
class ApprovalExecutionClaim:
    item: dict[str, Any]
    claimed: bool
    owned: bool
    decision_recorded: bool


class Inbox:
    def __init__(self, store: ConversationStore) -> None:
        self.store = store

    def park(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        item_id: str | None = None,
        reason: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        message_id: str | None = None,
        part_id: str | None = None,
        kind: str = "approval",
        recovery_command_id: str | None = None,
        original_call_id: str | None = None,
    ) -> dict[str, Any]:
        return self.store.park_inbox(
            item_id or secrets.token_hex(8),
            name,
            arguments,
            reason=reason,
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
            part_id=part_id,
            kind=kind,
            recovery_command_id=recovery_command_id,
            original_call_id=original_call_id,
        )

    def pending(self) -> list[dict[str, Any]]:
        return self.store.list_inbox(pending_only=True)

    def get(self, item_id: str) -> dict[str, Any] | None:
        return self.store.get_inbox(item_id)

    def resolve(
        self,
        item_id: str,
        decision: str,
        *,
        actor: str | None = None,
        scope: str = "once",
    ) -> dict[str, Any] | None:
        if decision not in ("allow", "deny"):
            return None
        return self.store.resolve_inbox(
            item_id, decision, actor=actor, scope=scope
        )

    def cancel(self, item_id: str) -> dict[str, Any] | None:
        return self.store.cancel_inbox(item_id)

    def decide_and_claim(
        self,
        item_id: str,
        decision: str,
        *,
        actor: str | None,
        scope: str,
        claimant: str,
    ) -> ApprovalExecutionClaim | None:
        outcome = self.store.decide_and_claim_inbox_execution(
            item_id,
            decision,
            actor=actor,
            scope=scope,
            claimant=claimant,
        )
        if outcome is None:
            return None
        return ApprovalExecutionClaim(
            item=outcome["item"],
            claimed=bool(outcome["claimed"]),
            owned=bool(outcome["owned"]),
            decision_recorded=bool(outcome["decision_recorded"]),
        )

    def complete_execution(
        self,
        item_id: str,
        *,
        claimant: str,
        ok: bool,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        return self.store.complete_inbox_execution(
            item_id,
            claimant=claimant,
            ok=ok,
            result=result,
        )

    async def wait_for_execution(
        self, item_id: str, *, poll_interval: float = 0.02
    ) -> dict[str, Any] | None:
        while True:
            item = self.get(item_id)
            if item is None:
                return None
            if item.get("execution_status") in {
                "succeeded",
                "failed",
                "not_run",
                "cancelled",
                "expired",
                "interrupted",
            }:
                return item
            await asyncio.sleep(poll_interval)

    @staticmethod
    def execution_outcome(item: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        result = item.get("execution_result")
        if not isinstance(result, dict):
            error = item.get("execution_error") or "approval execution has no result"
            result = {"error": str(error)}
        return item.get("execution_status") == "succeeded", result
