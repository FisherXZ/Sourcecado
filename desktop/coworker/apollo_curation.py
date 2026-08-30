"""Director-reviewed Apollo shortlist curation."""

from __future__ import annotations

from typing import Any

from coworker.people import PersonStore


def curate_apollo_candidates(
    people: PersonStore,
    rows: list[dict[str, Any]],
    *,
    target: str,
) -> dict[str, Any]:
    target = str(target or "").strip()
    if not target:
        raise ValueError("director-authored target is required")
    kept: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    seen: set[str] = set()
    identities = {
        str(row.get("apolloId") or "").strip()
        for row in rows
        if str(row.get("apolloId") or "").strip()
    }
    for index, row in enumerate(rows):
        apollo_id = str(row.get("apolloId") or "").strip()
        if not apollo_id:
            failed.append(
                {"row_index": index, "apollo_id": None, "code": "missing_apollo_id"}
            )
            continue
        if apollo_id in seen:
            duplicates.append({"row_index": index, "apollo_id": apollo_id})
            continue
        seen.add(apollo_id)
        try:
            existing = people.get_by_apollo_id(apollo_id)
            person = people.keep_from_apollo(
                apollo_id=apollo_id,
                first_name=row.get("firstName"),
                last_name_obfuscated=row.get("lastNameObfuscated"),
                title=row.get("title"),
                company=row.get("organizationName"),
                target=target,
            )
        except Exception:
            failed.append(
                {
                    "row_index": index,
                    "apollo_id": apollo_id,
                    "code": "invalid_candidate",
                }
            )
            continue
        try:
            sourcing_session = people.session_for_person(person["person_id"])
        except ValueError:
            sourcing_session = None
        kept.append(
            {
                "row_index": index,
                "apollo_id": apollo_id,
                "person_id": person["person_id"],
                "version": int(person["version"]),
                "operation": "updated" if existing is not None else "created",
                "first_name": person["first_name"],
                "last_name": person["last_name"],
                "last_name_status": person["last_name_status"],
                "title": person["title"],
                "company": person["company"],
                "sourcing_chat": (
                    {"session_id": sourcing_session}
                    if sourcing_session is not None
                    else None
                ),
            }
        )
    status = "success" if kept and not failed else "partial" if kept else "failed"
    return {
        "status": status,
        "selected_row_count": len(rows),
        "selected_identity_count": len(identities),
        "kept": kept,
        "failed": failed,
        "duplicates": duplicates,
    }
