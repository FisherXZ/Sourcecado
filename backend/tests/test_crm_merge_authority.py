"""Merge-review regressions for requested entity, deadline, and source authority."""
import sqlite3
import pytest

from coworker import crm_tasks
from coworker.crm_tasks import TaskService
from coworker.people import PersonStore
from test_crm_tools import invoke, keep


@pytest.mark.parametrize("text", [
    "Undo the description change", "Undo the status change", "Undo the attention flag",
    "Undo the last suggestion", "Undo the last email send", "Undo the last change", "Undo",
])
def test_non_task_or_unscoped_undo_never_falls_back_to_unique_task(tmp_path, text):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    saved = tasks.create(pid, title="Call", actor="director", operation_id="merge-before-call", timezone="America/Los_Angeles")
    done = tasks.update(pid, saved["task"]["task_id"], changes={"state": "completed"}, expected_version=1,
                        actor="director", operation_id="merge-complete-call", timezone="America/Los_Angeles")
    ok, result = invoke(people, "crm_undo_change", {
        "change_id": done["change"]["change_id"], "expected_version": 2,
        "operation_id": "merge-wrong-entity", "reason": "Director request",
    }, text)
    assert not ok, result
    assert tasks.get(pid, saved["task"]["task_id"])["state"] == "completed"
    assert len(tasks.changes(saved["task"]["task_id"])) == 2


@pytest.mark.parametrize("text", [
    "Remind me to send examples this Friday", "Remind me to send examples in two days",
    "Remind me to send examples at noon", "Remind me to send examples tomorrow",
    "Remind me to send examples next month", "Remind me to send examples on Halloween",
    "Remind me to send examples in eleven days", "Remind me to send examples within two days",
    "Remind me to send examples in a couple of days", "Remind me to send examples October 23",
    "Remind me to send examples tonight", "Remind me to send examples at five",
])
def test_unhandled_deadline_does_not_become_undated_work(tmp_path, text):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_create_task", {
        "title": "Send examples", "operation_id": "merge-missing-date", "reason": "Director request",
    }, text)
    assert not ok, result
    assert TaskService(people).list(person_id=pid, timezone="America/Los_Angeles")["tasks"] == []


def test_undo_receipt_cannot_authorize_a_new_non_task_directive(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    task = tasks.create(pid, title="Call", actor="director", operation_id="merge-replay-call", timezone="America/Los_Angeles")["task"]
    done = tasks.update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1,
                        actor="director", operation_id="merge-replay-complete", timezone="America/Los_Angeles")
    args = {"change_id": done["change"]["change_id"], "expected_version": 2,
            "operation_id": "merge-replay-undo", "reason": "Director request"}
    assert invoke(people, "crm_undo_change", args, "Undo that task completion")[0]
    ok, result = invoke(people, "crm_undo_change", args, "Undo the description change")
    assert not ok, result
    assert len(tasks.changes(task["task_id"])) == 3


@pytest.mark.parametrize("foreign", [False, True])
def test_explicit_missing_or_other_person_task_id_never_falls_back(tmp_path, foreign):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    task = tasks.create(pid, title="Call", actor="director", operation_id="merge-missing-call", timezone="America/Los_Angeles")["task"]
    done = tasks.update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1,
                        actor="director", operation_id="merge-missing-complete", timezone="America/Los_Angeles")
    requested = "task_" + "0" * 32
    if foreign:
        other_pid = keep(people, key="other", first="Other")["person_id"]
        requested = tasks.create(other_pid, title="Private elsewhere", actor="director", operation_id="merge-missing-foreign", timezone="America/Los_Angeles")["task"]["task_id"]
    ok, result = invoke(people, "crm_undo_change", {
        "change_id": done["change"]["change_id"], "expected_version": 2,
        "operation_id": "merge-missing-undo", "reason": "Director request",
    }, f"Undo {requested}")
    assert not ok, result
    assert tasks.get(pid, task["task_id"])["state"] == "completed"


def test_explicit_task_id_supports_field_edit_when_other_tasks_exist(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    first = tasks.create(pid, title="First", actor="director", operation_id="merge-select-first", timezone="America/Los_Angeles")["task"]
    other = tasks.create(pid, title="Other", actor="director", operation_id="merge-select-other", timezone="America/Los_Angeles")["task"]
    ok, result = invoke(people, "crm_update_task", {
        "task_id": first["task_id"], "changes": {"details": "Use the final deck"}, "expected_version": 1,
        "operation_id": "merge-select-edit", "reason": "Director request",
    }, f"Change task {first['task_id']} details to: Use the final deck")
    assert ok, result
    assert tasks.get(pid, first["task_id"])["details"] == "Use the final deck"
    assert tasks.get(pid, other["task_id"])["details"] == ""


def test_replacement_task_id_is_data_not_a_target_selector(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    first = tasks.create(pid, title="First", actor="director", operation_id="merge-data-first", timezone="America/Los_Angeles")["task"]
    tasks.create(pid, title="Other", actor="director", operation_id="merge-data-other", timezone="America/Los_Angeles")
    value = f"Compare with {first['task_id']}"
    ok, result = invoke(people, "crm_update_task", {
        "task_id": first["task_id"], "changes": {"details": value}, "expected_version": 1,
        "operation_id": "merge-data-edit", "reason": "Director request",
    }, f"Change task details to: {value}")
    assert not ok, result
    assert tasks.get(pid, first["task_id"])["details"] == ""


def test_source_validation_and_new_task_commit_share_write_transaction(tmp_path, monkeypatch):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    before = people.get(pid)["version"]
    source = people.upsert_attachment(pid, record_type="source_ref", fields={"id": "gmail:merge-source"},
        idempotency_key="merge-source", actor="director", rationale_summary="Attach evidence")
    service = TaskService(people)
    other = PersonStore(tmp_path)
    other._conn.execute("PRAGMA busy_timeout=0")
    original = crm_tasks.source_ids
    blocked = []
    def validate(*args):
        refs = original(*args)
        try:
            other.revert(pid, to_version=before, expected_version=other.get(pid)["version"],
                         actor="director", rationale_summary="Revert the source attachment")
        except sqlite3.OperationalError as error:
            assert "locked" in str(error)
            blocked.append(True)
            other._conn.rollback()
        else:
            blocked.append(False)
        return refs
    monkeypatch.setattr(crm_tasks, "source_ids", validate)
    saved = service.create(pid, title="Sourced promise", actor="assistant", source_refs=[source["id"]],
        operation_id="merge-source-create", timezone="America/Los_Angeles")
    assert blocked and all(blocked)
    assert original(people, pid, saved["task"]["source_refs"], None) == [source["id"]]
    assert service.changes(saved["task"]["task_id"])[0]["change_id"] == saved["change"]["change_id"]
    other._conn.close()
