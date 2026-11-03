"""Gather what a follow-up email for one task needs.

Returns the task, the person's email (if any), their Gmail threads, sources,
talking points and a starting message. Read-only: it creates no draft, sends
nothing, does not change the task and does not enrich the person. A missing
email blocks sending, not writing.
"""

from __future__ import annotations

from typing import Any

from coworker.gmail import reply_subject

TALKING_POINT_MAX = 600


def _clip(text: str, limit: int = TALKING_POINT_MAX) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"


def _first_name(person: dict[str, Any]) -> str | None:
    return str(person.get("first_name") or "").strip() or None


def talking_points(
    task: dict[str, Any],
    descriptions: dict[str, dict[str, Any]],
    brief: dict[str, Any],
) -> list[dict[str, str]]:
    """Points to cover, from the task, the short description and the brief.

    Uses saved records only, never email bodies.
    """
    points: list[dict[str, str]] = [
        {"origin": "task", "text": _clip(str(task.get("title") or ""))}
    ]
    details = str(task.get("details") or "").strip()
    if details:
        points.append({"origin": "task", "text": _clip(details)})
    general = str((descriptions.get("general") or {}).get("text") or "").strip()
    if general:
        points.append({"origin": "description", "text": _clip(general)})
    wants = brief.get("wants") or {}
    if str(wants.get("state") or "") == "current" and str(wants.get("text") or "").strip():
        points.append({"origin": "brief", "text": _clip(str(wants["text"]))})
    return points


def prepare_follow_up(
    *,
    person: dict[str, Any],
    task: dict[str, Any],
    threads: list[dict[str, Any]],
    descriptions: dict[str, dict[str, Any]],
    brief: dict[str, Any],
    account: str | None,
    thread_id: str | None = None,
    label: str,
) -> dict[str, Any]:
    """Raises ValueError if `thread_id` is not one of this person's threads."""
    if thread_id:
        selected = next((row for row in threads if row["thread_id"] == thread_id), None)
        if selected is None:
            raise ValueError("That Gmail conversation is not filed on this person.")
    else:
        selected = threads[0] if threads else None
    recipient = str(person.get("email") or "").strip() or None
    first = _first_name(person)
    subject = (
        reply_subject(selected.get("subject")) if selected else _clip(str(task["title"]), 150)
    )
    body = f"Hi {first},\n\n" if first else ""

    blockers: list[dict[str, str]] = []
    if recipient is None:
        blockers.append(
            {
                "code": "no_recipient",
                "message": (
                    "No email address on file. You can write now, but sending "
                    "waits until one is added."
                ),
            }
        )
    if account is None:
        blockers.append(
            {
                "code": "gmail_disconnected",
                "message": "Gmail is not connected, so no draft can be made yet.",
            }
        )

    points = talking_points(task, descriptions, brief)
    sources = [
        {
            "id": str(row.get("id") or ""),
            "provider": str(row.get("provider") or ""),
            "title": row.get("title"),
            "evidence": row.get("evidence"),
        }
        for row in (brief.get("source_refs") or [])
        if isinstance(row, dict)
    ]
    return {
        "task": {
            "task_id": task["task_id"],
            "title": task["title"],
            "details": task.get("details") or "",
            "due_date": task.get("due_date"),
            "state": task["state"],
            "version": task["version"],
        },
        "person": {
            "person_id": person["person_id"],
            "label": label,
            "email": recipient,
            "sequence_state": person.get("sequence_state"),
        },
        "account": account,
        "recipient": recipient,
        "thread": selected,
        "threads": threads,
        "subject": subject,
        "body": body,
        "talking_points": points,
        "sources": sources,
        "restricted_source_count": int(brief.get("restricted_source_count") or 0),
        "blockers": blockers,
        "assistant_prompt": assistant_prompt(
            label=label,
            task=task,
            thread=selected,
            recipient=recipient,
            points=points,
            sources=sources,
        ),
        "sent": False,
    }


def assistant_prompt(
    *,
    label: str,
    task: dict[str, Any],
    thread: dict[str, Any] | None,
    recipient: str | None,
    points: list[dict[str, str]],
    sources: list[dict[str, Any]],
) -> str:
    """A chat message asking the assistant to write the email text.

    It is placed in the chat box for the director to send. It asks for text
    only; the Gmail draft is created from the person file.
    """
    lines = [
        f"Write a follow-up email to {label} for this task: {task['title']}.",
    ]
    if str(task.get("details") or "").strip():
        lines.append(f"Task details: {_clip(str(task['details']))}")
    if task.get("due_date"):
        lines.append(f"Due: {task['due_date']}.")
    if thread:
        lines.append(
            "It is a reply in the Gmail thread "
            f"“{thread.get('subject') or 'no subject'}” "
            f"(thread {thread['thread_id']}), so keep that subject."
        )
    else:
        lines.append("We have no earlier emails with them.")
    if recipient is None:
        lines.append(
            "Their email address is not on file. Do not look it up or enrich it."
        )
    if len(points) > 1:
        lines.append("Talking points from the person file:")
        lines.extend(f"- {point['text']}" for point in points[1:])
    if sources:
        names = sorted({str(row["provider"]) for row in sources if row.get("provider")})
        if names:
            lines.append(
                "Use only the permitted sources on this person file "
                f"({', '.join(names)})."
            )
    lines.append(
        "Reply with only the email body as plain text. Do not create a Gmail "
        "draft or send anything. I will paste it into the follow-up editor."
    )
    return "\n".join(lines)
