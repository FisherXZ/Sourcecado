"""Scheduler tick — due jobs fire once, overlap skipped.

Copied policy from OpenWorker: run-once-catch-up, skip-on-overlap.
Run now fires one job without consuming the weekly slot.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from coworker.inbox import Inbox
from coworker.store import ConversationStore

TZ = ZoneInfo("America/Los_Angeles")
ROUTINE_TEMPLATES = (
    {
        "id": "weekly_sourcing_review",
        "name": "Weekly sourcing review",
        "description": "Review priorities, source context, and the next best sourcing work.",
        "cadences": ["weekly_monday_0900"],
        "default_prompt": "Review the highest-priority sourcing work for this week.",
    },
)
SUPPORTED_CADENCES = {"weekly_monday_0900": "0 9 * * 1"}
# Maps every run_turn status onto the shared run-status contract. "stopped"
# from a scheduled run can only mean the step budget ran out (nothing can
# cancel a scheduled turn): real but incomplete work, so it reads as partial.
RECEIPT_STATUSES = {
    "ok": "success",
    "error": "failed",
    "waiting": "waiting_approval",
    "partial": "partial",
    "stopped": "partial",
}
# The full vocabulary the runs table may carry, shared with the client.
# "interrupted" is written by the store's restart reconciler.
SCHEDULE_RUN_STATUSES = frozenset(
    {"running", "success", "failed", "waiting_approval", "partial", "interrupted"}
)


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


def _artifact_metadata(values: Any) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        external_url = str(raw["external_url"]) if raw.get("external_url") else None
        if external_url and urlparse(external_url).scheme not in {"http", "https"}:
            external_url = None
        artifacts.append(
            {
                "id": str(raw.get("id") or "artifact"),
                "artifact_type": str(
                    raw.get("artifact_type") or raw.get("type") or "artifact"
                ),
                "title": str(raw.get("title") or "Generated artifact"),
                "external_url": external_url,
            }
        )
    return artifacts


class Scheduler:
    def __init__(self, store: ConversationStore, inbox: Inbox) -> None:
        self.store = store
        self.inbox = inbox
        self.job_runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        self._running: set[int] = set()
        self._running_lock = threading.Lock()
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
        # The tick thread and "Run now" requests race here; take the slot
        # atomically so one job never runs twice concurrently.
        with self._running_lock:
            if job_id in self._running:
                return None
            self._running.add(job_id)
        session_id = f"sched-{job_id}"
        event_offset = len(self.store.load_events(session_id))
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        running = self.store.start_run(
            job_id, session_id=session_id, started_at=started_at
        )
        try:
            fn = runner or self.job_runner
            failed_before_result = False
            try:
                result = fn(job) if fn else {"status": "ok", "result": "tick"}
            except Exception as exc:
                self.errors.append(str(exc))
                failed_before_result = True
                result = {
                    "status": "error",
                    "result": "The routine failed before it could finish.",
                    "summary": "The routine failed before it could finish.",
                }
            raw_status = str(result.get("status") or "ok")
            status = RECEIPT_STATUSES.get(raw_status, raw_status)
            detail = str(result.get("result") or result.get("text") or "")
            summary = str(result.get("summary") or detail or "Routine finished.")
            if failed_before_result:
                detail = "The routine failed before it could finish."
                summary = detail
            artifacts = _artifact_metadata(result.get("artifacts"))
            event_artifacts = _artifact_metadata(
                [
                    artifact
                    for event in self.store.load_events(session_id)[event_offset:]
                    for artifact in event.get("artifacts") or []
                    if isinstance(event, dict)
                ]
            )
            artifacts_by_id = {artifact["id"]: artifact for artifact in artifacts}
            for artifact in event_artifacts:
                artifacts_by_id.setdefault(artifact["id"], artifact)
            artifacts = list(artifacts_by_id.values())
            waiting_approval_count = sum(
                1
                for item in self.inbox.pending()
                if item.get("session_id") == session_id
            )
            recorded = self.store.finish_run(
                int(running["id"]),
                status=status,
                result=detail,
                summary=summary,
                artifacts=artifacts,
                duration_ms=int((time.perf_counter() - started) * 1000),
                finished_at=datetime.now(UTC).isoformat(),
                waiting_approval_count=waiting_approval_count,
            )
            if advance:
                self.store.set_job_next_run(job_id, next_monday_0900(now))
            return recorded
        finally:
            with self._running_lock:
                self._running.discard(job_id)
