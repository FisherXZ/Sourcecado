#!/usr/bin/env python
"""Seed demo contacts, committee members, and tasks for the CRM-tasks feature.

This writes fictional sample data straight into the local ``people.db`` so a
reviewer can see the Tasks view, member assignment, owner column, and inline
sequence editing without Apollo or Gmail credentials. It is idempotent: people
are keyed by Apollo id and tasks by an operation id, so re-running it will not
create duplicates.

Run it against the SAME state dir the app uses, e.g.:

    export CLUB_STATE_DIR="$HOME/.local/state/sourcecado-dev"
    backend/.venv/bin/python backend/scripts/seed_demo.py

With no CLUB_STATE_DIR set it uses the default ``~/.config/club``.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Make the backend package importable regardless of the current directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from coworker.people import PersonStore  # noqa: E402


def _state_dir() -> Path:
    override = os.environ.get("CLUB_STATE_DIR")
    if override == "~":
        return Path.home()
    if override and override.startswith("~/"):
        return Path.home() / override[2:]
    if override:
        return Path(override)
    return Path.home() / ".config" / "club"


def _due(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def main() -> None:
    state = _state_dir()
    print(f"Seeding demo CRM data into: {state}")
    store = PersonStore(state)

    members = {name: store.add_member(name)["member_id"] for name in ("Milan", "Priya")}

    # (apollo_id, first, last, title, company, sequence, owner)
    people = [
        ("demo-maya", "Maya", "Chen", "Software Engineer", "Stripe", "open", "Milan"),
        ("demo-nora", "Nora", "Diaz", "Design Lead", "Figma", "open", "Milan"),
        ("demo-leo", "Leo", "Park", "Product Manager", "Airbnb", "in_conversation", "Priya"),
    ]
    ids: dict[str, str] = {}
    for apollo, first, last, title, company, seq, owner in people:
        person = store.keep_from_apollo(
            apollo_id=apollo,
            first_name=first,
            last_name_obfuscated=last,
            title=title,
            company=company,
            target="demo",
        )
        pid = person["person_id"]
        ids[first] = pid
        store.set_sequence(pid, seq, actor="director")
        current = store.get(pid)
        if current["owner_member_id"] != members[owner]:
            store.patch(
                pid,
                fields={"owner_member_id": members[owner]},
                expected_version=int(current["version"]),
                actor="director",
                rationale_summary="Seed demo owner",
            )

    # (person, title, due_offset_days|None, assignee, operation_id)
    tasks = [
        ("Maya", "Send project examples", 4, "Milan", "seed-maya-examples"),
        ("Maya", "Schedule intro call", 7, "Milan", "seed-maya-call"),
        ("Nora", "Draft and send proposal", 2, "Priya", "seed-nora-proposal"),
        ("Leo", "Reply to question about timeline", 0, "Milan", "seed-leo-reply"),
        ("Nora", "Share Codeology deck", None, None, "seed-nora-deck"),
    ]
    for person, title, offset, assignee, op in tasks:
        store.create_task(
            ids[person],
            title=title,
            due_date=_due(offset) if offset is not None else None,
            assignee_member_id=members[assignee] if assignee else None,
            actor="director",
            operation_id=op,
        )

    board = store.list_board()
    active = [p["first_name"] for lane in ("open", "in_conversation", "done") for p in board[lane]]
    print(f"  members: {sorted(members)}")
    print(f"  contacts in Contacts: {active}")
    print(f"  tasks: {len(store.list_tasks())}")
    print("Done. Start the app (backend + GUI) against this same state dir to see them.")


if __name__ == "__main__":
    main()
