"""Person-owned CRM source references; never accept an access grant from a model."""

from __future__ import annotations

from typing import Any


def source_ids(people: Any, person_id: str, refs: object, allowed: set[str] | None) -> list[str]:
    if refs is None:
        return []
    if not isinstance(refs, (list, tuple)) or len(refs) > 50:
        raise ValueError("source_refs must contain at most 50 source reference ids")
    if not refs:
        return []
    with people._lock:
        visible = {
            item["id"] for item in people._attachments_for(person_id, allowed_source_ids=allowed)
            if item["type"] == "source_ref"
        }
    if any(not isinstance(ref, str) or ref not in visible for ref in refs):
        raise ValueError("source reference is missing, private, or belongs to another person")
    return sorted(set(refs))


def sources_visible(people: Any, person_id: str, refs: object, allowed: set[str] | None) -> bool:
    try:
        source_ids(people, person_id, refs, allowed)
        return True
    except ValueError:
        return False
