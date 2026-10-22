"""Contacts behavior across storage, attention, authority and review."""
from datetime import UTC, datetime

import pytest

from coworker.people import PersonStore
from coworker.crm_tasks import TaskConflict, TaskService
from coworker.crm_service import CrmService


def setup_person(tmp_path):
    people = PersonStore(tmp_path)
    person = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")
    people.set_sequence(person["person_id"], "open", actor="director")
    return people, person["person_id"], CrmService(people)


def test_attention_keeps_tasks_status_and_flags_independent(tmp_path):
    people, pid, crm = setup_person(tmp_path)
    tasks = TaskService(people)
    task = tasks.create(pid, title="Send examples", due_date="2026-10-22", timezone="America/Los_Angeles", operation_id="test-create-task", actor="director")["task"]
    now = datetime(2026, 10, 23, 6, 59, tzinfo=UTC)  # Still October 22 in LA.
    row = crm.contacts(timezone="America/Los_Angeles", now=now)["contacts"][0]
    assert row["attention_reasons"][0]["code"] == "due_today"
    assert row["next_action"]["task_id"] == task["task_id"]
    flag = crm.set_attention(pid, reason="Call after meeting", until="2026-10-22", expected_version=people.get(pid)["version"], operation_id="test-manual-flag")
    assert flag["attention"]["reason"] == "Call after meeting"
    row = crm.contacts(timezone="America/Los_Angeles", now=now)["contacts"][0]
    assert [r["code"] for r in row["attention_reasons"]] == ["manual", "due_today"]
    later = datetime(2026, 10, 23, 7, 1, tzinfo=UTC)
    row = crm.contacts(timezone="America/Los_Angeles", now=later)["contacts"][0]
    assert [r["code"] for r in row["attention_reasons"]] == ["overdue"]
    people.set_sequence(pid, "done", actor="director")
    assert tasks.get(pid, task["task_id"])["state"] == "open"


def test_manual_flag_retry_and_stale_undo_do_not_lose_other_work(tmp_path):
    people, pid, crm = setup_person(tmp_path)
    args = dict(reason="Call", until=None, expected_version=people.get(pid)["version"], operation_id="test-flag-once")
    saved = crm.set_attention(pid, **args)
    assert crm.set_attention(pid, **args)["replayed"]
    crm.set_attention(pid, reason="Different", until=None, expected_version=people.get(pid)["version"], operation_id="test-flag-newer")
    with pytest.raises(TaskConflict):
        crm.undo(pid, saved["change"]["change_id"], operation_id="test-undo-stale")


def test_description_receipt_retry_protection_and_scoped_undo(tmp_path):
    people, pid, crm = setup_person(tmp_path)
    saved = crm.save_description(pid, "general", text="Known offline", expected_version=0, operation_id="test-description")
    assert saved["description"]["authority"] == "human"
    assert crm.save_description(pid, "general", text="Known offline", expected_version=0, operation_id="test-description")["replayed"]
    crm.save_description(pid, "detailed", text="Keep this", expected_version=0, operation_id="test-detailed")
    crm.undo(pid, saved["change"]["change_id"], operation_id="test-description-undo")
    assert people.descriptions(pid)["general"]["text"] is None
    assert people.descriptions(pid)["detailed"]["text"] == "Keep this"


def test_proposal_acceptance_commits_task_and_resolution_together(tmp_path):
    people, pid, crm = setup_person(tmp_path)
    proposal = crm.propose(pid, kind="task_create", value={"title": "Reconnect"}, expected_version=0, sources=[], fingerprint="vague-message", reason="No concrete date")
    assert crm.propose(pid, kind="task_create", value={"title": "Reconnect"}, expected_version=0, sources=[], fingerprint="vague-message", reason="No concrete date")["proposal_id"] == proposal["proposal_id"]
    result = crm.resolve(pid, proposal["proposal_id"], decision="accepted", operation_id="test-accept-proposal", timezone="UTC")
    assert result["proposal"]["state"] == "accepted"
    assert result["task"]["protected_fields"]
    assert len(TaskService(people).list(timezone="UTC")["tasks"]) == 1
    assert crm.resolve(pid, proposal["proposal_id"], decision="accepted", operation_id="test-accept-proposal", timezone="UTC")["replayed"]


def test_pending_proposal_never_replaces_committed_next_action(tmp_path):
    people, pid, crm = setup_person(tmp_path)
    task = TaskService(people).create(pid, title="Already promised", timezone="UTC", operation_id="test-real-task", actor="director")["task"]
    crm.propose(pid, kind="task_create", value={"title": "Maybe later"}, expected_version=0, sources=[], fingerprint="maybe", reason="Review")
    row = crm.contacts(timezone="UTC")["contacts"][0]
    assert row["next_action"]["task_id"] == task["task_id"]
    assert row["attention_reasons"][0]["code"] == "suggestion"


def test_local_api_reports_attention_and_rejects_stale_edits(tmp_path):
    from fastapi.testclient import TestClient
    from coworker.provider import FakeProvider
    from coworker.server import TOKEN_HEADER, create_app
    app = create_app(token="test-crm", state=tmp_path, provider=FakeProvider())
    person = app.state.people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")
    pid = person["person_id"]
    app.state.people.set_sequence(pid, "open", actor="director")
    client, headers = TestClient(app), {TOKEN_HEADER: "test-crm"}
    assert client.get("/v1/crm/contacts").status_code == 401
    saved = client.patch(f"/v1/people/{pid}/attention", headers=headers, json={"reason": "Call Maya", "expected_version": app.state.people.get(pid)["version"], "operation_id": "test-api-flag"})
    assert saved.status_code == 200
    rows = client.get("/v1/crm/contacts", headers=headers).json()["contacts"]
    assert rows[0]["attention_reasons"][0]["label"] == "Call Maya"
    detail = client.get(f"/v1/people/{pid}", headers=headers).json()
    assert detail["crm_changes"]
    assert detail["crm_proposals"] == []
    assert client.patch(f"/v1/people/{pid}/attention", headers=headers, json={"reason": "Stale", "expected_version": 0, "operation_id": "test-api-stale"}).status_code == 409


def test_accepted_task_and_proposal_both_roll_back_on_receipt_failure(tmp_path, monkeypatch):
    people, pid, crm = setup_person(tmp_path)
    proposal = crm.propose(pid, kind="task_create", value={"title": "Send examples"}, expected_version=0, sources=[], fingerprint="atomic", reason="Review")
    def fail(*args, **kwargs):
        raise RuntimeError("simulated failure after task write")
    monkeypatch.setattr(crm, "_record", fail)
    with pytest.raises(RuntimeError):
        crm.resolve(pid, proposal["proposal_id"], decision="accepted", operation_id="test-atomic-accept", timezone="UTC")
    assert TaskService(people).list(timezone="UTC")["tasks"] == []
    assert crm.proposals(pid)[0]["state"] == "pending"


def test_task_pages_have_stable_ids_without_overlap(tmp_path):
    people, pid, crm = setup_person(tmp_path)
    for index in range(5):
        crm.tasks.create(pid, title=f"Promise {index}", timezone="UTC", actor="director", operation_id=f"pagination-task-{index}")
    first = crm.tasks.list(timezone="UTC", limit=2)
    second = crm.tasks.list(timezone="UTC", limit=2, offset=2)
    assert first["truncated"] and second["truncated"]
    assert not {t["task_id"] for t in first["tasks"]} & {t["task_id"] for t in second["tasks"]}
    assert [t["title"] for t in first["tasks"] + second["tasks"]] == [f"Promise {i}" for i in range(4)]


def test_description_api_replays_before_checking_the_old_version(tmp_path):
    from fastapi.testclient import TestClient
    from coworker.provider import FakeProvider
    from coworker.server import TOKEN_HEADER, create_app
    app = create_app(token="test-description-retry", state=tmp_path, provider=FakeProvider())
    pid = app.state.people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    client = TestClient(app)
    args = {"headers": {TOKEN_HEADER: "test-description-retry"}, "json": {"text": "Offline context", "expected_version": 0, "operation_id": "description-lost-response"}}
    first = client.patch(f"/v1/people/{pid}/descriptions/general", **args)
    replay = client.patch(f"/v1/people/{pid}/descriptions/general", **args)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["replayed"]
    assert first.json()["change"]["change_id"] == replay.json()["change"]["change_id"]
    undo = client.post(f"/v1/people/{pid}/crm-changes/{first.json()['change']['change_id']}/undo",
        headers=args["headers"], json={"operation_id": "undo-description-through-http"})
    assert undo.status_code == 200
    assert app.state.people.descriptions(pid)["general"]["text"] is None
