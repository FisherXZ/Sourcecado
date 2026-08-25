"""Scheduler tick — due jobs fire once, overlap skipped.

Copied policy from OpenWorker: run-once-catch-up, skip-on-overlap.
Run now fires one job without consuming the weekly slot.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from coworker.inbox import Inbox
from coworker.store import ConversationStore

TZ = ZoneInfo("America/Los_Angeles")


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def _as_la(stamp: str | datetime) -> datetime:
    if isinstance(stamp, datetime):
        dt = stamp
    else:
        dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def next_monday_0900(now: str | datetime) -> str:
    dt = _as_la(now)
    days = (7 - dt.weekday()) % 7
    if days == 0 and (dt.hour, dt.minute, dt.second) >= (9, 0, 0):
        days = 7
    nxt = (dt + timedelta(days=days)).replace(hour=9, minute=0, second=0, microsecond=0)
    return nxt.isoformat()


class Scheduler:
    def __init__(self, store: ConversationStore, inbox: Inbox) -> None:
        self.store = store
        self.inbox = inbox
        self.job_runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._running: set[int] = set()
        self.errors: list[str] = []

    def tick(
        self,
        now: str | None = None,
        *,
        runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        stamp = now or now_iso()
        ran: list[dict[str, Any]] = []
        for job in self.store.due_jobs(stamp):
            recorded = self._fire(job, runner=runner, advance=True, now=stamp)
            if recorded is not None:
                ran.append(recorded)
        return ran

    def run_job(
        self,
        job_id: int,
        *,
        runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        recorded = self._fire(job, runner=runner, advance=False, now=now_iso())
        if recorded is None:
            raise RuntimeError("already running")
        return recorded

    def _fire(
        self,
        job: dict[str, Any],
        *,
        runner: Callable[[dict[str, Any]], dict[str, Any]] | None,
        advance: bool,
        now: str,
    ) -> dict[str, Any] | None:
        job_id = int(job["id"])
        if job_id in self._running:
            return None
        self._running.add(job_id)
        try:
            fn = runner or self.job_runner
            try:
                result = fn(job) if fn else {"status": "ok", "result": "tick"}
            except Exception as exc:
                self.errors.append(str(exc))
                result = {"status": "error", "result": str(exc)}
            status = str(result.get("status") or "ok")
            detail = str(result.get("result") or result.get("text") or "")
            recorded = self.store.record_run(job_id, status, detail)
            if advance:
                self.store.set_job_next_run(job_id, next_monday_0900(now))
            return recorded
        finally:
            self._running.discard(job_id)
