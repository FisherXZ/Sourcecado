"""Merge-review regressions for requested entity, deadline, and source authority."""
import sqlite3
import pytest

from coworker import crm_tasks
from coworker.crm_tasks import TaskService
from coworker.people import PersonStore
from coworker.provider import FakeProvider, ToolCall
from coworker.server import create_app
from test_crm_tools import TOKEN, invoke, keep, scripted_chat


@pytest.mark.parametrize("title", [
    "Follow up on pricing", "Work on the deck", "Send the second draft",
    "Ask who may attend", "Watch the band march together", "Send meeting minutes",
])
@pytest.mark.parametrize("due_date", [None, "2026-10-23"])
def test_ordinary_task_topics_do_not_establish_a_deadline(tmp_path, title, due_date):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_create_task", {
        "title": title, "due_date": due_date,
        "operation_id": "merge-topic-create", "reason": "Director request",
    }, f"Remind me to {title}")
    assert ok is (due_date is None), result
    tasks = TaskService(people).list(person_id=pid, timezone="America/Los_Angeles")["tasks"]
    if ok:
        assert len(tasks) == 1 and tasks[0]["title"] == title and tasks[0]["due_date"] is None
        assert tasks[0]["protected_fields"] == ["title"]
    else:
        assert result["code"] == "unverified_director_request" and tasks == []


@pytest.mark.parametrize("timing", [
    "in thirty seconds", "one second from now", "within one second", "in the second hour",
    "May", "March", "May 23", "23 May", "in May", "next March", "October 23", "May23", "October23", "on Mysteryday",
    "on the anniversary", "on the next holiday", "by whenever works", "due whenever works",
])
def test_contextual_time_words_still_require_a_supported_deadline(tmp_path, timing):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_create_task", {
        "title": "Send examples", "operation_id": "merge-context-time", "reason": "Director request",
    }, f"Remind me to send examples {timing}")
    assert not ok and result["code"] == "unverified_director_request", result
    assert TaskService(people).list(person_id=pid, timezone="America/Los_Angeles")["tasks"] == []


@pytest.mark.parametrize("month", ["May", "March"])
@pytest.mark.parametrize("day", ["one", "first", "ten", "tenth", "twenty-first", "thirty first", "the first", "the 23rd"])
@pytest.mark.parametrize("day_first", [False, True])
def test_written_calendar_days_do_not_disambiguate_months_as_prose(tmp_path, month, day, day_first):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    timing = f"{day} {month}" if day_first else f"{month} {day}"
    ok, result = invoke(people, "crm_create_task", {
        "title": "Send examples", "operation_id": "merge-written-month-day", "reason": "Director request",
    }, f"Remind me to send examples {timing} per the plan")
    assert not ok and result["code"] == "unverified_director_request", result
    assert TaskService(people).list(person_id=pid, timezone="America/Los_Angeles")["tasks"] == []


@pytest.mark.parametrize("text", [
    "Remind me to work on Halloween", "Remind me to follow up on Christmas",
    "Remind me to work on the launch day", "Remind me to work on next Friday",
])
def test_topic_verbs_do_not_hide_calendar_or_named_event_on_clauses(tmp_path, text):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_create_task", {
        "title": text.removeprefix("Remind me to "),
        "operation_id": "merge-topic-deadline", "reason": "Director request",
    }, text)
    assert not ok and result["code"] == "unverified_director_request", result
    assert TaskService(people).list(person_id=pid, timezone="America/Los_Angeles")["tasks"] == []


@pytest.mark.parametrize("field, value", [
    ("title", "Call before lunch"),
    ("details", "Ask if Maya can attend; note when she replies, not before"),
    ("description", "Met at the alumni event, not interested until Q3"),
])
def test_explicit_colon_replacements_keep_polarity_words_as_protected_data(tmp_path, field, value):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    args = {"operation_id": "merge-literal-replacement", "reason": "Director request"}
    if field == "description":
        name = "crm_update_description"
        args.update(slot="general", text=value, expected_version=0)
    else:
        name = "crm_update_task"
        task = app.state.tasks.create(pid, title="Original", actor="director",
            operation_id="merge-literal-before", timezone="America/Los_Angeles")["task"]
        args.update(task_id=task["task_id"], changes={field: value}, expected_version=1)
    command_field = "description" if field == "description" else f"task {field}"
    results = scripted_chat(app, pid, f"Change the {command_field} to: {value}", [
        ToolCall(id="save", name=name, arguments=args),
        ToolCall(id="replay", name=name, arguments=args),
        ToolCall(id="second", name=name, arguments={**args, "operation_id": "merge-literal-second"}),
    ])
    assert [result["ok"] for result in results] == [True, True, False], results
    assert results[1]["result"]["replayed"]
    assert results[2]["result"]["code"] == "crm_authority_consumed"
    if field == "description":
        saved = app.state.people.descriptions(pid)["general"]
        assert saved["text"] == value and saved["authority"] == "human" and saved["version"] == 1
    else:
        saved = app.state.tasks.get(pid, task["task_id"])
        assert saved[field] == value and field in saved["protected_fields"] and saved["version"] == 2
        assert saved["state"] == "open"


@pytest.mark.parametrize("template", [
    "Change the {field} to Alumni if approved",
    "Change the {field} if approved to: Alumni",
    "Change the {field} not to: Alumni",
])
@pytest.mark.parametrize("field", ["description", "task title"])
def test_replacement_boundary_does_not_authorize_conditional_or_negated_commands(tmp_path, template, field):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    text = template.format(field=field)
    value = text.split("to", 1)[1].lstrip(": ")
    args = {"operation_id": "merge-literal-condition", "reason": "Director request"}
    if field == "description":
        name = "crm_update_description"
        args.update(slot="general", text=value, expected_version=0)
    else:
        name = "crm_update_task"
        task = TaskService(people).create(pid, title="Original", actor="director",
            operation_id="merge-condition-before", timezone="America/Los_Angeles")["task"]
        args.update(task_id=task["task_id"], changes={"title": value}, expected_version=1)
    ok, result = invoke(people, name, args, text)
    assert not ok and result["code"] == "unverified_director_request", result
    assert people.descriptions(pid)["general"]["version"] == 0
    if field == "task title":
        assert TaskService(people).get(pid, task["task_id"])["title"] == "Original"


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
    "Remind me to send examples this evening", "Remind me to send examples in the morning",
    "Remind me to send examples next business day",
    "Remind me to send examples two days from now", "Remind me to send examples a week from now",
    "Remind me to send examples 24 hours from now",
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


def test_actual_change_id_cannot_mask_a_contradictory_task_id(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    task = tasks.create(pid, title="Call", actor="director", operation_id="merge-mixed-call", timezone="America/Los_Angeles")["task"]
    done = tasks.update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1,
                        actor="director", operation_id="merge-mixed-complete", timezone="America/Los_Angeles")
    ok, result = invoke(people, "crm_undo_change", {
        "change_id": done["change"]["change_id"], "expected_version": 2,
        "operation_id": "merge-mixed-undo", "reason": "Director request",
    }, f"Undo task task_{'0' * 32} change {done['change']['change_id']}")
    assert not ok, result
    assert tasks.get(pid, task["task_id"])["state"] == "completed"


def test_explicit_task_id_supports_field_edit_when_other_tasks_exist(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    first = tasks.create(pid, title="First", actor="director", operation_id="merge-select-first", timezone="America/Los_Angeles")["task"]
    other = tasks.create(pid, title="Other", actor="director", operation_id="merge-select-other", timezone="America/Los_Angeles")["task"]
    value = f"Compare with {other['task_id']} before lunch"
    ok, result = invoke(people, "crm_update_task", {
        "task_id": first["task_id"], "changes": {"details": value}, "expected_version": 1,
        "operation_id": "merge-select-edit", "reason": "Director request",
    }, f"Change task {first['task_id']} details to: {value}")
    assert ok, result
    assert tasks.get(pid, first["task_id"])["details"] == value
    assert tasks.get(pid, other["task_id"])["details"] == ""


def test_explicit_undated_task_keeps_time_words_as_literal_task_text(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_create_task", {
        "title": "Send meeting minutes", "operation_id": "merge-explicit-undated", "reason": "Director request",
    }, "Add an undated task: Send meeting minutes")
    assert ok, result
    assert result["task"]["due_date"] is None and result["task"]["title"] == "Send meeting minutes"


def test_explicit_undated_directive_cannot_replay_a_dated_operation(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    args = {"title": "Send examples", "due_date": "2026-10-23",
            "operation_id": "merge-undated-replay", "reason": "Director request"}
    assert invoke(people, "crm_create_task", args, "Remind me Friday to send examples")[0]
    ok, result = invoke(people, "crm_create_task", args, "Add an undated task: Send examples")
    assert not ok, result
    assert len(TaskService(people).list(person_id=pid, timezone="America/Los_Angeles")["tasks"]) == 1


def test_replacement_task_id_is_data_not_a_target_selector(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    tasks = TaskService(people)
    first = tasks.create(pid, title="First", actor="director", operation_id="merge-data-first", timezone="America/Los_Angeles")["task"]
    tasks.create(pid, title="Other", actor="director", operation_id="merge-data-other", timezone="America/Los_Angeles")
    value = f"Compare with {first['task_id']} before lunch"
    ok, result = invoke(people, "crm_update_task", {
        "task_id": first["task_id"], "changes": {"details": value}, "expected_version": 1,
        "operation_id": "merge-data-edit", "reason": "Director request",
    }, f"Change task details to: {value}")
    assert not ok, result
    assert result["code"] == "ambiguous_task"
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
