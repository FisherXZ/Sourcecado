"""Person tasks: one saved record behind the global Tasks view and each person file."""

import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from coworker.crm_tasks import TaskConflict, TaskError, TaskNotFound, TaskService
from coworker.people import PersonStore
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-tasks"
HEADERS = {TOKEN_HEADER: TOKEN}


def _keep(people: PersonStore, apollo_id: str = "maya", **extra) -> dict:
    return people.keep_from_apollo(
        apollo_id=apollo_id,
        first_name=extra.get("first_name", "Maya"),
        last_name_obfuscated=extra.get("last_name", "Patel"),
        title="Head of Talent",
        company="Lumen",
    )


def _app(tmp_path):
    return create_app(token=TOKEN, state=tmp_path, provider=FakeProvider())


def _create(client, person_id, operation_id, **fields):
    return client.post(
        f"/v1/people/{person_id}/tasks",
        headers=HEADERS,
        json={"operation_id": operation_id, **fields},
    )


# --- the shared record ---------------------------------------------------


def test_both_lists_show_the_same_saved_tasks_and_edits(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    person = _keep(app.state.people)
    pid = person["person_id"]
    app.state.people.set_sequence(pid, "open", actor="director")

    dated = _create(
        client, pid, "op-create-dated", title="Send project examples", due_date="2026-10-16"
    )
    undated = _create(client, pid, "op-create-undated", title="Ask about spring cohort")
    assert dated.status_code == 201
    assert undated.status_code == 201
    dated_task = dated.json()["task"]
    assert dated_task["due_date"] == "2026-10-16"
    assert dated_task["due_timezone"] == "America/Los_Angeles"
    assert dated_task["origin"] == "human"
    assert dated_task["state"] == "open"
    assert undated.json()["task"]["due_date"] is None
    assert undated.json()["task"]["due_timezone"] is None

    global_list = client.get("/v1/crm/tasks", headers=HEADERS).json()
    person_list = client.get(
        "/v1/crm/tasks", params={"person_id": pid}, headers=HEADERS
    ).json()
    ids = [task["task_id"] for task in global_list["tasks"]]
    assert ids == [task["task_id"] for task in person_list["tasks"]]
    assert ids == [dated_task["task_id"], undated.json()["task"]["task_id"]]

    # Edit from the person file; the global list sees it.
    edited = client.patch(
        f"/v1/people/{pid}/tasks/{dated_task['task_id']}",
        headers=HEADERS,
        json={
            "operation_id": "op-edit-1",
            "expected_version": 1,
            "changes": {
                "title": "Send three project examples",
                "details": "Use the fintech ones.",
                "due_date": None,
            },
        },
    )
    assert edited.status_code == 200
    task = edited.json()["task"]
    assert task["version"] == 2
    assert task["due_date"] is None and task["due_timezone"] is None
    assert task["protected_fields"] == ["details", "due_date", "title"]

    refreshed = client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"]
    by_id = {item["task_id"]: item for item in refreshed}
    assert by_id[task["task_id"]]["title"] == "Send three project examples"
    assert by_id[task["task_id"]]["details"] == "Use the fintech ones."
    assert by_id[task["task_id"]]["person"]["first_name"] == "Maya"

    history = app.state.tasks.changes(task["task_id"])
    assert [row["action"] for row in history] == ["create", "update"]
    assert {row["actor"] for row in history} == {"director"}


def test_a_person_can_hold_several_tasks_ordered_by_due_group(tmp_path):
    people = PersonStore(tmp_path)
    service = TaskService(people)
    pid = _keep(people)["person_id"]
    now = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # 11:00 in Los Angeles
    for op, title, due in (
        ("op-a-undated", "Undated", None),
        ("op-b-upcoming", "Upcoming", "2026-10-20"),
        ("op-c-today", "Today", "2026-10-12"),
        ("op-d-overdue", "Overdue", "2026-10-01"),
    ):
        service.create(
            pid,
            title=title,
            due_date=due,
            timezone="America/Los_Angeles",
            operation_id=op,
            actor="director",
        )
    listing = service.list(timezone="America/Los_Angeles", person_id=pid, now=now)
    assert [task["title"] for task in listing["tasks"]] == [
        "Overdue",
        "Today",
        "Upcoming",
        "Undated",
    ]
    assert [task["due_status"] for task in listing["tasks"]] == [
        "overdue",
        "today",
        "upcoming",
        "none",
    ]
    assert listing["today"] == "2026-10-12"


def test_today_follows_the_directors_saved_timezone(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    service: TaskService = app.state.tasks
    # 03:00 UTC on the 13th is still the 12th in Los Angeles, but the 13th in Tokyo.
    late = datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
    service.create(
        pid,
        title="Call",
        due_date="2026-10-12",
        timezone="America/Los_Angeles",
        operation_id="op-call-today",
        actor="director",
    )
    assert service.list(timezone="America/Los_Angeles", person_id=pid, now=late)[
        "tasks"
    ][0]["due_status"] == "today"
    assert service.list(timezone="Asia/Tokyo", person_id=pid, now=late)["tasks"][0][
        "due_status"
    ] == "today"  # Existing tasks retain their saved timezone after a settings change.

    assert client.post(
        "/v1/settings/timezone", headers=HEADERS, json={"timezone": "Mars/Olympus"}
    ).status_code == 400
    saved = client.post(
        "/v1/settings/timezone", headers=HEADERS, json={"timezone": "Asia/Tokyo"}
    )
    assert saved.status_code == 200
    assert client.get("/v1/settings", headers=HEADERS).json()["timezone"] == "Asia/Tokyo"
    created = _create(client, pid, "op-tokyo-task", title="Tokyo", due_date="2026-10-20")
    assert created.json()["task"]["due_timezone"] == "Asia/Tokyo"
    assert client.get("/v1/crm/tasks", headers=HEADERS, params={"person_id": pid}).json()[
        "timezone"
    ] == "Asia/Tokyo"


# --- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "Ok", "details": "x" * 4001},
        {"title": "Ok", "due_date": "2026-02-30"},
        {"title": "Ok", "due_date": "next Friday"},
        {"title": "Ok", "due_date": "2026-10-12T09:00:00Z"},
        {"title": "Ok", "due_date": 20261012},
    ],
)
def test_invalid_task_input_is_rejected_without_writing(tmp_path, fields):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    response = _create(client, pid, "op-invalid-input", **fields)
    assert response.status_code == 400
    assert app.state.tasks.list(timezone="UTC", person_id=pid)["tasks"] == []


def test_missing_or_deleted_people_cannot_hold_tasks(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    people = app.state.people
    person = _keep(people)
    missing = f"per_{'0' * 32}"
    assert _create(client, missing, "op-missing-person", title="Nope").status_code == 404

    task = _create(client, person["person_id"], "op-before-delete", title="Before").json()["task"]
    people.delete(
        person["person_id"],
        expected_version=person["version"],
        actor="director",
        rationale_summary="duplicate",
    )
    assert (
        _create(client, person["person_id"], "op-after-delete", title="After").status_code
        == 404
    )
    assert (
        client.patch(
            f"/v1/people/{person['person_id']}/tasks/{task['task_id']}",
            headers=HEADERS,
            json={"operation_id": "op-edit-deleted", "expected_version": 1, "changes": {"title": "x"}},
        ).status_code
        == 404
    )
    assert client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"] == []
    assert (
        client.get(
            "/v1/crm/tasks", headers=HEADERS, params={"person_id": person["person_id"]}
        ).status_code
        == 404
    )
    # The historical task is preserved, not erased with the person.
    row = people._conn.execute(
        "SELECT title FROM person_tasks WHERE task_id = ?", (task["task_id"],)
    ).fetchone()
    assert row["title"] == "Before"


def test_the_database_itself_refuses_a_task_for_a_missing_person(tmp_path):
    people = PersonStore(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        people._conn.execute(
            """
            INSERT INTO person_tasks (
                task_id, person_id, title, origin, created_at, updated_at
            ) VALUES ('task_x', 'per_missing', 'Orphan', 'human', 'now', 'now')
            """
        )
    people._conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        people._conn.execute(
            """
            INSERT INTO crm_changes (
                change_id, person_id, entity_type, entity_id, entity_version,
                action, after_json, actor, reason, operation_id, created_at
            ) VALUES ('chg_x', 'per_missing', 'task', 'task_x', 1, 'create',
                      '{}', 'director', 'r', 'op', 'now')
            """
        )
    people._conn.rollback()


def test_a_request_cannot_choose_its_author(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    created = _create(
        client, pid, "op-impersonate", title="Mine", actor="assistant", origin="assistant"
    ).json()
    assert created["task"]["origin"] == "human"
    assert created["change"]["actor"] == "director"
    undone = client.post(
        f"/v1/people/{pid}/crm-changes/{created['change']['change_id']}/undo",
        headers=HEADERS,
        json={"operation_id": "op-impersonate-undo", "actor": "assistant"},
    )
    # Creation has nothing to restore; the request's actor field is ignored.
    assert undone.status_code == 400


# --- retries and conflicts ----------------------------------------------


def test_a_repeated_save_returns_the_original_task_instead_of_a_duplicate(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    first = _create(client, pid, "op-retry-create", title="Send deck", due_date="2026-10-20")
    again = _create(client, pid, "op-retry-create", title="Send deck", due_date="2026-10-20")
    assert first.status_code == 201
    assert again.status_code == 200
    assert again.json()["replayed"] is True
    assert again.json()["task"]["task_id"] == first.json()["task"]["task_id"]
    assert len(app.state.tasks.list(timezone="UTC", person_id=pid)["tasks"]) == 1

    different = _create(client, pid, "op-retry-create", title="Send something else")
    assert different.status_code == 409
    assert different.json()["code"] == "operation_conflict"
    assert len(app.state.tasks.list(timezone="UTC", person_id=pid)["tasks"]) == 1

    task_id = first.json()["task"]["task_id"]
    edit = {
        "operation_id": "op-retry-edit",
        "expected_version": 1,
        "changes": {"title": "Send the deck"},
    }
    url = f"/v1/people/{pid}/tasks/{task_id}"
    assert client.patch(url, headers=HEADERS, json=edit).json()["task"]["version"] == 2
    replay = client.patch(url, headers=HEADERS, json=edit)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["task"]["version"] == 2
    assert [row["action"] for row in app.state.tasks.changes(task_id)] == [
        "create",
        "update",
    ]


def test_a_stale_edit_returns_the_newer_task_and_writes_nothing(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    task = _create(client, pid, "op-stale-create", title="Draft proposal").json()["task"]
    url = f"/v1/people/{pid}/tasks/{task['task_id']}"
    newer = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "op-other-tab", "expected_version": 1, "changes": {"due_date": "2026-10-16"}},
    ).json()["task"]

    stale = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "op-stale-tab", "expected_version": 1, "changes": {"title": "Mine"}},
    )
    assert stale.status_code == 409
    body = stale.json()
    assert body["code"] == "task_conflict"
    assert body["task"] == newer
    assert app.state.tasks.get(pid, task["task_id"])["title"] == "Draft proposal"

    # The director chooses to keep their text over the newer version.
    resolved = client.patch(
        url,
        headers=HEADERS,
        json={"operation_id": "op-stale-tab-2", "expected_version": 2, "changes": {"title": "Mine"}},
    )
    assert resolved.status_code == 200
    assert resolved.json()["task"]["title"] == "Mine"
    assert resolved.json()["task"]["due_date"] == "2026-10-16"


def test_an_unchanged_edit_creates_no_version_or_history(tmp_path):
    people = PersonStore(tmp_path)
    service = TaskService(people)
    pid = _keep(people)["person_id"]
    task = service.create(
        pid, title="Same", timezone="UTC", operation_id="op-same-create", actor="director"
    )["task"]
    result = service.update(
        pid,
        task["task_id"],
        changes={"title": "  Same "},
        expected_version=1,
        timezone="UTC",
        operation_id="op-same-edit",
        actor="director",
    )
    assert result["change"] is None
    assert result["task"]["version"] == 1
    assert len(service.changes(task["task_id"])) == 1


def test_task_errors_are_typed_for_the_http_layer(tmp_path):
    people = PersonStore(tmp_path)
    service = TaskService(people)
    pid = _keep(people)["person_id"]
    with pytest.raises(TaskNotFound):
        service.update(
            pid,
            "task_" + "f" * 32,
            changes={"title": "x"},
            expected_version=1,
            timezone="UTC",
            operation_id="op-unknown-task",
            actor="director",
        )
    with pytest.raises(TaskError):
        service.create(pid, title="x", timezone="UTC", operation_id="short", actor="director")
    task = service.create(
        pid, title="x", timezone="UTC", operation_id="op-typed-create", actor="director"
    )["task"]
    with pytest.raises(TaskConflict):
        service.update(
            pid,
            task["task_id"],
            changes={"title": "y"},
            expected_version=5,
            timezone="UTC",
            operation_id="op-typed-stale",
            actor="director",
        )


# --- durability and scope ------------------------------------------------


def test_tasks_survive_a_restart_with_the_same_identity(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    created = _create(
        client, pid, "op-restart-create", title="Send project examples", details="Fintech", due_date="2026-10-16"
    ).json()["task"]
    app.state.people._conn.close()

    # A fresh process: no Gmail, no model provider.
    reopened = create_app(token=TOKEN, state=tmp_path, provider=None)
    after = TestClient(reopened).get(
        "/v1/crm/tasks", headers=HEADERS, params={"person_id": pid}
    )
    assert after.status_code == 200
    (task,) = after.json()["tasks"]
    for field in ("task_id", "title", "details", "due_date", "due_timezone", "version"):
        assert task[field] == created[field]
    # The retry receipt survives too: replaying after restart is still no duplicate.
    replay = _create(
        TestClient(reopened),
        pid,
        "op-restart-create",
        title="Send project examples",
        details="Fintech",
        due_date="2026-10-16",
    )
    assert replay.status_code == 200
    assert replay.json()["task"]["task_id"] == created["task_id"]


def test_a_task_does_not_start_outreach_or_add_a_kept_person_to_contacts(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    kept = _keep(app.state.people)
    pid = kept["person_id"]
    timeline_before = app.state.people.timeline(pid)
    _create(client, pid, "op-kept-task", title="Research before outreach")

    person = app.state.people.get(pid)
    assert person["sequence_state"] is None
    assert person["version"] == kept["version"]
    board = client.get("/v1/board", headers=HEADERS).json()
    assert [item["person_id"] for item in board["backlog"]] == [pid]
    assert board["open"] == board["in_conversation"] == board["done"] == []
    global_tasks = client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"]
    assert [task["person_id"] for task in global_tasks] == [pid]
    assert (
        len(
            client.get("/v1/crm/tasks", headers=HEADERS, params={"person_id": pid}).json()[
                "tasks"
            ]
        )
        == 1
    )
    assert app.state.people.timeline(pid) == timeline_before


# --- complete, reopen, cancel and undo -----------------------------------


def test_receipts_created_before_attention_keep_their_retry_identity(tmp_path):
    from coworker.crm_tasks import _fingerprint
    people = PersonStore(tmp_path)
    pid = _keep(people)["person_id"]
    service = TaskService(people)
    args = dict(title="Legacy promise", timezone="UTC", actor="director", operation_id="legacy-operation-retry")
    created = service.create(pid, **args)
    legacy = _fingerprint({"op": "task.create", "person_id": pid, "actor": "director", "title": "Legacy promise", "details": "", "due_date": None})
    people._conn.execute("UPDATE crm_operations SET fingerprint=? WHERE operation_id=?", (legacy, args["operation_id"]))
    people._conn.commit()
    replay = service.create(pid, **args)
    assert replay["replayed"]
    assert replay["task"]["task_id"] == created["task"]["task_id"]


def _set_state(client, pid, task, state, operation_id, **extra):
    return client.patch(
        f"/v1/people/{pid}/tasks/{task['task_id']}",
        headers=HEADERS,
        json={
            "operation_id": operation_id,
            "expected_version": task["version"],
            "changes": {"state": state},
            **extra,
        },
    )


def _undo(client, pid, change_id, operation_id):
    return client.post(
        f"/v1/people/{pid}/crm-changes/{change_id}/undo",
        headers=HEADERS,
        json={"operation_id": operation_id},
    )


def test_complete_cancel_and_reopen_show_in_both_lists_and_keep_history(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    app.state.people.set_sequence(pid, "open", actor="director")
    first = _create(client, pid, "op-lc-first", title="Send examples", due_date="2026-10-16").json()["task"]
    second = _create(client, pid, "op-lc-second", title="Ask about cohort").json()["task"]

    done = _set_state(client, pid, first, "completed", "op-lc-complete")
    assert done.status_code == 200
    task = done.json()["task"]
    assert task["state"] == "completed"
    assert task["version"] == 2
    assert task["completed_at"] is not None
    assert "state" in task["protected_fields"]
    assert done.json()["change"]["action"] == "complete"

    cancelled = _set_state(client, pid, second, "cancelled", "op-lc-cancel").json()["task"]
    assert cancelled["state"] == "cancelled"
    reopened = _set_state(client, pid, cancelled, "open", "op-lc-reopen").json()
    assert reopened["task"]["state"] == "open"
    assert reopened["task"]["completed_at"] is None
    assert reopened["change"]["action"] == "reopen"

    # The global and the person list agree, open and all.
    for params in ({}, {"person_id": pid}):
        open_ids = [
            t["task_id"]
            for t in client.get("/v1/crm/tasks", headers=HEADERS, params=params).json()["tasks"]
        ]
        assert open_ids == [second["task_id"]]
        everything = client.get(
            "/v1/crm/tasks", headers=HEADERS, params={**params, "state": "all"}
        ).json()["tasks"]
        assert {t["task_id"]: t["state"] for t in everything} == {
            first["task_id"]: "completed",
            second["task_id"]: "open",
        }

    history = client.get(
        f"/v1/people/{pid}/tasks/{second['task_id']}/changes", headers=HEADERS
    ).json()["changes"]
    assert [row["action"] for row in history] == ["create", "cancel", "reopen"]
    assert all(row["actor"] == "director" for row in history)
    assert client.get(
        f"/v1/people/{pid}/tasks/task_{'0' * 32}/changes", headers=HEADERS
    ).status_code == 404

    # A finished task reopens first; it cannot jump between finished states.
    assert _set_state(client, pid, task, "cancelled", "op-lc-bad-1").status_code == 400
    assert _set_state(client, pid, task, "paused", "op-lc-bad-2").status_code == 400


def test_completing_a_task_leaves_other_tasks_and_the_person_alone(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    people = app.state.people
    pid = _keep(people)["person_id"]
    people.set_sequence(pid, "in_conversation", actor="director")
    person_before = people.get(pid)
    timeline_before = people.timeline(pid)
    a = _create(client, pid, "op-alone-task-a", title="A").json()["task"]
    b = _create(client, pid, "op-alone-task-b", title="B").json()["task"]

    assert _set_state(client, pid, a, "completed", "op-alone-complete").status_code == 200
    assert app.state.tasks.get(pid, b["task_id"]) == b
    person_after = people.get(pid)
    assert person_after["sequence_state"] == "in_conversation"
    assert person_after["version"] == person_before["version"]
    assert people.timeline(pid) == timeline_before

    # Marking the person Done keeps the unfinished task visible.
    people.set_sequence(pid, "done", actor="director")
    listed = client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"]
    assert [t["task_id"] for t in listed] == [b["task_id"]]
    assert listed[0]["person"]["sequence_state"] == "done"


def test_a_repeated_state_change_replays_the_receipt(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    task = _create(client, pid, "op-retry-state-create", title="Call").json()["task"]
    first = _set_state(client, pid, task, "completed", "op-retry-complete")
    again = _set_state(client, pid, task, "completed", "op-retry-complete")
    assert first.status_code == again.status_code == 200
    assert again.json()["replayed"] is True
    assert again.json()["task"]["version"] == 2
    assert [row["action"] for row in app.state.tasks.changes(task["task_id"])] == [
        "create",
        "complete",
    ]
    # Same id, different body.
    other = _set_state(client, pid, task, "cancelled", "op-retry-complete")
    assert other.status_code == 409
    assert other.json()["code"] == "operation_conflict"
    # A stale state change returns the current task and writes nothing.
    stale = _set_state(client, pid, task, "cancelled", "op-retry-stale")
    assert stale.status_code == 409
    assert stale.json()["task"]["state"] == "completed"
    assert app.state.tasks.get(pid, task["task_id"])["version"] == 2


def test_undo_writes_a_compensating_change_and_keeps_history(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    task = _create(client, pid, "op-undo-create", title="Draft", due_date="2026-10-16").json()["task"]
    completed = _set_state(client, pid, task, "completed", "op-undo-complete").json()
    change_id = completed["change"]["change_id"]

    undone = _undo(client, pid, change_id, "op-undo-first")
    assert undone.status_code == 200
    body = undone.json()
    assert body["task"]["state"] == "open"
    assert body["task"]["completed_at"] is None
    assert body["task"]["due_date"] == "2026-10-16"
    assert body["task"]["version"] == 3
    assert body["change"]["action"] == "undo"
    assert body["change"]["compensates_change_id"] == change_id

    history = app.state.tasks.changes(task["task_id"])
    assert [row["action"] for row in history] == ["create", "complete", "undo"]
    assert history[-1]["compensates_change_id"] == change_id

    # Replaying the undo returns the same receipt; a second undo of the same
    # change is a conflict because the task has moved on.
    replay = _undo(client, pid, change_id, "op-undo-first")
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    again = _undo(client, pid, change_id, "op-undo-second")
    assert again.status_code == 409
    assert again.json()["task"]["version"] == 3
    assert len(app.state.tasks.changes(task["task_id"])) == 3

    # Undoing the undo re-completes the task.
    redo = _undo(client, pid, body["change"]["change_id"], "op-undo-third").json()
    assert redo["task"]["state"] == "completed" and redo["task"]["version"] == 4

    assert _undo(client, pid, "chg_" + "f" * 32, "op-undo-missing").status_code == 404
    other = _keep(app.state.people, apollo_id="leo", first_name="Leo")["person_id"]
    assert _undo(client, other, change_id, "op-undo-wrong-person").status_code == 404


def test_undo_refuses_to_erase_a_later_edit(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    task = _create(client, pid, "op-later-create", title="Draft").json()["task"]
    completed = _set_state(client, pid, task, "completed", "op-later-complete").json()
    later = client.patch(
        f"/v1/people/{pid}/tasks/{task['task_id']}",
        headers=HEADERS,
        json={
            "operation_id": "op-later-edit",
            "expected_version": 2,
            "changes": {"title": "Draft the proposal"},
        },
    ).json()["task"]

    conflict = _undo(client, pid, completed["change"]["change_id"], "op-later-undo")
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "task_conflict"
    assert conflict.json()["task"] == later
    current = app.state.tasks.get(pid, task["task_id"])
    assert current["title"] == "Draft the proposal"
    assert current["state"] == "completed"
    assert current["version"] == 3
    assert [row["action"] for row in app.state.tasks.changes(task["task_id"])] == [
        "create",
        "complete",
        "update",
    ]


def test_a_committed_state_change_is_found_by_operation_id_after_restart(tmp_path):
    app = _app(tmp_path)
    client = TestClient(app)
    pid = _keep(app.state.people)["person_id"]
    task = _create(client, pid, "op-crash-create", title="Call").json()["task"]
    # The server commits, then the process stops before the client sees it.
    _set_state(client, pid, task, "completed", "op-crash-complete")
    app.state.people._conn.close()

    reopened = TestClient(create_app(token=TOKEN, state=tmp_path, provider=None))
    retry = _set_state(reopened, pid, task, "completed", "op-crash-complete")
    assert retry.status_code == 200
    assert retry.json()["replayed"] is True
    assert retry.json()["task"]["state"] == "completed"
    history = reopened.get(
        f"/v1/people/{pid}/tasks/{task['task_id']}/changes", headers=HEADERS
    ).json()["changes"]
    assert [row["action"] for row in history] == ["create", "complete"]
    assert len(
        reopened.get(
            "/v1/crm/tasks", headers=HEADERS, params={"person_id": pid, "state": "all"}
        ).json()["tasks"]
    ) == 1
