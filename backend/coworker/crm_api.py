"""Thin authenticated HTTP adapters for the shared manual-task service."""

from fastapi import APIRouter, Body, Query
from fastapi.responses import JSONResponse

from coworker.crm_repository import CrmConflict, CrmNotFound
from coworker.crm_service import CrmService


def crm_router(service: CrmService):
    router = APIRouter()

    def respond(action, *, status=200):
        try:
            return JSONResponse(action(), status_code=status)
        except CrmNotFound:
            return JSONResponse({"error": "not found"}, status_code=404)
        except CrmConflict as exc:
            return JSONResponse({"error": str(exc), "current": exc.current}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @router.get("/v1/crm/tasks")
    def tasks(person_id: str | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return respond(lambda: service.repository.list_tasks(person_id=person_id, limit=limit, offset=offset))

    @router.get("/v1/crm/people")
    def people(query: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        return respond(lambda: service.repository.list_people(query=query, limit=limit, offset=offset))

    @router.post("/v1/people/{person_id}/tasks", status_code=201)
    def create(person_id: str, payload: dict = Body(...)):
        return respond(lambda: service.save_task(person_id, payload), status=201)

    @router.patch("/v1/people/{person_id}/tasks/{task_id}")
    def edit(person_id: str, task_id: str, payload: dict = Body(...)):
        return respond(lambda: service.save_task(person_id, payload, task_id=task_id))

    @router.get("/v1/crm/preferences")
    def preferences():
        return respond(lambda: {"timezone": service.timezone()})

    @router.put("/v1/crm/preferences")
    def preferences_save(payload: dict = Body(...)):
        if set(payload) != {"timezone"}:
            return JSONResponse({"error": "Only timezone may be set."}, status_code=422)
        return respond(lambda: service.set_timezone(payload["timezone"]))

    return router
