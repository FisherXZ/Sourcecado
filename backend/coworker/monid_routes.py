"""Local Monid connection, quote and result routes; research uses the Inbox."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from coworker.monid import MonidError, MonidResearchActions


def monid_router(actions: MonidResearchActions):
    router = APIRouter()

    def refuse(exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @router.get("/v1/monid/connection")
    def status():
        return actions.status()

    @router.put("/v1/monid/connection")
    async def save(request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"api_key"}:
                raise MonidError("Only a personal API key is accepted here.")
            return actions.save_key(payload["api_key"])
        except (MonidError, ValueError) as exc:
            return refuse(exc)

    @router.delete("/v1/monid/connection")
    def disconnect():
        actions.secrets.delete("monid")
        return actions.status()

    @router.post("/v1/monid/check")
    def check():
        try:
            return actions.check()
        except MonidError as exc:
            return refuse(exc)

    @router.get("/v1/monid/catalog")
    def catalog(q: str = "", limit: int = 5):
        try:
            return actions.catalog(q, limit)
        except MonidError as exc:
            return refuse(exc)

    @router.post("/v1/monid/inspect")
    async def inspect(request: Request):
        try:
            payload = await request.json()
            return actions.inspect(payload.get("provider"), payload.get("endpoint"))
        except MonidError as exc:
            return refuse(exc)

    @router.post("/v1/people/{person_id}/monid/quote")
    async def quote(person_id: str, request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"session_id", "provider", "endpoint", "input", "max_cost_usd"}:
                raise MonidError("Choose the person chat, exact endpoint, structured input and maximum USD cost.")
            return {"quote": actions.prepare(person_id, payload["session_id"], payload["provider"], payload["endpoint"], payload["input"], payload["max_cost_usd"])}
        except MonidError as exc:
            return refuse(exc)

    @router.post("/v1/monid/approvals", status_code=201)
    async def approval(request: Request):
        try:
            payload = await request.json()
            return {"item": actions.park(payload.get("quote_id"), payload.get("session_id"))}
        except MonidError as exc:
            return refuse(exc)

    @router.get("/v1/people/{person_id}/monid/results")
    def saved(person_id: str):
        try:
            return {"results": actions.saved_results(person_id)}
        except MonidError as exc:
            return refuse(exc)

    @router.post("/v1/people/{person_id}/monid/results/{approval_id}")
    def result(person_id: str, approval_id: str):
        try:
            return actions.read_result(person_id, approval_id)
        except MonidError as exc:
            return refuse(exc)

    return router
