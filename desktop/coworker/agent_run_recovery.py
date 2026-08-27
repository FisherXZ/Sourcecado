"""Application-start recovery for explicitly resumable Agent Runs."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from coworker.agent_run_resume import resume_turn
from coworker.automation.scheduler import RECEIPT_STATUSES, SCHEDULE_RUN_STATUSES
from coworker.events import TurnIdentity
from coworker.turn import RunControl, RunCoordinator


class AgentRunRecoveryService:
    """Claim and resume safe interrupted runs without blocking app startup."""

    def __init__(
        self,
        *,
        store: Any,
        provider: Any,
        coordinator: RunCoordinator,
        dependencies: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._provider = provider
        self._coordinator = coordinator
        self._dependencies = dependencies
        self._emit = emit
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(self._tasks)

    async def start(self) -> int:
        launched = 0
        for run in self._store.agent_runs.list_resumable_runs():
            continuation = run.get("continuation") or {}
            cursor = continuation.get("cursor") or {}
            # Approval/external waits require their explicit interaction path.
            if cursor.get("phase") not in {"model_ready", "tools_ready"}:
                continue
            identity_data = continuation.get("identity") or {}
            values = (
                run.get("session_id"),
                run.get("run_id"),
                identity_data.get("message_id"),
                identity_data.get("part_id"),
            )
            if not all(isinstance(value, str) and value for value in values):
                continue
            identity = TurnIdentity(
                session_id=values[0],
                run_id=values[1],
                message_id=values[2],
                part_id=values[3],
            )
            control = RunControl(identity)
            if not self._coordinator.register(control):
                continue
            task = asyncio.create_task(self._resume(identity.run_id, control))
            self._tasks.add(task)
            task.add_done_callback(
                lambda done, owned=control: self._finished(done, owned)
            )
            launched += 1
        await asyncio.sleep(0)
        return launched

    async def wait(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _resume(self, run_id: str, control: RunControl) -> dict[str, Any]:
        result = await resume_turn(
            run_id=run_id,
            store=self._store,
            provider=self._provider,
            dependencies=self._dependencies,
            emit=self._emit,
            control=control,
        )
        self._project_schedule_receipt(run_id, result)
        return result

    def _project_schedule_receipt(
        self, run_id: str, result: dict[str, Any]
    ) -> None:
        receipt = self._store.get_schedule_run_for_agent(run_id)
        if receipt is None:
            return
        raw_status = str(result.get("status") or "")
        status = RECEIPT_STATUSES.get(raw_status, raw_status)
        if status not in SCHEDULE_RUN_STATUSES or status == "running":
            return
        detail = str(result.get("text") or "")
        run = self._store.get_agent_run(run_id) or {}
        self._store.finish_run(
            int(receipt["id"]),
            status=status,
            result=detail,
            summary=detail or "Routine resumed.",
            artifacts=list(run.get("artifact_refs") or []),
            duration_ms=int(receipt.get("duration_ms") or 0),
            finished_at=datetime.now(UTC).isoformat(),
            waiting_approval_count=sum(
                1
                for item in self._dependencies["inbox"].pending()
                if item.get("session_id") == receipt.get("session_id")
            )
            if "inbox" in self._dependencies
            else 0,
            agent_run_id=run_id,
        )

    def _finished(self, task: asyncio.Task[Any], control: RunControl) -> None:
        self._tasks.discard(task)
        control.abandon()
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logging.getLogger("coworker.agent_run_recovery").error(
                "startup Agent Run recovery crashed",
                exc_info=(type(error), error, error.__traceback__),
            )
