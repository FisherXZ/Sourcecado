"""Async lease heartbeat for provider transports and blocking tool execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coworker.agent_run_execution import AgentRunExecution


class AgentRunHeartbeatError(RuntimeError):
    """Lease renewal failed while external work was still in flight."""


class AgentRunHeartbeat:
    def __init__(self, execution: AgentRunExecution) -> None:
        self._execution = execution
        self._interval = execution.lease_seconds / 3
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._failure: AgentRunHeartbeatError | None = None

    async def __aenter__(self) -> AgentRunHeartbeat:
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self._stop.set()
        if self._task is not None:
            await self._task
        if self._failure is not None:
            raise self._failure
        return False

    def raise_if_failed(self) -> None:
        if self._failure is not None:
            raise self._failure

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval
                )
                return
            except TimeoutError:
                pass
            try:
                self._execution.renew()
            except Exception as exc:
                try:
                    self._execution.reconcile_expired_boundary()
                except Exception:
                    pass
                self._failure = AgentRunHeartbeatError(
                    "Agent Run lease heartbeat lost execution authority"
                )
                self._failure.__cause__ = exc
                return
