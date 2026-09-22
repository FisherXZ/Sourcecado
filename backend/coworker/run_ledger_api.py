"""HTTP routes for the unified Agent Run ledger.

Read only, on purpose. There is no write verb here, so no request to this
surface can create a run, change a state, or record an approval. Authority
lives with the owner-native approval and send receipts, never with the ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from coworker.run_ledger import DEFAULT_QUERY_LIMIT, RunLedger


def _no_store(payload: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _moment(value: str | None, field: str) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def run_ledger_router(*, ledger: RunLedger) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/agent-runs")
    def list_agent_runs(
        run_id: str | None = None,
        session_id: str | None = None,
        person_id: str | None = None,
        trigger: str | None = None,
        status: list[str] | None = Query(default=None),
        since: str | None = None,
        until: str | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ):
        try:
            page = ledger.query(
                run_id=run_id,
                session_id=session_id,
                person_id=person_id,
                trigger=trigger,
                statuses=status,
                since=_moment(since, "since"),
                until=_moment(until, "until"),
                limit=limit,
            )
        except ValueError as exc:
            return _no_store({"error": str(exc)}, status_code=400)
        return _no_store(page)

    @router.get("/v1/agent-runs/{run_id}")
    def get_agent_run(run_id: str):
        receipt = ledger.receipt(run_id)
        if receipt is None:
            return _no_store({"error": "run_not_found"}, status_code=404)
        return _no_store({"receipt": receipt})

    return router
