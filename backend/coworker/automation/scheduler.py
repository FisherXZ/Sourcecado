"""Sourcecado scheduler: run-once catch-up, skip overlap, preserve weekly slots."""

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
# Preset cadences in Los Angeles time. Weekdays use Python's numbering
# (Monday is 0).
CADENCES = {
    "weekly_monday_0900": {
        "label": "Every Monday at 9:00 AM",
        "cron": "0 9 * * 1",
        "weekdays": frozenset({0}),
    },
    "weekdays_0900": {
        "label": "Every weekday at 9:00 AM",
        "cron": "0 9 * * 1-5",
        "weekdays": frozenset({0, 1, 2, 3, 4}),
    },
    "daily_0900": {
        "label": "Every day at 9:00 AM",
        "cron": "0 9 * * *",
        "weekdays": frozenset(range(7)),
    },
}
SUPPORTED_CADENCES = {key: str(value["cron"]) for key, value in CADENCES.items()}
ROUTINE_TEMPLATES = (
    {
        "id": "weekly_sourcing_review",
        "name": "Weekly sourcing review",
        "description": "Review priorities, source context, and the next best sourcing work.",
        "cadences": list(CADENCES),
        "default_prompt": "Review the highest-priority sourcing work for this week.",
    },
)
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


def next_run_for(cadence: str, now: str | datetime) -> str:
    """The first 9:00 AM slot strictly after `now` on one of the cadence's days."""
    weekdays = CADENCES[cadence]["weekdays"]
    dt = _as_la(now)
    nxt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    if nxt <= dt:
        nxt += timedelta(days=1)
    while nxt.weekday() not in weekdays:
        nxt += timedelta(days=1)
    return nxt.isoformat()


def next_monday_0900(now: str | datetime) -> str:
    return next_run_for("weekly_monday_0900", now)


def cadence_id(job: dict[str, Any]) -> str:
    """The job's preset cadence. Rows saved before presets carry only a cron."""
    if job.get("cadence") in CADENCES:
        return str(job["cadence"])
    for key, value in CADENCES.items():
        if value["cron"] == job.get("cron"):
            return key
    return "weekly_monday_0900"


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

    def start_job(
        self,
        job_id: int,
        *,
        runner: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run now without waiting: return the running receipt, finish in a thread."""
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if not self._claim(job_id):
            raise RuntimeError("already running")
        try:
            started = self._begin(job_id)
        except Exception:
            self._release(job_id)
            raise

        def finish() -> None:
            try:
                self._complete(job, started, runner=runner, advance=False, now=now_iso())
            finally:
                self._release(job_id)

        threading.Thread(target=finish, daemon=True).start()
        return started["running"]

    def _claim(self, job_id: int) -> bool:
        # The tick thread and "Run now" requests race here; take the slot
        # atomically so one job never runs twice concurrently.
        with self._running_lock:
            if job_id in self._running:
                return False
            self._running.add(job_id)
            return True

    def _release(self, job_id: int) -> None:
        with self._running_lock:
            self._running.discard(job_id)

    def _fire(
        self,
        job: dict[str, Any],
        *,
        runner: Callable[[dict[str, Any]], dict[str, Any]] | None,
        advance: bool,
        now: str,
    ) -> dict[str, Any] | None:
        job_id = int(job["id"])
        if not self._claim(job_id):
            return None
        try:
            started = self._begin(job_id)
            return self._complete(job, started, runner=runner, advance=advance, now=now)
        finally:
            self._release(job_id)

    def _begin(self, job_id: int) -> dict[str, Any]:
        session_id = f"sched-{job_id}"
        event_offset = len(self.store.load_events(session_id))
        clock = time.perf_counter()
        running = self.store.start_run(
            job_id, session_id=session_id, started_at=datetime.now(UTC).isoformat()
        )
        return {
            "running": running,
            "session_id": session_id,
            "event_offset": event_offset,
            "clock": clock,
        }

    def _complete(
        self,
        job: dict[str, Any],
        started: dict[str, Any],
        *,
        runner: Callable[[dict[str, Any]], dict[str, Any]] | None,
        advance: bool,
        now: str,
    ) -> dict[str, Any]:
        job_id = int(job["id"])
        session_id = started["session_id"]
        event_offset = started["event_offset"]
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
            int(started["running"]["id"]),
            status=status,
            result=detail,
            summary=summary,
            artifacts=artifacts,
            duration_ms=int((time.perf_counter() - started["clock"]) * 1000),
            finished_at=datetime.now(UTC).isoformat(),
            waiting_approval_count=waiting_approval_count,
        )
        if advance:
            # Re-read the job: a pause or cadence edit made during the run wins.
            current = self.store.get_job(job_id)
            if current is not None and current.get("next_run_at"):
                self.store.set_job_next_run(job_id, next_run_for(cadence_id(current), now))
        return recorded
