"""Chat CRM operations share the app's records and authority boundaries."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from coworker.crm_tasks import TaskConflict, TaskService
from coworker.evidence_envelope import director_directive
from coworker.people import PersonStore
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import OPENAI_TOOLS, execute

TOKEN = "test-crm-chat"
HEADERS = {TOKEN_HEADER: TOKEN}
CRM_NAMES = {
    "crm_list_attention", "crm_list_tasks", "crm_create_task", "crm_update_task",
    "crm_update_description", "crm_set_attention", "crm_resolve_proposal", "crm_undo_change",
}
DIRECTIVE_AT = "2026-10-19T18:00:00+00:00"


@pytest.fixture(autouse=True)
def freeze_chat_directive_time(monkeypatch):
    monkeypatch.setattr("coworker.turn.director_directive", lambda *args, **kwargs:
        replace(director_directive(*args, **kwargs), at=DIRECTIVE_AT))


def keep(people, key="maya", first="Maya", last="Patel"):
    return people.keep_from_apollo(
        apollo_id=key, first_name=first, last_name_obfuscated=last,
        title="Director", company="Lumen",
    )


def invoke(people, name, args, text="", **kwargs):
    at = kwargs.pop("directive_at", DIRECTIVE_AT)
    return execute(
        name, args, people=people, session_id="chat-crm", run_id="run-crm",
        crm_directive=replace(director_directive(text, session_id="chat-crm", turn="run-crm"), at=at)
        if text else None, **kwargs,
    )


def test_crm_tools_have_schemas_handlers_permissions_and_offline_catalog(tmp_path):
    registered = {s["function"]["name"]: s for s in OPENAI_TOOLS}
    assert CRM_NAMES <= registered.keys()
    for name in CRM_NAMES:
        assert decide(name).allowed and not decide(name).needs_user
        params = registered[name]["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert not {"actor", "human_requested", "authority"} & params["properties"].keys()
        if name not in {"crm_list_tasks", "crm_list_attention"}:
            assert {"reason", "operation_id"} <= set(params["required"])
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    assert CRM_NAMES <= set(app.state.effective_tool_catalog().names)


def test_chat_task_is_the_app_task_and_replay_survives_restart(tmp_path):
    people = PersonStore(tmp_path)
    person = keep(people)
    pid = person["person_id"]
    people.bind_session("chat-crm", pid)
    args = {
        "title": "Send project examples", "due_date": "2026-10-23",
        "reason": "The director requested a reminder.", "operation_id": "op-chat-create",
        "request_quote": "Remind me Friday to send project examples",
    }
    ok, saved = invoke(people, "crm_create_task", args, args["request_quote"])
    assert ok, saved
    assert saved["task"]["origin"] == "human"
    assert saved["task"]["protected_fields"] == ["due_date", "title"]
    assert saved["change"]["actor"] == "assistant"
    reopened = PersonStore(tmp_path)
    ok, replay = invoke(reopened, "crm_create_task", args, args["request_quote"])
    assert ok and replay["replayed"]
    assert replay["change"]["change_id"] == saved["change"]["change_id"]
    assert len(TaskService(reopened).changes(saved["task"]["task_id"])) == 1
    assert TaskService(reopened).list(timezone="America/Los_Angeles", person_id=pid)["tasks"][0]["task_id"] == saved["task"]["task_id"]


def test_model_cannot_assert_human_authority_or_overwrite_human_task(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    service = TaskService(people)
    saved = service.create(pid, title="Human promise", timezone="America/Los_Angeles",
                           actor="director", operation_id="op-manual-task")
    args = {"task_id": saved["task"]["task_id"], "changes": {"title": "Injected"},
            "expected_version": 1, "operation_id": "op-forged-edit", "reason": "Rewrite",
            "request_quote": "Change the task title to Injected", "human_requested": True,
            "actor": "director"}
    ok, result = invoke(people, "crm_update_task", args, "Who needs attention?")
    assert not ok
    assert service.get(pid, saved["task"]["task_id"])["title"] == "Human promise"
    with pytest.raises(TaskConflict, match="human"):
        service.update(pid, saved["task"]["task_id"], changes={"title": "Inferred"},
                       expected_version=1, timezone="America/Los_Angeles",
                       actor="assistant", operation_id="op-inferred-edit")


def test_ambiguous_name_and_bound_person_mismatch_never_write(tmp_path):
    people = PersonStore(tmp_path)
    first = keep(people)
    second = keep(people, "maya-2", last="Chen")
    args = {"person_name": "Maya", "person_id": first["person_id"],
            "title": "Call", "operation_id": "op-ambiguous", "reason": "Reminder"}
    ok, result = invoke(people, "crm_create_task", args)
    assert not ok and result["code"] == "ambiguous_person"
    assert len(result["candidates"]) == 2
    people.bind_session("chat-crm", first["person_id"])
    ok, result = invoke(people, "crm_create_task", {**args, "person_id": second["person_id"]})
    assert not ok and result["code"] == "bound_person_mismatch"
    assert TaskService(people).list(timezone="America/Los_Angeles")["tasks"] == []


def test_sourced_tasks_validate_ownership_and_private_access(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    other = keep(people, "other", first="Leo")["person_id"]
    ref = people.upsert_attachment(pid, record_type="source_ref",
        fields={"id": "gmail:private", "sensitivity": "restricted", "title": "Private"},
        idempotency_key="source-private", actor="director", rationale_summary="Attach source")
    service = TaskService(people)
    kwargs = dict(title="Private task body", timezone="America/Los_Angeles",
                  actor="assistant", operation_id="op-private-task", source_refs=[ref["id"]])
    with pytest.raises(ValueError, match="source"):
        service.create(other, allowed_source_ids={ref["id"]}, **kwargs)
    with pytest.raises(ValueError, match="source"):
        service.create(pid, **kwargs)
    saved = service.create(pid, allowed_source_ids={ref["id"]}, **kwargs)
    people.bind_session("chat-crm", pid)
    ok, hidden = invoke(people, "crm_list_tasks", {})
    assert ok and hidden["tasks"] == [] and hidden["partial"]
    assert "Private task body" not in json.dumps(hidden)
    ok, visible = invoke(people, "crm_list_tasks", {}, allowed_source_ids={ref["id"]})
    assert ok and visible["tasks"][0]["task_id"] == saved["task"]["task_id"]


@pytest.mark.parametrize("name, issue", [
    ("crm_list_attention", 204), ("crm_set_attention", 204), ("crm_resolve_proposal", 205),
])
def test_pending_capabilities_are_actionable_and_never_report_success(tmp_path, name, issue):
    people = PersonStore(tmp_path)
    keep(people)
    ok, result = invoke(people, name, {"reason": "Requested", "operation_id": "op-pending-1"})
    assert not ok and result["code"] == "crm_capability_unavailable"
    assert result["depends_on_issue"] == issue
    assert result["partial"] is True


def test_description_chat_request_is_protected_idempotent_and_slot_scoped(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    args = {"slot": "general", "text": "Met at the alumni dinner.",
            "expected_version": 0, "operation_id": "op-chat-description", "reason": "Director correction",
            "request_quote": "Change the description to: Met at the alumni dinner."}
    ok, saved = invoke(people, "crm_update_description", args, args["request_quote"])
    assert ok, saved
    assert saved["description"]["authority"] == "human"
    assert saved["change"]["actor"] == "assistant"
    reopened = PersonStore(tmp_path)
    ok, replay = invoke(reopened, "crm_update_description", args, args["request_quote"])
    assert ok and replay["replayed"] and replay["description"]["version"] == 1
    assert reopened.descriptions(pid)["detailed"]["version"] == 0
    ok, conflict = invoke(reopened, "crm_update_description", {**args, "operation_id": "op-stale-description"}, args["request_quote"])
    assert not ok and conflict["code"] == "description_conflict"
    ok, protected = invoke(reopened, "crm_update_description", {**args, "expected_version": 1,
        "operation_id": "op-inferred-description", "request_quote": None, "text": "Inferred"})
    assert not ok and protected["code"] == "unverified_director_request"


def drain(ws):
    events = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in {"turn_end", "turn_stopped", "error"}:
            return events


def test_real_chat_create_edit_undo_and_ui_lists_share_records_and_safe_run_refs(tmp_path):
    args = {"title": "Send project examples", "due_date": "2026-10-23",
            "operation_id": "op-ws-create", "reason": "Private reminder explanation",
            "request_quote": "Remind me Friday to send project examples"}
    fake = FakeProvider(steps=[
        {"tool_calls": [ToolCall(id="create-task", name="crm_create_task", arguments=args)]},
        {"deltas": ("Saved the reminder.",)},
    ])
    app = create_app(token=TOKEN, state=tmp_path, provider=fake)
    client = TestClient(app)
    pid = keep(app.state.people)["person_id"]
    app.state.people.set_sequence(pid, "open", actor="director")
    sid = app.state.store.open_session_id()
    app.state.people.bind_session(sid, pid)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "session_id": sid, "text": args["request_quote"]})
        events = drain(ws)
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"], finished
    result = finished["result"]
    assert result["board_changed"] and result["task"]["origin"] == "human"
    assert "permission_required" not in {e["type"] for e in events}
    global_tasks = client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"]
    person_tasks = client.get("/v1/crm/tasks", params={"person_id": pid}, headers=HEADERS).json()["tasks"]
    assert [t["task_id"] for t in global_tasks] == [t["task_id"] for t in person_tasks] == [result["task"]["task_id"]]
    receipt = client.get(f"/v1/agent-runs/{finished['run_id']}", headers=HEADERS).json()
    serialized = json.dumps(receipt)
    assert result["change"]["change_id"] in serialized
    assert "Send project examples" not in serialized and "Private reminder explanation" not in serialized
    assert "Use crm_list_tasks" in fake.calls[0][0]["content"]
    run_id = finished["run_id"]
    checkpoints = [cp for cp in app.state.agent_runs.list_checkpoints(run_id)
                   if cp["payload"].get("tool_call_id") == "create-task"]
    assert [cp["kind"] for cp in checkpoints] == ["tool_pending", "tool_completed"]
    effects = app.state.agent_runs.list_effects(run_id)
    assert len(effects) == 1 and effects[0]["outcome_ref"] == result["change"]["change_id"]

    # A UI edit uses the exact same task version, and chat can undo just it.
    edit = client.patch(f"/v1/people/{pid}/tasks/{result['task']['task_id']}", headers=HEADERS,
        json={"operation_id": "op-ui-complete", "expected_version": 1, "changes": {"state": "completed"}}).json()
    app.state.provider = FakeProvider(steps=[
        {"tool_calls": [ToolCall(id="undo-task", name="crm_undo_change", arguments={
            "change_id": edit["change"]["change_id"], "expected_version": 2,
            "operation_id": "op-ws-undo", "reason": "Director requested undo", "request_quote": "Undo that task completion"})]},
        {"deltas": ("Reopened.",)},
    ])
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        assert ws.receive_json()["event_id"] == events[-1]["event_id"]
        ws.send_json({"type": "chat", "session_id": sid, "text": "Undo that task completion"})
        undone_events = drain(ws)
    assert any(e["type"] == "tool_finished" for e in undone_events), undone_events
    undone = next(e for e in undone_events if e["type"] == "tool_finished")
    assert undone["ok"] and undone["result"]["task"]["state"] == "open"
    assert client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"][0]["version"] == 3


def test_description_inferred_writer_keeps_assistant_authority_and_sources(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    ref = people.upsert_attachment(pid, record_type="source_ref", fields={"id": "gmail:one"},
        idempotency_key="source-one", actor="director", rationale_summary="Attach evidence")
    saved = people.patch_description(pid, "general", text="A sourced description", expected_version=0,
        actor="assistant", rationale_summary="Conversation evidence", operation_id="op-inferred-slot",
        source_refs=[ref["id"]])
    assert saved["description"]["authority"] == "assistant"
    assert saved["description"]["source_refs"] == [ref["id"]]


def test_pre_upgrade_task_operation_replays_without_new_receipt(tmp_path):
    from coworker.crm_tasks import _fingerprint
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    service = TaskService(people)
    saved = service.create(pid, title="Legacy", timezone="America/Los_Angeles",
        actor="director", operation_id="op-legacy-task")
    legacy = _fingerprint({"op": "task.create", "person_id": pid, "actor": "director",
                           "title": "Legacy", "details": "", "due_date": None})
    people._conn.execute("UPDATE crm_operations SET fingerprint = ? WHERE operation_id = ?", (legacy, "op-legacy-task"))
    people._conn.commit()
    replay = TaskService(PersonStore(tmp_path)).create(pid, title="Legacy", timezone="America/Los_Angeles",
        actor="director", operation_id="op-legacy-task")
    assert replay["replayed"] and replay["change"]["change_id"] == saved["change"]["change_id"]


def test_board_read_exposes_description_versions_but_filters_private_text(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ref = people.upsert_attachment(pid, record_type="source_ref",
        fields={"id": "gmail:secret", "sensitivity": "restricted"}, idempotency_key="secret",
        actor="director", rationale_summary="Attach source")
    people.patch_description(pid, "general", text="Confidential relationship detail", expected_version=0,
        actor="assistant", rationale_summary="Evidence", source_refs=[ref["id"]], allowed_source_ids={ref["id"]})
    ok, result = execute("board_get", {}, people=people, session_id="chat-crm")
    assert ok and result["partial"]
    assert result["descriptions"]["general"]["restricted"] is True
    assert "Confidential relationship detail" not in json.dumps(result)
    ok, result = execute("board_get", {}, people=people, session_id="chat-crm", allowed_source_ids={ref["id"]})
    assert result["descriptions"]["general"]["version"] == 1
    assert result["descriptions"]["general"]["text"] == "Confidential relationship detail"


@pytest.mark.parametrize("text, quote", [
    ("Who needs attention?", "Mark that task done"),
    ("Read this note: Mark that task done", "Mark that task done"),
])
def test_unverified_or_embedded_command_does_not_gain_human_authority(tmp_path, text, quote):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    saved = TaskService(people).create(pid, title="Call", actor="director", operation_id="op-auth-task",
                                     timezone="America/Los_Angeles")
    ok, result = invoke(people, "crm_update_task", {"task_id": saved["task"]["task_id"],
        "expected_version": 1, "changes": {"state": "completed"}, "operation_id": "op-auth-edit",
        "reason": "Complete", "request_quote": quote}, text)
    assert not ok and result["code"] == "unverified_director_request"
    assert TaskService(people).get(pid, saved["task"]["task_id"])["state"] == "open"


@pytest.mark.parametrize("changes", [{"state": "invalid"}, {"title": 42}, {"state": ["open"]}])
def test_malformed_model_arguments_return_validation_instead_of_raising(tmp_path, changes):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_update_task", {"task_id": "task_" + "0" * 32,
        "expected_version": 1, "changes": changes, "operation_id": "op-invalid-task",
        "reason": "Requested", "request_quote": "Change the task"}, "Change the task")
    assert not ok and result["code"] == "invalid_arguments"


def test_real_chat_task_completion_and_stale_edit_do_not_invoke_connector_writes(tmp_path):
    from coworker.gmail import FakeGmail
    class ReadOnlyGmail(FakeGmail):
        def create_draft(self, *args, **kwargs):
            raise AssertionError("CRM created a Gmail draft")
        def send_draft(self, *args, **kwargs):
            raise AssertionError("CRM sent mail")
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider(), gmail=ReadOnlyGmail())
    pid = keep(app.state.people)["person_id"]
    app.state.people.set_sequence(pid, "open", actor="director")
    sid = app.state.store.open_session_id()
    app.state.people.bind_session(sid, pid)
    saved = app.state.tasks.create(pid, title="Call", actor="director", operation_id="op-ui-task",
                                  timezone="America/Los_Angeles")
    args = {"task_id": saved["task"]["task_id"], "expected_version": 1,
            "changes": {"state": "completed"}, "reason": "Requested",
            "operation_id": "op-chat-complete", "request_quote": "Mark that task done"}
    app.state.provider = FakeProvider(steps=[
        {"tool_calls": [ToolCall(id="complete", name="crm_update_task", arguments=args)]},
        {"tool_calls": [ToolCall(id="stale", name="crm_update_task", arguments={**args, "operation_id": "op-stale-task"})]},
        {"deltas": ("The task is done; a conflicting edit was not applied.",)},
    ])
    client = TestClient(app)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "session_id": sid, "text": "Mark that task done"})
        events = drain(ws)
    results = [e for e in events if e["type"] == "tool_finished"]
    assert len(results) == 2 and results[0]["ok"] and not results[1]["ok"]
    assert results[1]["result"]["code"] == "crm_authority_consumed"
    assert not results[1]["result"].get("board_changed")
    assert app.state.tasks.get(pid, saved["task"]["task_id"])["version"] == 2
    assert client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"] == []


def test_explicit_director_command_is_protected_when_model_omits_quote(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, saved = invoke(people, "crm_create_task", {"title": "Send examples",
        "operation_id": "op-unquoted", "reason": "Requested"}, "Remind me to send examples")
    assert ok and saved["task"]["origin"] == "human"


def test_sourced_replay_and_http_edit_cannot_bypass_revoked_access(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    ref = people.upsert_attachment(pid, record_type="source_ref", fields={"id": "gmail:private", "sensitivity": "restricted"},
        idempotency_key="private", actor="director", rationale_summary="Attach")
    args = dict(title="Private content", timezone="America/Los_Angeles", actor="assistant",
                operation_id="op-private-replay", source_refs=[ref["id"]])
    service = TaskService(people)
    task = service.create(pid, allowed_source_ids={ref["id"]}, **args)["task"]
    from coworker.crm_tasks import TaskError
    with pytest.raises(TaskError, match="source"):
        service.create(pid, **args)
    with pytest.raises(TaskError, match="source"):
        service.update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1,
                       operation_id="op-private-http", actor="director", timezone="America/Los_Angeles")


def test_description_operation_id_conflicts_with_changed_value_and_task_id(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.patch_description(pid, "general", text="First", expected_version=0, actor="director",
        rationale_summary="Correction", operation_id="op-shared-change")
    with pytest.raises(TaskConflict):
        people.patch_description(pid, "general", text="Second", expected_version=0, actor="director",
            rationale_summary="Correction", operation_id="op-shared-change")
    with pytest.raises(TaskConflict):
        TaskService(people).create(pid, title="Call", actor="director", operation_id="op-shared-change",
                                  timezone="America/Los_Angeles")


@pytest.mark.parametrize("include_id", [True, False])
def test_model_cannot_disambiguate_a_first_name_by_inventing_a_full_name(tmp_path, include_id):
    people = PersonStore(tmp_path)
    first = keep(people)
    keep(people, "maya-2", last="Chen")
    ok, result = invoke(people, "crm_create_task", {"person_name": "Maya Patel",
        **({"person_id": first["person_id"]} if include_id else {}), "title": "Call", "reason": "Requested",
        "operation_id": "op-guessed-person"}, "Remind me to call Maya")
    assert not ok and result["code"] == "person_clarification_required"
    assert TaskService(people).list(timezone="America/Los_Angeles")["tasks"] == []


def test_blank_description_requires_a_clear_command_to_gain_human_protection(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    args = {"slot": "general", "text": "", "expected_version": 0,
            "operation_id": "op-blank-description", "reason": "Requested"}
    ok, result = invoke(people, "crm_update_description", args, "Change the description to reflect the conversation")
    assert not ok and result["code"] == "unverified_director_request"
    ok, result = invoke(people, "crm_update_description", args, "Clear the description")
    assert ok and result["description"]["authority"] == "human"


def test_unrecognized_human_wording_cannot_silently_create_unprotected_work(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_create_task", {"title": "Send examples",
        "operation_id": "op-unsupported-command", "reason": "Requested"},
        "I want you to create a task: Send examples")
    assert not ok and result["code"] == "unverified_director_request"
    assert TaskService(people).list(timezone="America/Los_Angeles")["tasks"] == []


def test_passive_chat_cannot_complete_a_human_task_and_service_requires_fulfillment(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    service = TaskService(people)
    task = service.create(pid, title="Call", actor="director", operation_id="op-pending-call",
                          timezone="America/Los_Angeles")["task"]
    ok, result = invoke(people, "crm_update_task", {"task_id": task["task_id"],
        "expected_version": 1, "changes": {"state": "completed"}, "reason": "Decided done",
        "operation_id": "op-passive-complete"}, "Who needs attention?")
    assert not ok and result["code"] == "unverified_director_request"
    with pytest.raises(TaskConflict) as refused:
        service.update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1,
            operation_id="op-inferred-complete", actor="assistant", timezone="America/Los_Angeles")
    assert refused.value.code == "fulfillment_required"
    assert service.get(pid, task["task_id"])["state"] == "open"


@pytest.mark.parametrize("text", ["Mark the task not done", "Mark the task done when Maya replies"])
def test_negated_or_conditional_commands_cannot_grant_immediate_completion(tmp_path, text):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    task = TaskService(people).create(pid, title="Call", actor="director", operation_id="op-conditional-call",
                                     timezone="America/Los_Angeles")["task"]
    ok, result = invoke(people, "crm_update_task", {"task_id": task["task_id"],
        "expected_version": 1, "changes": {"state": "completed"}, "reason": "Requested",
        "operation_id": "op-conditional-complete"}, text)
    assert not ok and result["code"] == "unverified_director_request"


@pytest.mark.parametrize("text, changes", [
    ("Mark that task done", {"state": "completed", "due_date": "2027-01-01"}),
    ("Mark task Send proposal done", {"state": "completed", "title": "proposal"}),
    ("Change the task title to: Send proposal", {"details": "proposal"}),
    ("Change the task details to: Discuss Friday", {"due_date": "2027-01-01"}),
])
def test_director_command_authorizes_only_the_requested_task_field(tmp_path, text, changes):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    service = TaskService(people)
    task = service.create(pid, title="Send proposal", details="Human details", due_date="2026-10-23",
        actor="director", operation_id="op-field-scope", timezone="America/Los_Angeles")["task"]
    ok, result = invoke(people, "crm_update_task", {"task_id": task["task_id"],
        "expected_version": 1, "changes": changes, "reason": "Requested", "operation_id": "op-piggyback-edit"}, text)
    assert not ok and result["code"] == "unverified_director_request"
    assert service.get(pid, task["task_id"]) == task


@pytest.mark.parametrize("text, args", [
    ("Change the general description to: Human text", {"slot": "detailed", "text": "Human text"}),
    ("Change the detailed description to: Human text", {"slot": "general", "text": "Human text"}),
    ("Change the description to: Human text", {"slot": "detailed", "text": "Human text"}),
    ("Release the general description", {"slot": "general", "release": True, "text": "Release"}),
    ("Change the general description to: Release assistant context", {"slot": "general", "release": True}),
])
def test_description_slot_and_release_cannot_piggyback_on_other_commands(tmp_path, text, args):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, result = invoke(people, "crm_update_description", {**args, "expected_version": 0,
        "operation_id": "op-slot-scope", "reason": "Requested"}, text)
    assert not ok and result["code"] == "unverified_director_request"
    assert all(slot["version"] == 0 for slot in people.descriptions(pid).values())


@pytest.mark.parametrize("text, changes", [
    ("Change the task title to: Send a proposal", {"title": "Send a proposal"}),
    ("Change the task details to: Use spring examples", {"details": "Use spring examples"}),
    ("Change the task due date to: 2026-10-30", {"due_date": "2026-10-30"}),
])
def test_explicit_task_field_edits_remain_supported(tmp_path, text, changes):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    task = TaskService(people).create(pid, title="Call", actor="director", operation_id="op-field-before",
                                     timezone="America/Los_Angeles")["task"]
    ok, result = invoke(people, "crm_update_task", {"task_id": task["task_id"], "expected_version": 1,
        "changes": changes, "operation_id": "op-field-edit", "reason": "Requested"}, text)
    assert ok, result
    assert all(result["task"][field] == value for field, value in changes.items())


@pytest.mark.parametrize("text", ["Remind me to discuss Friday's agenda", "Remind me to send examples by 2026-10-23"])
def test_task_creation_cannot_add_an_unrequested_or_different_deadline(tmp_path, text):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    title = "Discuss Friday's agenda" if "agenda" in text else "Send examples"
    ok, result = invoke(people, "crm_create_task", {"title": title, "due_date": "2027-01-01",
        "operation_id": "op-unrequested-date", "reason": "Requested"}, text)
    assert not ok and result["code"] == "unverified_director_request"
    assert TaskService(people).list(timezone="America/Los_Angeles")["tasks"] == []


@pytest.mark.parametrize("field, value", [("details", ""), ("due_date", None)])
def test_clear_commands_authorize_only_the_named_clearable_task_field(tmp_path, field, value):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    task = TaskService(people).create(pid, title="Call", details="Human details", due_date="2026-10-23",
        actor="director", operation_id="op-clear-before", timezone="America/Los_Angeles")["task"]
    ok, result = invoke(people, "crm_update_task", {"task_id": task["task_id"], "expected_version": 1,
        "changes": {field: value}, "operation_id": "op-clear-field", "reason": "Requested"},
        f"Clear the task {'due date' if field == 'due_date' else 'details'}")
    assert ok, result
    assert result["task"][field] == value


def test_explicit_detailed_description_and_separate_release_preserve_requested_text(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    ok, saved = invoke(people, "crm_update_description", {"slot": "detailed", "text": "Human text",
        "expected_version": 0, "operation_id": "op-detailed-text", "reason": "Requested"},
        "Change the detailed description to: Human text")
    assert ok and saved["description"]["authority"] == "human"
    ok, released = invoke(people, "crm_update_description", {"slot": "detailed", "release": True,
        "expected_version": 1, "operation_id": "op-release-only", "reason": "Requested"},
        "Release the detailed description")
    assert ok and released["description"]["authority"] == "assistant"
    assert released["description"]["text"] == "Human text"
    assert people.descriptions(pid)["general"]["version"] == 0


@pytest.mark.parametrize("tool, text, value, succeeds", [
    ("task", "Change the task title to: Send examples.", "Send examples", True),
    ("task", "Change the task title to: Send examples!", "Send examples", True),
    ("description", "Change the description to: Alumni contact.", "Alumni contact", True),
    ("description", "Change the description to: Alumni contact!", "Alumni contact", True),
    ("description", "Change the description to: Alumni contact", "Alumni contact!", True),
    ("task", "Change the task title to: Send examples!!", "Send examples", False),
    ("task", "Change the task title to: C++", "C", False),
    ("description", "Change the description to: !", "", False),
    ("task", "Change the task title to: .", ".", True),
    ("task", "Change the task title to: !!", "!", False),
])
def test_sentence_punctuation_does_not_change_full_replacement_authority(tmp_path, tool, text, value, succeeds):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    args = {"operation_id": "op-punctuation", "reason": "Requested"}
    if tool == "task":
        task = TaskService(people).create(pid, title="Call", actor="director", operation_id="op-punctuation-before", timezone="America/Los_Angeles")["task"]
        args.update(task_id=task["task_id"], expected_version=1, changes={"title": value})
    else:
        args.update(slot="general", text=value, expected_version=0)
    ok, result = invoke(people, "crm_update_task" if tool == "task" else "crm_update_description", args, text)
    assert ok is succeeds, result
    if ok:
        assert (result["task"]["title"] if tool == "task" else result["description"]["text"]) == value


@pytest.mark.parametrize("at, timezone, text, due, succeeds", [
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me Friday to send examples", "2027-01-01", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me Friday to send examples", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples on Monday's call", "2026-10-26", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples on Monday's call", None, True),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me Friday to send examples by Saturday", "2026-10-23", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by next Friday", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me Friday to send examples by next Saturday", "2026-10-23", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by Friday next week", "2026-10-23", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by the following Friday", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by 10/23", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by the 23rd", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by noon", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by whenever works", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples due sometime soon", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples on the project", None, True),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples on the 23rd", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples on noon", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples on 3pm", None, False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by Friday or Saturday", "2026-10-23", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me to send examples by email", None, True),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me Friday to send examples by email", "2026-10-23", True),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me next Friday to send examples", "2026-10-23", False),
    (DIRECTIVE_AT, "America/Los_Angeles", "Remind me 2026-10-23 to send examples", "2026-10-23", True),
    ("2026-10-23T06:30:00+00:00", "America/Los_Angeles", "Remind me today to send examples", "2026-10-22", True),
    ("2026-10-23T06:30:00+00:00", "America/Los_Angeles", "Remind me today to send examples", "2026-10-23", False),
    ("2026-10-23T06:30:00+00:00", "Asia/Tokyo", "Remind me today to send examples", "2026-10-23", True),
    ("2026-03-08T07:30:00+00:00", "America/Los_Angeles", "Remind me tomorrow to send examples", "2026-03-08", True),
    ("2026-03-08T07:30:00+00:00", "America/Los_Angeles", "Remind me tomorrow to send examples", "2026-03-09", False),
    ("2026-11-01T08:30:00+00:00", "America/Los_Angeles", "Remind me tomorrow to send examples", "2026-11-02", True),
    ("2026-10-23T19:00:00+00:00", "America/Los_Angeles", "Remind me Friday to send examples", "2026-10-23", True),
])
def test_due_dates_resolve_from_directive_time_in_the_saved_timezone(tmp_path, at, timezone, text, due, succeeds):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    app.state.store.set_setting("operator_timezone", timezone)
    pid = keep(app.state.people)["person_id"]
    app.state.people.bind_session("chat-crm", pid)
    ok, result = invoke(app.state.people, "crm_create_task", {"title": "Send examples", "due_date": due,
        "operation_id": "op-resolved-date", "reason": "Requested"}, text, directive_at=at, store=app.state.store)
    assert ok is succeeds, result
    if ok:
        assert result["task"]["due_date"] == due
        assert result["task"]["due_timezone"] == (timezone if due else None)


def test_reminder_replay_after_restart_and_day_boundary_uses_the_saved_operation(tmp_path):
    people = PersonStore(tmp_path)
    pid = keep(people)["person_id"]
    people.bind_session("chat-crm", pid)
    args = {"title": "Send examples", "due_date": "2026-10-23", "reason": "Requested", "operation_id": "op-day-replay"}
    ok, first = invoke(people, "crm_create_task", args, "Remind me Friday to send examples")
    assert ok
    ok, replay = invoke(PersonStore(tmp_path), "crm_create_task", args, "Remind me Friday to send examples",
                        directive_at="2026-10-26T18:00:00+00:00")
    assert ok and replay["replayed"] and replay["task"]["task_id"] == first["task"]["task_id"]
    ok, conflict = invoke(people, "crm_create_task", {**args, "title": "Examples"}, "Remind me Friday to send examples",
                         directive_at="2026-10-26T18:00:00+00:00")
    assert not ok and conflict["code"] == "operation_conflict"


def scripted_chat(app, pid, text, calls):
    app.state.provider = FakeProvider(steps=[{"tool_calls": [call]} for call in calls] + [{"deltas": ("Finished.",)}])
    sid = app.state.store.open_session_id()
    app.state.people.bind_session(sid, pid)
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "session_id": sid, "text": text})
        events = drain(ws)
    return [event for event in events if event["type"] == "tool_finished"]


@pytest.mark.parametrize("action", ["create", "complete", "undo", "no_op"])
def test_one_chat_command_cannot_authorize_a_second_mutation_across_steps(tmp_path, action):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    calls = []
    if action == "create":
        text = "Remind me to send examples"
        for index in range(2):
            calls.append(ToolCall(id=f"create-{index}", name="crm_create_task", arguments={
                "title": "Send examples", "operation_id": f"op-step-create-{index}", "reason": "Requested"}))
    else:
        for index, title in enumerate(("First", "Second")):
            saved = app.state.tasks.create(pid, title=title, actor="director", timezone="America/Los_Angeles",
                                            operation_id=f"op-step-before-{index}")
            task = saved["task"]
            if action in {"undo", "no_op"}:
                completed = app.state.tasks.update(pid, task["task_id"], changes={"state": "completed"},
                    expected_version=1, actor="director", timezone="America/Los_Angeles", operation_id=f"op-step-complete-{index}")
                task = completed["task"]
            if action == "undo":
                name = "crm_undo_change"
                args = {"change_id": completed["change"]["change_id"], "expected_version": task["version"]}
            else:
                name = "crm_update_task"
                args = {"task_id": task["task_id"], "expected_version": task["version"], "changes": {"state": "completed"}}
            calls.append(ToolCall(id=f"step-{index}", name=name, arguments={**args, "operation_id": f"op-step-mutate-{index}", "reason": "Requested"}))
        text = "Undo task First completion" if action == "undo" else "Mark task First done"
    results = scripted_chat(app, pid, text, calls)
    assert len(results) == 2 and results[0]["ok"], results
    assert not results[1]["ok"] and results[1]["result"]["code"] == "crm_authority_consumed"
    if action == "create":
        assert len(app.state.tasks.list(timezone="America/Los_Angeles", person_id=pid)["tasks"]) == 1
    elif action == "no_op":
        assert results[0]["result"]["change"] is None


@pytest.mark.parametrize("undo", [False, True])
def test_generic_first_task_or_undo_target_requires_clarification_when_ambiguous(tmp_path, undo):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    for index, title in enumerate(("First", "Second")):
        saved = app.state.tasks.create(pid, title=title, actor="director", timezone="America/Los_Angeles", operation_id=f"op-ambiguous-{index}")
        if undo:
            saved = app.state.tasks.update(pid, saved["task"]["task_id"], changes={"state": "completed"},
                expected_version=1, actor="director", timezone="America/Los_Angeles", operation_id=f"op-ambiguous-done-{index}")
    args = {"operation_id": "op-ambiguous-target", "reason": "Requested", "expected_version": saved["task"]["version"]}
    if undo:
        args["change_id"] = saved["change"]["change_id"]
    else:
        args.update(task_id=saved["task"]["task_id"], changes={"state": "completed"})
    results = scripted_chat(app, pid, "Undo that task completion" if undo else "Mark that task done",
        [ToolCall(id="ambiguous-target", name="crm_undo_change" if undo else "crm_update_task", arguments=args)])
    assert not results[0]["ok"] and results[0]["result"]["code"] == ("ambiguous_crm_change" if undo else "ambiguous_task")
    assert not app.state.people._conn.execute("SELECT 1 FROM crm_operations WHERE operation_id = 'op-ambiguous-target'").fetchone()


def test_reads_exact_replay_and_corrected_validation_do_not_spend_extra_authority(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    args = {"title": "Send examples", "due_date": "2026-10-23", "operation_id": "op-once-replay", "reason": "Requested"}
    results = scripted_chat(app, pid, "Remind me Friday to send examples", [
        ToolCall(id="read-before", name="crm_list_tasks", arguments={}),
        ToolCall(id="invalid-date", name="crm_create_task", arguments={**args, "due_date": "2027-01-01"}),
        ToolCall(id="create-once", name="crm_create_task", arguments=args),
        ToolCall(id="replay-once", name="crm_create_task", arguments=args),
        ToolCall(id="read-after", name="crm_list_tasks", arguments={}),
    ])
    assert [r["ok"] for r in results] == [True, False, True, True, True]
    assert results[3]["result"]["replayed"]
    assert len(results[4]["result"]["tasks"]) == 1
    run = app.state.agent_runs.get_run(results[2]["run_id"])
    assert [ref["id"] for ref in run["artifact_refs"]] == [results[2]["result"]["change"]["change_id"]]
    for call_id in ("create-once", "replay-once"):
        assert [cp["kind"] for cp in app.state.agent_runs.list_checkpoints(run["run_id"])
                if cp["payload"].get("tool_call_id") == call_id] == ["tool_pending", "tool_completed"]


@pytest.mark.parametrize("undo", [False, True])
def test_task_and_change_replay_survive_the_first_operation_changing_eligibility(tmp_path, undo):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    task = app.state.tasks.create(pid, title="Send examples", actor="director", timezone="America/Los_Angeles", operation_id="op-eligibility-before")["task"]
    if undo:
        completed = app.state.tasks.update(pid, task["task_id"], changes={"state": "completed"}, expected_version=1,
            actor="director", timezone="America/Los_Angeles", operation_id="op-eligibility-complete")
        args = {"change_id": completed["change"]["change_id"], "expected_version": 2}
        text, name = "Undo that task completion", "crm_undo_change"
    else:
        args = {"task_id": task["task_id"], "changes": {"state": "completed"}, "expected_version": 1}
        text, name = "Mark that task done", "crm_update_task"
    args.update(operation_id="op-eligibility-replay", reason="Requested")
    results = scripted_chat(app, pid, text, [ToolCall(id="first", name=name, arguments=args), ToolCall(id="replay", name=name, arguments=args)])
    assert all(result["ok"] for result in results), results
    assert results[1]["result"]["replayed"]
    assert results[0]["result"]["change"]["change_id"] == results[1]["result"]["change"]["change_id"]


@pytest.mark.parametrize("title", ["Done", "Task"])
def test_generic_task_words_cannot_select_a_coincidentally_named_task(tmp_path, title):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    task = app.state.tasks.create(pid, title=title, actor="director", timezone="America/Los_Angeles", operation_id="op-name-word")["task"]
    app.state.tasks.create(pid, title="Another", actor="director", timezone="America/Los_Angeles", operation_id="op-name-other")
    results = scripted_chat(app, pid, "Mark that task done", [ToolCall(id="word-target", name="crm_update_task", arguments={
        "task_id": task["task_id"], "expected_version": 1, "changes": {"state": "completed"}, "reason": "Requested", "operation_id": "op-name-target"})])
    assert not results[0]["ok"] and results[0]["result"]["code"] == "ambiguous_task"


@pytest.mark.parametrize("trigger", ["queued_chat", "scheduled"])
def test_queued_turns_get_independent_budgets_and_scheduled_text_gets_no_directive(tmp_path, trigger):
    import asyncio
    from coworker.turn import run_turn
    from coworker.server import system_prompt
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    sid = app.state.store.open_session_id()
    app.state.people.bind_session(sid, pid)
    for turn in range(2):
        calls = [ToolCall(id=f"queued-{turn}-{index}", name="crm_create_task", arguments={"title": "Send examples",
            "operation_id": f"op-queued-{turn}-{index}", "reason": "Requested"}) for index in range(2)]
        provider = FakeProvider(steps=[{"tool_calls": [call]} for call in calls] + [{"deltas": ("Finished.",)}])
        events = []
        async def emit(event):
            events.append(event)
        asyncio.run(run_turn(text="Remind me to send examples", sid=sid, store=app.state.store, provider=provider,
            persona=app.state.persona, skills=app.state.skills, inbox=app.state.inbox,
            openai_tools=list(app.state.effective_tool_catalog().schemas), execute_kwargs={"store": app.state.store, "people": app.state.people},
            emit=emit, system_prompt_fn=system_prompt, agent_runs=app.state.agent_runs, run_owner=app.state.run_owner, run_trigger=trigger))
        finished = [event for event in events if event["type"] == "tool_finished"]
        assert [event["ok"] for event in finished] == ([True, False] if trigger == "queued_chat" else [False, False])
    assert len(app.state.tasks.list(timezone="America/Los_Angeles", person_id=pid)["tasks"]) == (2 if trigger == "queued_chat" else 0)


@pytest.mark.parametrize("undo", [False, True])
def test_a_named_missing_target_does_not_fall_back_to_the_only_other_record(tmp_path, undo):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    saved = app.state.tasks.create(pid, title="Existing", actor="director", timezone="America/Los_Angeles", operation_id="op-existing-target")
    if undo:
        saved = app.state.tasks.update(pid, saved["task"]["task_id"], changes={"state": "completed"}, expected_version=1,
            actor="director", timezone="America/Los_Angeles", operation_id="op-existing-complete")
        name, text = "crm_undo_change", "Undo task Missing completion"
        args = {"change_id": saved["change"]["change_id"], "expected_version": 2}
    else:
        name, text = "crm_update_task", "Mark task Missing done"
        args = {"task_id": saved["task"]["task_id"], "expected_version": 1, "changes": {"state": "completed"}}
    results = scripted_chat(app, pid, text, [ToolCall(id="missing-target", name=name,
        arguments={**args, "reason": "Requested", "operation_id": "op-missing-target"})])
    assert not results[0]["ok"]
    assert app.state.tasks.get(pid, saved["task"]["task_id"])["version"] == saved["task"]["version"]


def test_a_private_eligible_task_prevents_guessing_the_only_visible_target(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    visible = app.state.tasks.create(pid, title="Visible", actor="director", timezone="America/Los_Angeles", operation_id="op-visible-target")["task"]
    ref = app.state.people.upsert_attachment(pid, record_type="source_ref", fields={"id": "gmail:private", "sensitivity": "restricted"},
        idempotency_key="private-target", actor="director", rationale_summary="Attach")
    app.state.tasks.create(pid, title="PRIVATE TASK", actor="assistant", timezone="America/Los_Angeles", operation_id="op-hidden-target",
        source_refs=[ref["id"]], allowed_source_ids={ref["id"]})
    results = scripted_chat(app, pid, "Mark that task done", [ToolCall(id="private-target", name="crm_update_task", arguments={
        "task_id": visible["task_id"], "expected_version": 1, "changes": {"state": "completed"},
        "operation_id": "op-guess-visible", "reason": "Requested"})])
    assert not results[0]["ok"] and results[0]["result"]["code"] == "ambiguous_task"
    assert "PRIVATE TASK" not in json.dumps(results)


@pytest.mark.parametrize("undo", [False, True])
def test_an_explicit_task_or_change_id_selects_the_director_named_record(tmp_path, undo):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    for index in range(2):
        saved = app.state.tasks.create(pid, title=f"Task {index}", actor="director", timezone="America/Los_Angeles", operation_id=f"op-id-target-{index}")
        if undo:
            saved = app.state.tasks.update(pid, saved["task"]["task_id"], changes={"state": "completed"}, expected_version=1,
                actor="director", timezone="America/Los_Angeles", operation_id=f"op-id-complete-{index}")
    if undo:
        name, text = "crm_undo_change", f"Undo {saved['change']['change_id']}"
        args = {"change_id": saved["change"]["change_id"], "expected_version": 2}
    else:
        name, text = "crm_update_task", f"Mark task {saved['task']['task_id']} done"
        args = {"task_id": saved["task"]["task_id"], "expected_version": 1, "changes": {"state": "completed"}}
    results = scripted_chat(app, pid, text, [ToolCall(id="explicit-target", name=name,
        arguments={**args, "operation_id": "op-explicit-target", "reason": "Requested"})])
    assert results[0]["ok"], results
    assert results[0]["result"]["task"]["task_id"] == saved["task"]["task_id"]


@pytest.mark.parametrize("undo", [False, True])
def test_a_shared_title_cannot_select_the_public_copy_of_private_work(tmp_path, undo):
    app = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())
    pid = keep(app.state.people)["person_id"]
    public = app.state.tasks.create(pid, title="Send proposal", actor="director", timezone="America/Los_Angeles", operation_id="op-title-public")
    ref = app.state.people.upsert_attachment(pid, record_type="source_ref", fields={"id": "gmail:private", "sensitivity": "restricted"},
        idempotency_key="private-title", actor="director", rationale_summary="Attach")
    private = app.state.tasks.create(pid, title="Send proposal", actor="assistant", timezone="America/Los_Angeles", operation_id="op-title-private",
        source_refs=[ref["id"]], allowed_source_ids={ref["id"]})
    if undo:
        for index, saved in enumerate((public, private)):
            completed = app.state.tasks.update(pid, saved["task"]["task_id"], changes={"state": "completed"}, expected_version=1,
                actor="director", timezone="America/Los_Angeles", operation_id=f"op-title-complete-{index}", allowed_source_ids={ref["id"]})
            if index == 0:
                public = completed
        name, text = "crm_undo_change", "Undo task Send proposal completion"
        args = {"change_id": public["change"]["change_id"], "expected_version": 2}
        explicit_text = f"Undo {public['change']['change_id']}"
    else:
        name, text = "crm_update_task", "Mark task Send proposal done"
        args = {"task_id": public["task"]["task_id"], "expected_version": 1, "changes": {"state": "completed"}}
        explicit_text = f"Mark task {public['task']['task_id']} done"
    args.update(operation_id="op-title-target", reason="Requested")
    results = scripted_chat(app, pid, text, [ToolCall(id="shared-title", name=name, arguments=args)])
    assert not results[0]["ok"] and results[0]["result"]["code"] == ("ambiguous_crm_change" if undo else "ambiguous_task")
    assert "Send proposal" not in json.dumps(results[0]["result"])
    assert "candidates" not in results[0]["result"]
    # An explicit permitted id establishes a target without exposing its twin.
    direct_args = {**args, "operation_id": "op-title-explicit"}
    ok, explicit = execute(name, direct_args, people=app.state.people,
        session_id=app.state.people.session_for_person(pid),
        crm_directive=replace(director_directive(explicit_text), at=DIRECTIVE_AT))
    assert ok and explicit["task"]["task_id"] == public["task"]["task_id"]
