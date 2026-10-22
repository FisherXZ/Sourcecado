"""Read-only interpretation gates and durable progress, using real local stores."""
import asyncio
import json

from coworker.crm_analysis import ConversationAnalysis
from coworker.crm_mail_repository import MailRepository
from coworker.crm_service import CrmService
from coworker.crm_tasks import TaskService
from coworker.people import PersonStore
from coworker.provider import FakeProvider


def example(tmp_path, body="Please send project examples by 2026-10-23.", completeness="present"):
    people = PersonStore(tmp_path)
    p = people.keep_from_apollo(apollo_id="maya", first_name="Maya", last_name_obfuscated="Patel", title="Director", company="Lumen")
    pid = p["person_id"]
    MailRepository(people).save_message(pid, {"id": "msg1", "thread_id": "thread1", "from": "maya@example.com", "to": "director@example.com", "subject": "Examples", "received_at": "2026-10-22T10:00:00Z"}, direction="inbound", account="director@example.com", content={"body": body, "body_state": completeness})
    action = {"kind": "task_create", "message_id": "msg1", "title": "Send project examples", "quote": body.strip(), "due_date": "2026-10-23", "date_text": "2026-10-23", "reason": "Maya requested examples"}
    return people, pid, action


def run(people, actions):
    provider = FakeProvider(deltas=(json.dumps({"actions": actions}),))
    asyncio.run(ConversationAnalysis(people, provider).run_pending())
    return provider


def test_full_owned_request_creates_one_sourced_task_and_restart_replays(tmp_path):
    people, pid, action = example(tmp_path)
    provider = run(people, [action])
    tasks = TaskService(people).list(timezone="UTC")["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["source_refs"][0]["message_id"] == "msg1"
    assert tasks[0]["origin"] == "assistant"
    assert MailRepository(people).analysis_state(pid)["status"] == "current"
    assert not provider.calls[0][-1].get("tools")
    restarted = PersonStore(tmp_path)
    assert not run(restarted, [action]).calls


def test_partial_quoted_vague_and_foreign_evidence_cannot_commit_tasks(tmp_path):
    for case in ("partial", "quoted", "vague", "foreign"):
        body = "> Please send project examples by 2026-10-23." if case == "quoted" else "Please send project examples by 2026-10-23."
        people, pid, action = example(tmp_path / case, body=body, completeness="partial" if case == "partial" else "present")
        if case == "vague":
            action["date_text"] = "Friday sometime"
        if case == "foreign":
            action["message_id"] = "someone-elses-message"
        run(people, [action])
        assert TaskService(people).list(timezone="UTC")["tasks"] == []
        if case != "foreign":
            assert CrmService(people).proposals(pid)


def test_human_description_stays_protected_and_dismissal_does_not_resurrect(tmp_path):
    people, pid, action = example(tmp_path)
    crm = CrmService(people)
    crm.save_description(pid, "general", text="", expected_version=0, operation_id="test-human-blank")
    action = {**action, "kind": "description", "slot": "general", "text": "Maya requested examples."}
    run(people, [action])
    assert people.descriptions(pid)["general"]["text"] == ""
    proposal = crm.proposals(pid)[0]
    crm.resolve(pid, proposal["proposal_id"], decision="dismissed", operation_id="test-dismiss", timezone="UTC")
    # Explicit retry of the same revision must not revive a dismissed suggestion.
    worker = ConversationAnalysis(people, FakeProvider(deltas=(json.dumps({"actions": [action]}),)))
    worker.retry(pid)
    asyncio.run(worker.run_pending())
    assert crm.proposals(pid) == []


def test_model_failure_retains_evidence_and_caps_retries(tmp_path):
    people, pid, action = example(tmp_path)
    worker = ConversationAnalysis(people, FakeProvider(deltas=("not json",)))
    for _ in range(3):
        worker.retry(pid, reset_attempts=False)
        asyncio.run(worker.run_pending())
    state = MailRepository(people).analysis_state(pid)
    assert state["status"] == "failed"
    assert state["attempts"] == 3
    assert state["analyzed_revision"] == 0
    assert MailRepository(people).message("director@example.com", "msg1")["body"]
    assert TaskService(people).list(timezone="UTC")["tasks"] == []

    MailRepository(people).save_message(pid, {"id": "new-evidence", "thread_id": "thread1", "from": "maya@example.com", "to": "director@example.com", "subject": "Updated request"},
        direction="inbound", account="director@example.com", content={"body": "The original request still stands.", "body_state": "present"})
    assert MailRepository(people).analysis_state(pid)["status"] == "pending"
    run(people, [action])
    assert MailRepository(people).analysis_state(pid)["status"] == "current"


def test_cancelled_analysis_remains_restartable(tmp_path):
    people, pid, action = example(tmp_path)
    class SlowProvider:
        async def astream(self, **kwargs):
            await asyncio.sleep(60)
            yield
    async def scenario():
        task = asyncio.create_task(ConversationAnalysis(people, SlowProvider()).run_pending())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(scenario())
    assert MailRepository(people).analysis_state(pid)["attempts"] == 0
    run(people, [action])
    assert MailRepository(people).analysis_state(pid)["status"] == "current"


def test_refresh_requests_are_shared_and_read_only(tmp_path):
    from coworker.crm_analysis import RefreshCoordinator
    people, pid, action = example(tmp_path)
    calls = []
    worker = ConversationAnalysis(people, FakeProvider(deltas=(json.dumps({"actions": [action]}),)))
    def collect():
        calls.append("read-only collect")
    async def scenario():
        coordinator = RefreshCoordinator(collect, worker)
        first, second = coordinator.request(), coordinator.request()
        assert first is second
        await asyncio.gather(first, second)
    asyncio.run(scenario())
    assert calls == ["read-only collect"]
    assert MailRepository(people).analysis_state(pid)["status"] == "current"


def test_undo_automatic_task_cancels_only_that_task_and_suppresses_recreation(tmp_path):
    people, pid, action = example(tmp_path)
    run(people, [action])
    change = people._conn.execute("SELECT change_id FROM crm_changes WHERE entity_type='task'").fetchone()[0]
    crm = CrmService(people)
    undone = crm.undo(pid, change, operation_id="test-undo-inferred")
    assert undone["task"]["state"] == "cancelled"
    worker = ConversationAnalysis(people, FakeProvider(deltas=(json.dumps({"actions": [action]}),)))
    worker.retry(pid)
    asyncio.run(worker.run_pending())
    assert crm.tasks.list(timezone="UTC")["tasks"] == []
    assert crm.proposals(pid) == []


def test_explicit_fulfillment_is_tied_to_the_task_and_matching_thread(tmp_path):
    people, pid, action = example(tmp_path)
    run(people, [action])
    task = TaskService(people).list(timezone="UTC")["tasks"][0]
    MailRepository(people).save_message(pid, {"id": "msg2", "thread_id": "thread1", "from": "director@example.com", "to": "maya@example.com", "subject": "Examples", "received_at": "2026-10-22T12:00:00Z"}, direction="outbound", account="director@example.com", content={"body": "Here are the project examples: https://example.com/projects", "body_state": "present"})
    completion = {"kind": "task_update", "message_id": "msg2", "task_id": task["task_id"], "expected_version": task["version"],
        "changes": {"state": "completed"}, "quote": "Here are the project examples: https://example.com/projects"}
    run(people, [completion])
    saved = TaskService(people).get(pid, task["task_id"])
    assert saved["state"] == "completed"
    assert saved["source_refs"][-1]["message_id"] == "msg2"
