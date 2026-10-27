"""HTTP routes for chat attachments (#253).

The upload is the raw request body, not a form. The file name travels in the
``X-Attachment-Name`` header, percent-encoded, so it is never part of a URL
that an access log would keep. The body is read in chunks and refused as soon
as it passes the size limit.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from coworker.attachments import (
    ATTACHMENT_ID,
    MAX_FILE_BYTES,
    AttachmentRejected,
    AttachmentStore,
    human_bytes,
    limits,
)

NAME_HEADER = "x-attachment-name"
_NO_STORE = {"Cache-Control": "no-store"}
UNAVAILABLE = {
    "error": "file_unavailable",
    "message": "File unavailable. It is no longer on this computer.",
}


def _json(payload: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=_NO_STORE)


def _rejected(error: AttachmentRejected) -> JSONResponse:
    return _json({"error": error.code, "message": error.message}, error.status)


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_FILE_BYTES:
        raise _too_large(int(declared))
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_FILE_BYTES:
            raise _too_large(len(received))
    return bytes(received)


def _too_large(size: int) -> AttachmentRejected:
    return AttachmentRejected(
        "too_large",
        f"This file is over the {human_bytes(MAX_FILE_BYTES)} limit"
        + (f" ({human_bytes(size)})." if size <= MAX_FILE_BYTES * 4 else "."),
        status=413,
    )


def attachments_router(
    *, store: AttachmentStore, accepts_images: Callable[[], bool]
) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/attachments/limits")
    def attachment_limits():
        return _json(limits(accepts_images=accepts_images()))

    @router.post("/v1/attachments")
    async def upload_attachment(request: Request):
        try:
            data = await _read_body(request)
            name = request.headers.get(NAME_HEADER, "")
            attachment = await asyncio.to_thread(
                store.create,
                name=name,
                data=data,
                accepts_images=accepts_images(),
            )
        except AttachmentRejected as error:
            return _rejected(error)
        return _json({"attachment": attachment.public()}, 201)

    @router.get("/v1/attachments/{attachment_id}")
    def get_attachment(attachment_id: str):
        attachment = _find(store, attachment_id)
        if attachment is None:
            return _json(UNAVAILABLE, 404)
        return _json({"attachment": attachment.public()})

    @router.get("/v1/attachments/{attachment_id}/text")
    def get_attachment_text(attachment_id: str):
        attachment = _find(store, attachment_id)
        text = None if attachment is None else store.text(attachment_id)
        if attachment is None or text is None:
            return _json(UNAVAILABLE, 404)
        return _json(
            {
                "text": text,
                "truncated": attachment.truncated,
                "total_chars": attachment.total_chars,
            }
        )

    @router.get("/v1/attachments/{attachment_id}/content")
    def get_attachment_content(attachment_id: str):
        attachment = _find(store, attachment_id)
        data = None if attachment is None else store.content(attachment_id)
        # Only images are served as bytes. Text and PDF are read as text.
        if attachment is None or attachment.kind != "image" or data is None:
            return _json(UNAVAILABLE, 404)
        return Response(
            data,
            media_type=attachment.mime,
            headers={
                **_NO_STORE,
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    return router


def _find(store: AttachmentStore, attachment_id: str):
    if not ATTACHMENT_ID.fullmatch(attachment_id):
        return None
    return store.get(attachment_id)
