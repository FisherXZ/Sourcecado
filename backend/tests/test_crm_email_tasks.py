"""Synthetic email requests exercise the real bounded analysis/task writers.

These are policy/regression proofs, not a measure of live model precision.
"""
import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from coworker.crm_tasks import TaskConflict, TaskError, TaskService
from coworker.people import PersonStore
from coworker.provider import StreamChunk
from coworker.server import TOKEN_HEADER, create_app
from test_crm_analysis import DescriptionModel, ReadOnlyMail, tracked_person, worker

REQUEST = "Please send our team the project proposal by Friday."
BODY = REQUEST + " We want to discuss the student engineering projects with our infrastructure team."


class TaskMail(ReadOnlyMail):
    def __init__(self, body=BODY, *, timestamp="2026-10-18T12:00:00Z", trusted=True):
        super().__init__(body)
        self.meta.update(received_at=timestamp, received_at_trusted=trusted)


def candidate(request=REQUEST, **extra):
    return {"kind": "create", "source": "mail_1", "request": request,
            "title": "Send project proposal", **extra}


class TaskModel(DescriptionModel):
    def __init__(self, tasks=None, **kwargs):
        super().__init__(**kwargs)
        self.tasks = tasks if tasks is not None else [candidate()]

    async def astream(self, **kwargs):
        async for chunk in super().astream(**kwargs):
            if chunk.text_delta:
                result = json.loads(chunk.text_delta)
                result["tasks"] = self.tasks
                chunk = StreamChunk(text_delta=json.dumps(result))
            yield chunk


def run(people, pid, tasks=None, mail=None, **kwargs):
    model = TaskModel(tasks, **kwargs)
    service = worker(people, model, mail or TaskMail())
    result = asyncio.run(service.analyze(pid))
    return service, result


def saved_tasks(people, pid, states=("open",)):
    return TaskService(people).list(person_id=pid, timezone="America/Los_Angeles", states=states)["tasks"]


@pytest.mark.parametrize("wording,due", [
    (REQUEST, "2026-10-23"),
    ("Please send our team the project proposal by 2026-10-23.", "2026-10-23"),
    ("Please send our team the project proposal tomorrow.", "2026-10-19"),
    ("Please send our team the project proposal.", None),
])
def test_clear_original_inbound_request_creates_sourced_task(wording, due, tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    before_status = people.get(pid)["sequence_state"]
    service, result = run(people, pid, [candidate(wording)], TaskMail(wording + " We want to discuss these student engineering projects with our team."))
    assert result["status"] == "analyzed"
    tasks = saved_tasks(people, pid)
    assert len(tasks) == 1
    task = tasks[0]
    assert task["due_date"] == due
    assert task["origin"] == "assistant" and task["protected_fields"] == []
    assert task["source_refs"] and task["source_links"] == [{"id": task["source_refs"][0], "label": "Gmail message", "url": "https://mail.google.com/mail/u/0/#all/tracked-1"}]
    changes = TaskService(people).changes(task["task_id"])
    assert changes[0]["actor"] == "assistant" and changes[0]["can_undo"] is True
    assert changes[0]["source_links"] == task["source_links"]
    assert people.get(pid)["sequence_state"] == before_status
    assert service.proposals(pid) == []


@pytest.mark.parametrize("wording", [
    "It would be nice to see the project proposal sometime.",
    "Please send our team the project proposal next Friday.",
    "If the budget is approved, please send our team the project proposal Friday.",
    "Please do not send our team the project proposal Friday.",
    "Please send our team the project proposal Friday or Monday.",
    "Please send our team the project proposal later this month.",
    "Please send our team the project proposal by October 23.",
    "Please send our team the project proposal by noon.",
    "Please send our team the project proposal by the 3rd.",
    "Please send our team the project proposal due in a fortnight.",
    "Please send our team the project proposal on 10/23 at 3pm.",
    "Please send our team the project proposal Friday by noon.",
    "Please send our team the project proposal by launch.",
    "Please send our team the project proposal after Friday.",
    "Please review the Friday proposal.",
])
def test_vague_conditional_negated_and_ambiguous_dates_only_propose_undated_work(tmp_path, wording):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, [candidate(wording)], TaskMail(wording + " We want to discuss these student engineering projects with our team."))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    proposal = next(p for p in service.proposals(pid) if p["target_kind"] == "task_create")
    assert json.loads(proposal["proposed_value"])["due_date"] is None
    assert proposal["source_refs"]


@pytest.mark.parametrize("wording,body", [
    (REQUEST, "> " + REQUEST + "\n" + "We would like to discuss the engineering student projects with our infrastructure team."),
    ("Please send our team the project proposal Friday.", "If approved, please send our team the project proposal Friday. We would like to discuss student projects with your engineering team."),
    (REQUEST, "We would like to discuss engineering student projects with your team.\nOn Monday Maya wrote:\n" + REQUEST),
])
def test_model_cannot_cherry_pick_quoted_or_conditional_request_spans(tmp_path, wording, body):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, [candidate(wording)], TaskMail(body))
    assert result["status"] in {"analyzed", "failed", "partial"}
    assert saved_tasks(people, pid) == []


@pytest.mark.parametrize("trusted,timestamp", [(False, "2026-10-18T12:00:00Z"), (True, None), (True, "2026-10-18")])
def test_relative_dates_need_trusted_message_time_not_analysis_clock(tmp_path, trusted, timestamp):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, mail=TaskMail(timestamp=timestamp, trusted=trusted))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid) == []
    assert json.loads(service.proposals(pid)[0]["proposed_value"])["due_date"] is None


def test_relative_date_uses_saved_operator_timezone(tmp_path):
    from coworker.crm_analysis import AnalysisService, ConversationReader
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    request = "Please send our team the project proposal tomorrow."
    mail = TaskMail(request + " We want to discuss these projects with our infrastructure team.", timestamp="2026-10-19T02:00:00Z")
    service = AnalysisService(people, provider=TaskModel([candidate(request)]), reader=ConversationReader(mail), timezone="America/Los_Angeles")
    assert asyncio.run(service.analyze(pid, now=10**12))["status"] == "analyzed"
    assert saved_tasks(people, pid)[0]["due_date"] == "2026-10-19"


@pytest.mark.parametrize("wording", ["Please review Friday’s proposal.", "Please review the project proposal. Our Friday meeting went well."])
def test_topical_weekday_never_becomes_an_invented_deadline(tmp_path, wording):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, result = run(people, pid, [candidate(wording, title="Review proposal")], TaskMail(wording + " We want to discuss the engineering projects with your infrastructure team."))
    assert result["status"] == "analyzed"
    assert all(t["due_date"] is None for t in saved_tasks(people, pid))
    assert all(json.loads(p["proposed_value"]).get("due_date") is None for p in service.proposals(pid))


def test_two_distinct_requests_remain_two_tasks(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    other = "Please share the student project examples by Monday."
    service, result = run(people, pid, [candidate(), candidate(other, title="Share student examples")], TaskMail(BODY + " " + other))
    assert result["status"] == "analyzed"
    assert {(t["title"], t["due_date"]) for t in saved_tasks(people, pid)} == {("Send project proposal", "2026-10-23"), ("Share student examples", "2026-10-19")}


def test_two_promises_in_one_sentence_get_distinct_canonical_support(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    first = "Please send our team the project proposal by Friday"
    second = "share the student project examples by Monday."
    body = first + " and " + second + " We would like to discuss engineering projects with your infrastructure team."
    service, result = run(people, pid, [candidate(first), candidate(second, title="Share student examples")], TaskMail(body))
    assert result["status"] == "analyzed"
    assert {(t["title"], t["due_date"]) for t in saved_tasks(people, pid)} == {("Send project proposal", "2026-10-23"), ("Share student examples", "2026-10-19")}
    assert people._conn.execute("SELECT count(DISTINCT request_start) FROM crm_task_evidence").fetchone()[0] == 2


def add_unrelated_mail(people, pid):
    people.file_inbound_reply(pid, message_id="aaa-unrelated", thread_id="tracked-1", sender="maya@lumen.test", subject="Developer tools", snippet="another", received_at="2026-10-19T12:00:00Z")


@pytest.mark.parametrize("resolution", ["undo", "complete", "dismiss"])
def test_unchanged_request_never_resurrects_after_restart_and_unrelated_mail(tmp_path, resolution):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    mail = TaskMail()
    tasks = [candidate()] if resolution != "dismiss" else [candidate("It would be nice to see the project proposal sometime.")]
    if resolution == "dismiss":
        mail = TaskMail(tasks[0]["request"] + " We would like to discuss the engineering projects with your team.")
    service, result = run(people, pid, tasks, mail)
    assert result["status"] == "analyzed"
    if resolution == "dismiss":
        proposal = service.proposals(pid)[0]
        service.resolve(pid, proposal["proposal_id"], action="dismiss", expected_version=1)
    else:
        task = saved_tasks(people, pid)[0]
        if resolution == "undo":
            change = TaskService(people).changes(task["task_id"])[0]
            undone = TaskService(people).undo(pid, change["change_id"], operation_id="undo-inferred-task", actor="director", expected_version=1)
            assert undone["task"]["state"] == "cancelled"
        else:
            TaskService(people).update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1, timezone="America/Los_Angeles", operation_id="complete-inferred-task", actor="director")
    add_unrelated_mail(people, pid)
    # The new alphabetically earlier message shifts reply-1 from mail_1 to mail_2.
    tasks = [{**tasks[0], "source": "mail_2", "title": "A changed model paraphrase"}]
    class NewMail(TaskMail):
        def read(self, *, message_id):
            if message_id == "aaa-unrelated":
                meta = self.inbound_message(message_id=message_id)
                return {"id": message_id, "from": meta["from"], "to": meta["to"], "subject": meta["subject"], "body": "Our infrastructure team had a productive discussion about the student engineering projects and learned a great deal."}
            return super().read(message_id=message_id)
    restarted = PersonStore(tmp_path)
    service, result = run(restarted, pid, tasks, NewMail(mail.body))
    assert result["status"] == "analyzed"
    assert saved_tasks(restarted, pid) == []
    assert service.proposals(pid) == []


def test_human_matching_task_and_corrected_date_get_reconciliation_not_duplicate(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    human = TaskService(people).create(pid, title="Send project proposal", details="Use the final deck", due_date="2026-10-26", actor="director", timezone="America/Los_Angeles", operation_id="human-matching-task")["task"]
    service, result = run(people, pid)
    assert result["status"] == "analyzed"
    assert len(saved_tasks(people, pid)) == 1
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_update" and proposal["target_id"] == human["task_id"]
    assert TaskService(people).get(pid, human["task_id"])["due_date"] == "2026-10-26"
    service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    after = TaskService(people).get(pid, human["task_id"])
    assert after["due_date"] == "2026-10-23" and after["details"] == "Use the final deck"
    assert "due_date" in after["protected_fields"]
    assert TaskService(people).changes(after["task_id"])[-1]["source_links"]


def test_stale_task_proposal_acceptance_preserves_target_and_pending_review(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    task = TaskService(people).create(pid, title="Send project proposal", actor="director", timezone="America/Los_Angeles", operation_id="task-stale-accept")["task"]
    service, _ = run(people, pid)
    proposal = service.proposals(pid)[0]
    TaskService(people).update(pid, task["task_id"], changes={"due_date": "2026-11-01"}, expected_version=1, timezone="America/Los_Angeles", actor="director", operation_id="human-after-proposal")
    with pytest.raises(TaskConflict):
        service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert service.proposals(pid)[0]["state"] == "pending"
    assert saved_tasks(people, pid)[0]["due_date"] == "2026-11-01"


def test_task_edit_during_generation_keeps_latest_human_values(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    task = TaskService(people).create(pid, title="Send project proposal", actor="director", timezone="America/Los_Angeles", operation_id="human-task-race")["task"]
    def edit():
        current = TaskService(people).get(pid, task["task_id"])
        if current["version"] > 1:
            return
        TaskService(people).update(pid, task["task_id"], changes={"due_date": "2026-10-29", "details": "Human edit during generation"}, expected_version=current["version"], actor="director", timezone="America/Los_Angeles", operation_id="human-edit-race")
    service, result = run(people, pid, before=edit)
    assert result["status"] == "analyzed"
    proposal = service.proposals(pid)[0]
    assert proposal["expected_target_version"] == 2
    assert saved_tasks(people, pid)[0]["due_date"] == "2026-10-29"


@pytest.mark.parametrize("expanded", [False, True])
def test_request_punctuation_and_expanded_citation_do_not_revive_dismissal(tmp_path, expanded):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    wording = "It would be nice to see the project proposal sometime."
    full = wording + " We want to discuss the student engineering projects with our infrastructure team."
    service, _ = run(people, pid, [candidate(wording)], TaskMail(full))
    proposal = service.proposals(pid)[0]
    service.resolve(pid, proposal["proposal_id"], action="dismiss", expected_version=1)
    add_unrelated_mail(people, pid)
    citation = full if expanded else wording.rstrip(".")
    service, result = run(PersonStore(tmp_path), pid, [candidate(citation, source="mail_2")], TaskMail(full))
    assert result["status"] == "analyzed"
    assert service.proposals(pid) == []


def test_valid_task_hint_reconciles_paraphrased_human_work_and_foreign_hint_cannot_create(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    human = TaskService(people).create(pid, title="Prepare material for Maya", details="Preserve my wording", actor="director", timezone="America/Los_Angeles", operation_id="task-paraphrase-hint")["task"]
    service, result = run(people, pid, [candidate(task_id=human["task_id"])])
    assert result["status"] == "analyzed"
    assert len(saved_tasks(people, pid)) == 1
    proposal = service.proposals(pid)[0]
    assert proposal["target_kind"] == "task_update" and proposal["target_id"] == human["task_id"]
    other = people.keep_from_apollo(apollo_id="foreign", first_name="Other", last_name_obfuscated="Person", title="Lead", company="Elsewhere")["person_id"]
    foreign = TaskService(people).create(other, title="Private elsewhere", actor="director", timezone="America/Los_Angeles", operation_id="task-foreign-hint")["task"]
    new_request = "Please share the student project examples by Monday."
    add_unrelated_mail(people, pid)
    service, result = run(people, pid, [candidate(new_request, task_id=foreign["task_id"])], TaskMail(new_request + " We want to discuss these engineering projects with our team."))
    assert result["status"] == "analyzed"
    assert len(saved_tasks(people, pid)) == 1


def test_missing_task_stage_output_fails_without_checkpoint_or_description_writes(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    class MissingTasks(DescriptionModel):
        async def astream(self, **kwargs):
            async for chunk in super().astream(**kwargs):
                if chunk.text_delta:
                    result = json.loads(chunk.text_delta)
                    result.pop("tasks")
                    chunk = StreamChunk(text_delta=json.dumps(result))
                yield chunk
    service = worker(people, MissingTasks(), TaskMail())
    assert asyncio.run(service.analyze(pid))["failure_code"] == "invalid_model_output"
    assert people.descriptions(pid)["general"]["text"] is None
    assert service.state(pid)["latest_revision"] != service.state(pid)["analyzed_revision"]


def test_task_proposal_acceptance_replay_stale_target_and_atomic_failure(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    request = "It would be nice to see the project proposal sometime."
    service, _ = run(people, pid, [candidate(request)], TaskMail(request + " We want to discuss the engineering projects with your infrastructure team."))
    proposal = service.proposals(pid)[0]
    people._conn.execute("""CREATE TRIGGER fail_task_resolution BEFORE UPDATE OF resolution_receipt_json ON crm_proposals
        BEGIN SELECT RAISE(ABORT, 'after task creation'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert saved_tasks(people, pid) == []
    assert service.proposals(pid)[0]["state"] == "pending"
    people._conn.execute("DROP TRIGGER fail_task_resolution")
    saved = service.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1)
    assert len(saved_tasks(people, pid)) == 1
    assert saved_tasks(people, pid)[0]["origin"] == "human"
    restarted = worker(PersonStore(tmp_path))
    assert restarted.resolve(pid, proposal["proposal_id"], action="accept", expected_version=1) == saved
    with pytest.raises(TaskConflict):
        restarted.resolve(pid, proposal["proposal_id"], action="dismiss", expected_version=1)


@pytest.mark.parametrize("text", ["Thank you for your message and for discussing student engineering projects with our infrastructure team.", "I have sent the project proposal to our team and look forward to discussing the engineering work."])
def test_generic_thanks_and_unbound_send_never_complete_task(tmp_path, text):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    task = TaskService(people).create(pid, title="Send project proposal", actor="director", timezone="America/Los_Angeles", operation_id="task-awaiting-send")["task"]
    service, result = run(people, pid, [candidate(text, kind="complete", task_id=task["task_id"])], TaskMail(text))
    assert result["status"] == "analyzed"
    assert saved_tasks(people, pid)[0]["state"] == "open"
    assert service.proposals(pid)[0]["target_kind"] == "task_update"


@pytest.mark.parametrize("fail_at", ["task", "proposal", "checkpoint"])
def test_analysis_rolls_back_descriptions_tasks_proposals_and_receipts_together(tmp_path, fail_at):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    trigger = {
        "task": "BEFORE INSERT ON person_tasks",
        "proposal": "BEFORE INSERT ON crm_proposals WHEN NEW.target_kind='task_create'",
        "checkpoint": "BEFORE UPDATE OF analyzed_revision ON crm_analysis_state WHEN NEW.analyzed_revision > OLD.analyzed_revision",
    }[fail_at]
    people._conn.execute(f"CREATE TRIGGER fail_analysis {trigger} BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
    service, result = run(people, pid)
    assert result["status"] == "failed"
    assert saved_tasks(people, pid) == []
    assert people.descriptions(pid)["general"]["text"] is None
    assert people._conn.execute("SELECT count(*) FROM crm_proposals").fetchone()[0] == 0
    assert people._conn.execute("SELECT count(*) FROM crm_changes").fetchone()[0] == 0
    assert people._conn.execute("SELECT count(*) FROM crm_operations").fetchone()[0] == 0
    assert service.state(pid)["latest_revision"] != service.state(pid)["analyzed_revision"]


def test_automatic_creation_undo_is_version_checked_and_human_creation_still_cancels(tmp_path):
    people = PersonStore(tmp_path)
    pid = tracked_person(people)
    service, _ = run(people, pid)
    task = saved_tasks(people, pid)[0]
    change = TaskService(people).changes(task["task_id"])[0]
    TaskService(people).update(pid, task["task_id"], changes={"details": "Director correction"}, expected_version=1, actor="director", timezone="America/Los_Angeles", operation_id="edit-inferred-task")
    assert TaskService(people).changes(task["task_id"])[0]["can_undo"] is False
    with pytest.raises(TaskConflict):
        TaskService(people).undo(pid, change["change_id"], operation_id="stale-inferred-undo", actor="director")
    human = TaskService(people).create(pid, title="Another task", timezone="America/Los_Angeles", actor="director", operation_id="human-creation-undo")
    assert TaskService(people).changes(human["task"]["task_id"])[0]["can_undo"] is False
    with pytest.raises(TaskError, match="cancel"):
        TaskService(people).undo(pid, human["change"]["change_id"], operation_id="human-undo-rejected", actor="director")


def test_task_source_links_are_safe_current_and_shared_by_http_and_chat(tmp_path):
    from coworker.crm_tools import execute_crm_tool
    app = create_app(token="email-tasks", state=tmp_path, provider=TaskModel(), gmail=TaskMail())
    people = app.state.people
    pid = tracked_person(people)
    with TestClient(app) as client:
        assert client.post(f"/v1/people/{pid}/analysis", headers={TOKEN_HEADER: "email-tasks"}, json={}).json()["status"] == "analyzed"
        person_tasks = client.get(f"/v1/crm/tasks?person_id={pid}", headers={TOKEN_HEADER: "email-tasks"}).json()["tasks"]
        global_tasks = client.get("/v1/crm/tasks", headers={TOKEN_HEADER: "email-tasks"}).json()["tasks"]
        assert person_tasks[0]["source_links"] == global_tasks[0]["source_links"]
        people.bind_session("email-tasks-chat", pid)
        ok, chat = execute_crm_tool("crm_list_tasks", {}, people=people, session_id="email-tasks-chat")
        assert ok and chat["tasks"][0]["source_links"] == person_tasks[0]["source_links"]
        ref = person_tasks[0]["source_refs"][0]
        with people._lock, people._conn:
            people._conn.execute("UPDATE person_attachments SET restricted=1 WHERE id=?", (ref,))
        assert client.get(f"/v1/crm/tasks?person_id={pid}", headers={TOKEN_HEADER: "email-tasks"}).json()["tasks"] == []
        history = client.get(f"/v1/people/{pid}/tasks/{person_tasks[0]['task_id']}/changes", headers={TOKEN_HEADER: "email-tasks"})
        assert history.status_code in {400, 404}


def test_runtime_chat_can_undo_automatic_creation_once_with_director_authority(tmp_path):
    from coworker.provider import FakeProvider, ToolCall
    from test_crm_tools import scripted_chat, TOKEN
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    people = app.state.people
    pid = tracked_person(people)
    run(people, pid)
    task = saved_tasks(people, pid)[0]
    change = TaskService(people).changes(task["task_id"])[0]
    args = {"change_id": change["change_id"], "expected_version": task["version"],
            "reason": "Director requested Undo of inferred work.", "operation_id": "chat-inference-undo"}
    extra = TaskService(people).create(pid, title="Another independent task", actor="director", timezone="America/Los_Angeles", operation_id="chat-undo-unrelated")
    results = scripted_chat(app, pid, f"Undo {change['change_id']}", [
        ToolCall(id="inferred-undo", name="crm_undo_change", arguments=args),
        ToolCall(id="replay", name="crm_undo_change", arguments=args),
        ToolCall(id="extra", name="crm_update_task", arguments={"task_id": extra["task"]["task_id"], "expected_version": 1, "changes": {"state": "completed"}, "reason": "Extra unrelated mutation", "operation_id": "chat-extra-denied"}),
    ])
    assert results[0]["ok"], results
    assert results[0]["result"]["task"]["state"] == "cancelled"
    assert results[1]["ok"] and results[1]["result"]["replayed"]
    assert not results[2]["ok"]
    assert TaskService(people).get(pid, extra["task"]["task_id"])["state"] == "open"
    assert worker(PersonStore(tmp_path)).proposals(pid, state="dismissed")
