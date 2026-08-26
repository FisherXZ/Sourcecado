"""Map Club tool results into person-file ledger events."""

from __future__ import annotations

import re
from typing import Any

from coworker.people import PersonStore

_NOT_FILED = frozenset(
    {
        "now",
        "remember",
        "memory_update",
        "memory_forget",
        "load_skill",
        "apollo_search_people",
    }
)
_MCP_WRITE = re.compile(r"write|create|delete|update", re.I)


def _event(
    *,
    source: str,
    kind: str,
    summary: str,
    payload: dict[str, Any],
    tool: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "kind": kind,
        "summary": summary,
        "payload": payload,
        "tool": tool,
    }


def _source_for(name: str) -> str | None:
    if name.startswith("gmail_"):
        return "gmail"
    if name.startswith("drive_"):
        return "drive"
    if name.startswith("calendar_"):
        return "calendar"
    if name == "apollo_enrich_contact":
        return "apollo"
    if name.startswith("mcp__granola__"):
        return "granola"
    if name in {"web_search", "web_fetch"}:
        return "web"
    return None


def _granola_write(name: str) -> bool:
    if not name.startswith("mcp__granola__"):
        return False
    last = name.rsplit("__", 1)[-1]
    return bool(_MCP_WRITE.search(last))


def _error_event(name: str, result: dict[str, Any]) -> dict[str, Any] | None:
    source = _source_for(name)
    if source is None:
        return None
    detail = str(result.get("error") or "unknown error")
    if name.startswith("mcp__granola__"):
        label = "Granola " + name.rsplit("__", 1)[-1].replace("_", " ")
    else:
        parts = name.split("_")
        label = parts[0].capitalize() + (" " + " ".join(parts[1:]) if len(parts) > 1 else "")
    return _event(
        source=source,
        kind="error",
        summary=f"{label} failed",
        payload={"detail": detail},
        tool=name,
    )


def event_from_tool(
    name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
    *,
    ok: bool,
) -> dict[str, Any] | None:
    """kwargs for PersonStore.append_event except person_id / actor / session / run."""
    if name in _NOT_FILED or _granola_write(name):
        return None
    if not ok:
        return _error_event(name, result)
    if name == "gmail_search":
        messages = result.get("messages") or []
        ids = [
            str(row.get("id"))
            for row in messages
            if isinstance(row, dict) and row.get("id")
        ]
        query = str(arguments.get("query") or "")
        return _event(
            source="gmail",
            kind="mail",
            summary=f"Searched Gmail for {query!r} ({len(ids)})",
            payload={"query": query, "ids": ids},
            tool=name,
        )
    if name == "gmail_read":
        return _event(
            source="gmail",
            kind="mail",
            summary=f"Read mail {result.get('subject') or result.get('id') or ''}".strip(),
            payload={
                "id": result.get("id"),
                "from": result.get("from"),
                "subject": result.get("subject"),
                "date": result.get("date"),
            },
            tool=name,
        )
    if name == "gmail_draft":
        return _event(
            source="gmail",
            kind="draft",
            summary=f"Drafted mail to {result.get('to') or arguments.get('to') or ''}".strip(),
            payload={
                "draft_id": result.get("id"),
                "to": result.get("to"),
                "subject": result.get("subject"),
                "sent": False,
            },
            tool=name,
        )
    if name == "gmail_send":
        return _event(
            source="gmail",
            kind="send",
            summary=f"Sent draft {result.get('draft_id') or arguments.get('draft_id') or ''}".strip(),
            payload={
                "draft_id": result.get("draft_id") or arguments.get("draft_id"),
                "id": result.get("id"),
                "sent": True,
            },
            tool=name,
        )
    if name == "drive_search":
        files = [
            {"id": row.get("id"), "name": row.get("name")}
            for row in (result.get("files") or [])
            if isinstance(row, dict)
        ]
        query = str(arguments.get("query") or "")
        return _event(
            source="drive",
            kind="file",
            summary=f"Searched Drive for {query!r} ({len(files)})",
            payload={"query": query, "files": files},
            tool=name,
        )
    if name == "drive_list_folder":
        files = [
            {"id": row.get("id"), "name": row.get("name")}
            for row in (result.get("files") or [])
            if isinstance(row, dict)
        ]
        folder_id = str(
            result.get("id") or result.get("folder_id") or arguments.get("folder_id") or ""
        )
        return _event(
            source="drive",
            kind="file",
            summary=f"Listed Drive folder {result.get('name') or folder_id}".strip(),
            payload={
                "folder_id": folder_id,
                "status": result.get("status"),
                "files": files,
            },
            tool=name,
        )
    if name == "drive_read":
        return _event(
            source="drive",
            kind="file",
            summary=f"Read Drive file {result.get('name') or result.get('id') or ''}".strip(),
            payload={
                "id": result.get("id"),
                "name": result.get("name"),
                "mimeType": result.get("mimeType"),
                "status": result.get("status"),
                "truncated": bool(result.get("truncated", False)),
                "reason": result.get("reason"),
            },
            tool=name,
        )
    if name == "calendar_list":
        events = result.get("events") or []
        count = len(events) if isinstance(events, list) else 0
        return _event(
            source="calendar",
            kind="event",
            summary=f"Listed {count} calendar events",
            payload={"count": count},
            tool=name,
        )
    if name in {"calendar_create", "calendar_update"}:
        action = "Created" if name == "calendar_create" else "Updated"
        return _event(
            source="calendar",
            kind="event",
            summary=f"{action} calendar event {result.get('summary') or result.get('id') or ''}".strip(),
            payload={"id": result.get("id"), "summary": result.get("summary")},
            tool=name,
        )
    if name == "apollo_enrich_contact":
        return _event(
            source="apollo",
            kind="enrich",
            summary=f"Enriched {result.get('name') or 'contact'}",
            payload={
                "name": result.get("name"),
                "title": result.get("title"),
                "organizationName": result.get("organizationName"),
                "email": result.get("email"),
            },
            tool=name,
        )
    if name.startswith("mcp__granola__"):
        return _event(
            source="granola",
            kind="meeting",
            summary=f"Read meeting notes {result.get('title') or result.get('id') or ''}".strip(),
            payload={"id": result.get("id"), "title": result.get("title")},
            tool=name,
        )
    if name == "web_search":
        rows = result.get("results") or []
        urls = [
            str(row.get("url"))
            for row in rows
            if isinstance(row, dict) and row.get("url")
        ]
        query = str(arguments.get("query") or "")
        return _event(
            source="web",
            kind="search",
            summary=f"Searched the web for {query!r} ({len(urls)})",
            payload={"query": query, "count": len(urls), "urls": urls},
            tool=name,
        )
    if name == "web_fetch":
        return _event(
            source="web",
            kind="fetch",
            summary=f"Fetched {result.get('title') or result.get('url') or arguments.get('url') or ''}".strip(),
            payload={
                "url": result.get("url") or arguments.get("url"),
                "title": result.get("title"),
            },
            tool=name,
        )
    return None


def record_tool_on_person(
    people: PersonStore,
    session_id: str,
    name: str,
    arguments: dict[str, Any] | None,
    result: dict[str, Any],
    *,
    ok: bool,
) -> None:
    """Bind a one-person keep to this session, then file connector events on the bound person."""
    if name == "people_keep" and ok:
        kept = result.get("kept") or []
        if len(kept) == 1:
            person_id = kept[0].get("person_id") if isinstance(kept[0], dict) else None
            if person_id:
                people.bind_session(session_id, str(person_id))
        return
    if not ok and str(result.get("error") or "") == "denied by user":
        return
    person_id = people.person_for_session(session_id)
    if not person_id:
        return
    event = event_from_tool(name, arguments or {}, result, ok=ok)
    if event is None:
        return
    people.append_event(person_id, session_id=session_id, **event)
    if ok and event.get("kind") == "draft":
        person = people.get(person_id)
        if person is not None and person.get("sequence_state") is None:
            people.set_sequence(person_id, "open", actor="assistant")
