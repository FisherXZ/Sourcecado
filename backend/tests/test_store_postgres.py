"""The conversation store on Postgres: user isolation and inbox locking.

Two state directories are two users (see docs/engineering/postgres-port.md).
Two stores opened at one directory are one user on two connections.
"""

from __future__ import annotations

import threading
import time

import pytest

from coworker import store as store_module
from coworker.store import ConversationStore


def _seed(store: ConversationStore) -> dict:
    store.create_session("main")
    store.append("main", {"role": "user", "content": "find leads at Rippling"})
    memory = store.remember("prefer short drafts")
    job = store.add_job("0 9 * * 1", "weekly check-in", "2026-01-01T00:00:00+00:00")
    run = store.start_run(job["id"], session_id="sched-1", started_at="2026-01-01T00:00:00+00:00")
    item = store.park_inbox("call_1", "gmail_send", {"draft_id": "d1"}, session_id="main")
    store.set_setting("persona", "sourcing")
    store.apply_queue_command(
        "main", {"type": "queue_add", "command_id": "c1", "item_id": "q1", "text": "next"}
    )
    return {"memory": memory["id"], "job": job["id"], "run": run["id"], "item": item["id"]}


def test_two_users_cannot_read_or_change_each_others_rows_by_id(tmp_path):
    alice = ConversationStore(tmp_path / "alice")
    bob = ConversationStore(tmp_path / "bob")
    ids = _seed(alice)

    # Bob reads nothing of Alice's, by list or by id.
    assert bob.index("main") is None
    assert bob.list_sessions() == []
    assert bob.list_memories() == []
    assert bob.list_schedule() == {"jobs": [], "runs": []}
    assert bob.get_job(ids["job"]) is None
    assert bob.due_jobs("2030-01-01T00:00:00+00:00") == []
    assert bob.get_inbox(ids["item"]) is None
    assert bob.list_inbox(pending_only=False) == []
    assert bob.get_setting("persona") is None
    assert bob.list_queue("main") == []
    assert bob.sessions_with_queue() == []

    # Bob changes nothing of Alice's by id.
    assert bob.rename_session("main", "stolen") is None
    assert bob.set_session_pinned("main", True) is None
    assert bob.memory_update(ids["memory"], "rewritten") is None
    assert bob.memory_classify(ids["memory"]) is None
    assert bob.memory_forget(ids["memory"]) is False
    assert bob.update_job(
        ids["job"], name="x", prompt="x", cadence="x", cron="x", next_run_at=None
    ) is None
    bob.set_job_next_run(ids["job"], "1999-01-01T00:00:00+00:00")
    assert bob.resolve_inbox(ids["item"], "allow") is None
    assert bob.cancel_inbox(ids["item"]) is None
    assert bob.decide_and_claim_inbox_execution(
        ids["item"], "allow", actor="bob", scope="once", claimant="bob"
    ) is None
    assert bob.complete_inbox_execution(
        ids["item"], claimant="bob", ok=True, result={}
    ) is None
    assert bob.mark_queue_offline("main") == 0
    assert bob.claim_next_queue("main") is None

    # Bob's own rows with the same keys stay his own.
    bob.create_session("main")
    bob.rename_session("main", "bob's thread")
    bob.set_setting("persona", "recruiting")
    bob.park_inbox("call_1", "apollo_enrich_contact", {"person_id": "p"})

    # Alice still has exactly what she wrote.
    session = alice.index("main")
    assert session["title"] == "find leads at Rippling"
    assert session["pinned"] is False
    assert [m["content"] for m in alice.list_memories()] == ["prefer short drafts"]
    job = alice.get_job(ids["job"])
    assert job["name"] == "weekly check-in"
    assert job["next_run_at"] == "2026-01-01T00:00:00+00:00"
    item = alice.get_inbox(ids["item"])
    assert item["state"] == "pending"
    assert item["name"] == "gmail_send"
    assert item["execution_status"] == "pending"
    assert alice.get_setting("persona") == "sourcing"
    assert [q["state"] for q in alice.list_queue("main")] == ["waiting"]
    assert bob.index("main")["title"] == "bob's thread"


def _slow_between_read_and_write(monkeypatch) -> None:
    """Hold each decision open between its read and its write.

    `requires_once` runs after the store reads the inbox row and before it
    writes the decision. A pause there makes two callers overlap every time.
    """
    original = store_module.requires_once

    def slow(name: str) -> bool:
        time.sleep(0.3)
        return original(name)

    monkeypatch.setattr(store_module, "requires_once", slow)


def _race(*calls):
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)
    errors: list[BaseException] = []

    def run(index, call):
        try:
            barrier.wait()
            results[index] = call()
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors
    return results


def test_two_connections_resolving_one_inbox_item_exactly_one_wins(tmp_path, monkeypatch):
    first = ConversationStore(tmp_path / "state")
    second = ConversationStore(tmp_path / "state")
    item = first.park_inbox("call_race", "gmail_send", {"draft_id": "d1"})
    _slow_between_read_and_write(monkeypatch)

    results = _race(
        lambda: first.resolve_inbox(item["id"], "allow", actor="first"),
        lambda: second.resolve_inbox(item["id"], "deny", actor="second"),
    )

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    stored = first.get_inbox(item["id"])
    assert stored["state"] == "resolved"
    assert (stored["decision"], stored["actor"]) == (
        winners[0]["decision"],
        winners[0]["actor"],
    )


def test_two_connections_claiming_one_execution_exactly_one_owns_it(tmp_path, monkeypatch):
    first = ConversationStore(tmp_path / "state")
    second = ConversationStore(tmp_path / "state")
    item = first.park_inbox("call_claim", "gmail_send", {"draft_id": "d1"})
    _slow_between_read_and_write(monkeypatch)

    results = _race(
        lambda: first.decide_and_claim_inbox_execution(
            item["id"], "allow", actor="director", scope="once", claimant="first"
        ),
        lambda: second.decide_and_claim_inbox_execution(
            item["id"], "allow", actor="director", scope="once", claimant="second"
        ),
    )

    assert sorted(bool(result["claimed"]) for result in results) == [False, True]
    assert sorted(bool(result["decision_recorded"]) for result in results) == [False, True]
    winner = next(result for result in results if result["claimed"])
    stored = first.get_inbox(item["id"])
    assert stored["execution_status"] == "executing"
    assert stored["execution_claimant"] == winner["item"]["execution_claimant"]


@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_a_resolution_and_a_cancel_at_once_leave_one_outcome(tmp_path, monkeypatch, decision):
    first = ConversationStore(tmp_path / "state")
    second = ConversationStore(tmp_path / "state")
    item = first.park_inbox("call_cancel", "gmail_send", {"draft_id": "d1"})
    _slow_between_read_and_write(monkeypatch)

    resolved, cancelled = _race(
        lambda: first.resolve_inbox(item["id"], decision),
        lambda: (time.sleep(0.05), second.cancel_inbox(item["id"]))[1],
    )

    # resolve_inbox read first and held the row, so the cancel waited and
    # then found the item no longer pending.
    assert resolved is not None
    assert cancelled is None
    stored = first.get_inbox(item["id"])
    assert stored["state"] == "resolved"
    assert stored["decision"] == decision
