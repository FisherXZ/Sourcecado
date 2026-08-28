"""Project the living brief from a person file and its ledger."""

from __future__ import annotations

from typing import Any

from coworker.people import SOURCES


def build_brief(person: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    name = " ".join(
        part for part in (person.get("first_name"), person.get("last_name")) if part
    )
    bits = [name] if name else []
    if person.get("title"):
        bits.append(str(person["title"]))
    who = ", ".join(bits)
    if person.get("company"):
        who = f"{who} at {person['company']}" if who else str(person["company"])
    missing: list[str] = []
    if not person.get("email"):
        missing.append("email")
    present = {str(event.get("source")) for event in events}
    if "gmail" not in present:
        missing.append("mail")
    if any(
        event.get("kind") == "meeting"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("notes_present") is False
        for event in events
    ):
        missing.append("meeting notes")
    sources = [source for source in SOURCES if source in present]
    learned = [str(event.get("summary")) for event in events if event.get("summary")]
    learned.extend(
        f"Meeting notes (untrusted): {event['payload']['notes_excerpt']}"
        for event in events
        if event.get("kind") == "meeting"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("notes_excerpt")
    )
    return {
        "who": who,
        "why": person.get("target") or "",
        "learned": learned,
        "missing": missing,
        "sources": sources,
    }
