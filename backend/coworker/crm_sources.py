"""Person-owned CRM source references; never accept an access grant from a model."""

from __future__ import annotations

from typing import Any
import re


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


def source_links(people: Any, person_id: str, refs: list[str], allowed: set[str] | None = None) -> list[dict]:
    """Project current permitted pointers, never source titles or arbitrary URLs.

    A Gmail href must match a filed message on this person's tracked thread.
    An attachment alone cannot authorize navigation to a different mailbox item.
    """
    from coworker.reply_filing import build_index
    with people._lock:
        index = build_index(people)
        events = [e["payload"] for e in people.timeline(person_id) if e["source"] == "gmail"]
        visible = {ref["id"]: ref for ref in people._attachments_for(person_id, allowed_source_ids=allowed)
                   if ref["type"] == "source_ref"}
        links = []
        for sid in sorted(set(refs)):
            if sid not in visible:
                continue
            fields = visible[sid]["fields"]
            mid, thread = fields.get("message_id"), fields.get("thread_id")
            url = None
            if (isinstance(mid, str) and isinstance(thread, str)
                    and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", thread)
                    and {s.person_id for s in index.threads.get(thread, ())} == {person_id}
                    and any(e.get("message_id") == mid and e.get("thread_id") == thread for e in events)):
                url = "https://mail.google.com/mail/u/0/#all/" + thread
            links.append({"id": sid, "label": "Gmail message" if fields.get("provider") == "Gmail" else "Source", "url": url})
        return links
