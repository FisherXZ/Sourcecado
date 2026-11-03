"""A bounded, read-only projection of one Person File for the agent.

It reads the same local records the Person File shows, with the same source
restrictions. It never calls Gmail, Apollo or Monid. Mail subjects and Monid
source titles and links are external text; ``person_file_evidence`` fences them.
"""

from __future__ import annotations

import copy
from typing import Any

from coworker.crm_enrichment import EnrichmentView
from coworker.crm_service import CrmService
from coworker.crm_sources import sources_visible
from coworker.crm_tasks import TaskNotFound
from coworker.evidence_envelope import EvidenceParts, external, opaque
from coworker.monid import saved_research
from coworker.people import PersonStore

LIMITS = {"tasks": 20, "changes": 10, "mail_threads": 10, "monid_research": 5, "monid_sources": 5}
_IDENTITY = ("person_id", "first_name", "last_name", "title", "company", "email", "phone", "linkedin_url", "version")


def _bounded(items: list, limit: int, truncated: bool | None = None) -> dict[str, Any]:
    return {"items": items[:limit], "truncated": len(items) > limit if truncated is None else truncated}


def _clip(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _enrichment(people: PersonStore, store: Any, pid: str, configured: bool) -> dict[str, Any] | None:
    if store is None:
        return None
    try:
        view = EnrichmentView(people, store, lambda: configured).get(pid)
    except ValueError:  # Several bound sourcing chats: the app reports person_chat_conflict.
        return None
    latest = view["latest"]
    return {
        "status": view["status"],
        "latest_attempt": {key: latest[key] for key in (
            "id", "status", "credits_estimated", "credits_actual", "fetched_at",
            "fields_found", "fields_applied", "review_required")} if latest else None,
        "unresolved_unknown_id": view["unresolved_unknown_id"],
        "problem": view["problem"],
        "can_request": view["can_request"],
    }


def _research(person: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for row in saved_research(person):
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        found = output.get("results") if isinstance(output.get("results"), list) else (
            [output] if output.get("title") else [])
        sources = [{"title": _clip(source.get("title"), 200), "url": _clip(source.get("url"), 500)}
                   for source in found if isinstance(source, dict)]
        items.append({
            **{key: row.get(key) for key in ("approval_id", "status", "remote_provider", "endpoint",
                                             "quoted_cost_usd", "actual_cost_usd", "cost_unknown", "saved_at")},
            "untrusted": True,
            "source_count": len(sources),
            "sources": sources[:LIMITS["monid_sources"]],
        })
    return items


def person_file(people: PersonStore, pid: str, *, store: Any, timezone: str,
                allowed_source_ids: set[str] | None, apollo_configured: bool) -> dict[str, Any]:
    person = people.get(pid, expand_sources=True, allowed_source_ids=allowed_source_ids)
    if person is None:
        raise TaskNotFound("unknown person")
    crm = CrmService(people)
    hidden = False
    descriptions = {}
    for slot, description in people.descriptions(pid).items():
        if sources_visible(people, pid, description["source_refs"], allowed_source_ids):
            descriptions[slot] = {key: description[key] for key in ("slot", "text", "authority", "version")}
        else:
            hidden = True
            descriptions[slot] = {"slot": slot, "restricted": True}
    listing = crm.tasks.list(timezone=timezone, person_id=pid, limit=LIMITS["tasks"],
                             allowed_source_ids=allowed_source_ids)
    tasks = []
    for task in listing["tasks"]:
        if sources_visible(people, pid, task["source_refs"], allowed_source_ids):
            tasks.append({key: task[key] for key in ("task_id", "title", "due_date", "due_status", "version")})
        else:
            hidden = True
    changes = [{**{key: change[key] for key in ("change_id", "entity_type", "entity_id", "action", "actor",
                                                "created_at", "can_undo")},
                "reason": _clip(change["reason"], 200)}
               for change in crm.recent_changes(pid, limit=LIMITS["changes"] + 1,
                                                allowed_source_ids=allowed_source_ids)]
    threads = [{"thread_id": thread["thread_id"], "subject": _clip(thread.get("subject"), 200),
                "last_message_at": thread.get("last_at"), "last_direction": thread.get("last_direction"),
                "message_count": thread["message_count"]}
               for thread in people.mail_threads(pid)]
    enrichment = _enrichment(people, store, pid, apollo_configured)
    unavailable = [] if enrichment is not None else ["enrichment"]
    partial = hidden or bool(unavailable)
    return {
        "status": "partial" if partial else "complete",
        "partial": partial,
        "unavailable_sources": unavailable,
        "person_id": pid,
        "person": {key: person.get(key) for key in _IDENTITY},
        "sequence": {"state": person.get("sequence_state"), "version": person["version"]},
        "attention": {"reason": person.get("attention_reason"), "until": person.get("attention_until")},
        "descriptions": descriptions,
        "tasks": _bounded(tasks, LIMITS["tasks"], listing["truncated"]),
        "changes": _bounded(changes, LIMITS["changes"]),
        "mail_threads": _bounded(threads, LIMITS["mail_threads"]),
        "enrichment": enrichment,
        "monid_research": _bounded(_research(person), LIMITS["monid_research"]),
        "restricted_source_count": person.get("restricted_source_count", 0),
        "timezone": timezone,
    }


def person_file_evidence(tool_name: str, payload: Any) -> EvidenceParts:
    """Sourcecado-owned metadata, with mail subjects and research sources fenced."""
    try:
        metadata = copy.deepcopy(payload)
        threads = metadata["mail_threads"]["items"]
        research = metadata["monid_research"]["items"]
        subjects = [(thread["thread_id"], thread.pop("subject")) for thread in threads]
        found = [(item, item.pop("sources")) for item in research]
    except (KeyError, TypeError, AttributeError):
        return opaque("unknown", tool_name, payload)
    envelopes = []
    if any(subject for _, subject in subjects):
        envelopes.extend(external(
            "gmail", identity=("crm_mail_subjects", payload.get("person_id"), *(tid for tid, _ in subjects)),
            title="Filed Gmail thread subjects", sensitivity="sensitive",
            body="\n".join(f"{tid}: {subject or ''}" for tid, subject in subjects),
        ).envelopes)
    for item, sources in found:
        if sources:
            envelopes.extend(external(
                "monid", identity=("research", payload.get("person_id"), item.get("approval_id"), item.get("saved_at")),
                title="Saved Monid research sources",
                body="\n".join(f"{source.get('title') or 'Untitled'} {source.get('url') or ''}".strip()
                               for source in sources),
            ).envelopes)
    return EvidenceParts(metadata=metadata, envelopes=tuple(envelopes))
