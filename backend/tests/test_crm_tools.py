from coworker.crm_tasks import TaskService
from coworker.people import PersonStore
from coworker.tools import execute


def test_chat_and_ui_share_tasks_authority_and_retry_receipts(tmp_path):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    args = {"person_id": pid, "title": "Send examples", "operation_id": "chat-create-task", "reason": "Director requested examples"}
    ok, result = execute("crm_create_task", args, people=people, actor="assistant", run_id="run_test")
    assert ok
    task = result["task"]
    assert task["origin"] == "human"
    assert task["protected_fields"]
    assert result["change"]["actor"] == "assistant"
    assert result["artifacts"] == [{"id": result["change"]["change_id"], "artifact_type": "crm_change", "title": "CRM change receipt"}]
    assert execute("crm_create_task", args, people=people)[1]["replayed"]
    assert TaskService(people).get(pid, task["task_id"]) == task
    assert people.get(pid)["sequence_state"] is None


def test_bound_chat_cannot_mutate_another_person(tmp_path):
    people = PersonStore(tmp_path)
    first = people.keep_from_apollo(apollo_id="first", first_name="First", last_name_obfuscated=None, title=None, company=None)["person_id"]
    second = people.keep_from_apollo(apollo_id="second", first_name="Second", last_name_obfuscated=None, title=None, company=None)["person_id"]
    people.bind_session("session_test", first)
    ok, result = execute("crm_create_task", {"person_id": second, "title": "Wrong person", "operation_id": "chat-wrong-person", "reason": "No"}, people=people, session_id="session_test")
    assert not ok
    assert "does not match" in result["error"]
    assert TaskService(people).list(timezone="UTC")["tasks"] == []


def test_chat_can_set_then_clear_a_flag_without_changing_status(tmp_path):
    people = PersonStore(tmp_path)
    pid = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")["person_id"]
    for label, operation in (("Call Maya", "chat-set-flag"), (None, "chat-clear-flag")):
        ok, result = execute("crm_set_attention", {"person_id": pid, "flag_reason": label, "expected_version": people.get(pid)["version"],
            "operation_id": operation, "reason": "Director requested this attention change"}, people=people)
        assert ok
        assert result["attention"]["reason"] == label
    assert people.get(pid)["sequence_state"] is None
