"""Attach an operator-selected Drive search result, folder child, or read
source to an existing person file as a reviewable source reference.

This is narrow evidence curation for one person, distinct from the resumable
folder-ingestion job in ``drive_ingestion.py`` (#43). It never re-indexes a
folder tree, never writes to Drive, and never copies raw file bodies into the
person record - only the metadata needed to judge scope, freshness,
extraction truth, and sensitivity.
"""

from __future__ import annotations

from typing import Any

from coworker.people import PersonStore

EVIDENCE_KINDS = frozenset({"search_result", "folder_child", "read_source"})
_EXTRACTION_STATUSES = frozenset(
    {"metadata_only", "read", "truncated", "unsupported", "failed"}
)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize(
    kind: str,
    raw: dict[str, Any],
    *,
    folder_id: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Turn one ``coworker.drive.DriveApi`` search/list_folder/read result into
    person-file ``source_ref`` fields plus a stable idempotency key.

    Pure - no Drive access, no person-store writes. ``raw`` is expected to
    already be a redacted DriveApi result (name and content credential
    redaction, legal-document flagging) since that is drive.py's job, not
    this module's.
    """
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unsupported Drive evidence kind: {kind}")
    if not isinstance(raw, dict):
        raise ValueError("Drive result must be an object")
    file_id = _clean(raw.get("id"))
    if not file_id:
        raise ValueError("Drive file id is required")
    clean_folder_id = _clean(folder_id)
    if kind == "folder_child" and not clean_folder_id:
        raise ValueError("folder_id is required to attach a folder child")
    parents = [str(p) for p in (raw.get("parents") or []) if _clean(p)]
    out_of_scope = kind == "folder_child" and clean_folder_id not in parents

    status = str(raw.get("status") or "metadata_only")
    if status not in _EXTRACTION_STATUSES:
        status = "metadata_only"

    sensitivity = "standard"
    if raw.get("sensitive_content_redacted"):
        sensitivity = "restricted"
    elif raw.get("source_safety"):
        sensitivity = "sensitive"

    modified_time = _clean(raw.get("modifiedTime"))
    source_refs = raw.get("sources") if isinstance(raw.get("sources"), list) else []

    fields = {
        "provider": "Google Drive",
        "kind": kind,
        "drive_id": file_id,
        "title": _clean(raw.get("name")) or "Drive file",
        "mime_type": _clean(raw.get("mimeType")),
        "modified_time": modified_time,
        "parents": parents,
        "url": raw.get("webViewLink"),
        "extraction_status": status,
        "truncated": bool(raw.get("truncated")),
        "sensitivity": sensitivity,
        "folder_id": clean_folder_id if kind == "folder_child" else None,
        "out_of_scope": out_of_scope,
        "source_refs": source_refs,
    }
    idempotency_key = f"drive:{file_id}:{modified_time or 'unknown'}"
    return fields, idempotency_key


def attach(
    people: PersonStore,
    person_id: str,
    *,
    kind: str,
    raw: dict[str, Any],
    folder_id: str | None = None,
    actor: str,
    rationale_summary: str,
    session_id: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Attach one Drive evidence item to ``person_id`` as a ``source_ref``.

    Reattaching an unchanged source (same drive id and modified time) is
    idempotent. A changed source gets a new idempotency key, so it lands as a
    new, inspectable source reference alongside the old one rather than a
    silent overwrite.
    """
    fields, idempotency_key = normalize(kind, raw, folder_id=folder_id)
    return people.upsert_attachment(
        person_id,
        record_type="source_ref",
        fields=fields,
        idempotency_key=idempotency_key,
        actor=actor,
        rationale_summary=rationale_summary,
        session_id=session_id,
        run_id=run_id,
    )
