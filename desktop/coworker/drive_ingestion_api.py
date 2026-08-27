"""HTTP routes for durable Drive ingestion jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from coworker.drive_ingestion import DriveIngestionCoordinator, DriveIngestionStore


def drive_ingestion_router(
    *,
    store: DriveIngestionStore,
    coordinator: DriveIngestionCoordinator,
    people: Any,
) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/drive-ingestions")
    async def create_drive_ingestion(request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            job = store.create_job(
                folder_id=str(body.get("folder_id") or ""),
                resolved_path=str(body.get("resolved_path") or ""),
            )
            await coordinator.start(job["id"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        current = store.get_job(job["id"])
        return JSONResponse({"job": current}, status_code=202)

    @router.get("/v1/drive-ingestions")
    def list_drive_ingestions():
        return {"jobs": store.list_jobs()}

    @router.get("/v1/drive-ingestions/{job_id}")
    def get_drive_ingestion(job_id: str):
        job = store.get_job(job_id)
        if job is None:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return {"job": job}

    @router.get("/v1/drive-ingestions/{job_id}/query")
    def query_drive_ingestion(
        job_id: str,
        q: str,
        include_external: bool = False,
        limit: int = 20,
    ):
        try:
            return store.query(
                job_id,
                q,
                include_external=include_external,
                limit=limit,
            )
        except ValueError as exc:
            status = 404 if "unknown" in str(exc) else 409
            return JSONResponse({"error": str(exc)}, status_code=status)

    @router.get("/v1/drive-ingestions/{job_id}/sources")
    def list_drive_ingestion_sources(job_id: str):
        if store.get_job(job_id) is None:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return {"sources": store.list_source_receipts(job_id)}

    @router.post("/v1/drive-ingestions/{job_id}/cancel")
    def cancel_drive_ingestion(job_id: str):
        try:
            job = coordinator.cancel(job_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse({"job": job}, status_code=202)

    @router.post("/v1/drive-ingestions/{job_id}/resume")
    async def resume_drive_ingestion(job_id: str):
        job = store.get_job(job_id)
        if job is None:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        if job["status"] != "paused":
            return JSONResponse({"error": "job_not_paused"}, status_code=409)
        try:
            await coordinator.start(job_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"job": store.get_job(job_id)}, status_code=202)

    @router.post("/v1/drive-ingestions/{job_id}/rerun")
    async def rerun_drive_ingestion(job_id: str):
        try:
            store.prepare_rerun(job_id)
            await coordinator.start(job_id)
        except ValueError as exc:
            status = 404 if "unknown" in str(exc) else 409
            return JSONResponse({"error": str(exc)}, status_code=status)
        return JSONResponse({"job": store.get_job(job_id)}, status_code=202)

    @router.post("/v1/drive-ingestions/{job_id}/external-sources")
    async def add_explicit_source(job_id: str, request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("request body must be an object")
            store.add_explicit_source(
                job_id,
                drive_id=str(body.get("drive_id") or ""),
                name=str(body.get("name") or ""),
                parent_id=str(body.get("parent_id") or ""),
                display_path=str(body.get("display_path") or ""),
                mime_type=str(body.get("mime_type") or ""),
                modified_time=str(body.get("modified_time") or "") or None,
                web_view_link=str(body.get("web_view_link") or "") or None,
            )
            await coordinator.start(job_id)
        except ValueError as exc:
            status = 404 if "unknown" in str(exc) else 409
            return JSONResponse({"error": str(exc)}, status_code=status)
        return JSONResponse({"job": store.get_job(job_id)}, status_code=202)

    @router.post("/v1/drive-ingestions/{job_id}/proposals")
    async def propose_board_attachment(job_id: str, request: Request):
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("fields"), dict):
                raise ValueError("proposal fields must be an object")
            proposal = store.propose_board_attachment(
                job_id,
                source_drive_id=str(body.get("source_drive_id") or ""),
                person_id=str(body.get("person_id") or ""),
                record_type=str(body.get("record_type") or ""),
                fields=body["fields"],
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"proposal": proposal}, status_code=201)

    @router.get("/v1/drive-ingestions/{job_id}/proposals")
    def list_board_proposals(job_id: str):
        if store.get_job(job_id) is None:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return {"proposals": store.list_board_proposals(job_id)}

    @router.post("/v1/drive-ingestion-proposals/{proposal_id}/apply")
    def apply_board_attachment(proposal_id: str):
        try:
            proposal = store.apply_board_proposal(
                proposal_id,
                people=people,
                actor="director",
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"proposal": proposal}

    return router
