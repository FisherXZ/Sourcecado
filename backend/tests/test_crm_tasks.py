"""Manual CRM tasks exercise the real HTTP app and durable person database."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from fastapi.testclient import TestClient

from coworker.server import TOKEN_HEADER, create_app

TOKEN = "crm-task-test"
HEADERS = {TOKEN_HEADER: TOKEN}


def app_at(path):
    return create_app(token=TOKEN, state=path, provider=None)


def keep(app, name="Maya"):
    return app.state.people.keep_from_apollo(
        apollo_id=name, first_name=name, last_name_obfuscated=None,
        title="Founder", company="Example"
    )


def create(client, person_id, operation_id="create-one", **fields):
    return client.post(
        f"/v1/people/{person_id}/tasks",
        headers=HEADERS,
        json={"operation_id": operation_id, "title": "Send examples", **fields},
    )


def test_two_tasks_share_identity_and_edits_survive_restart_without_ai(tmp_path):
    app = app_at(tmp_path)
    person = keep(app)
    pid = person["person_id"]
    client = TestClient(app)
    dated = create(client, pid, due_date="2026-10-06", details="Include the demo")
    assert dated.status_code == 201
    undated = create(client, pid, "create-two", title="Research company")
    assert undated.status_code == 201
    first = dated.json()["task"]
    assert first["due_timezone"] == "America/Los_Angeles"
    assert undated.json()["task"]["due_date"] is None
    edited = client.patch(
        f"/v1/people/{pid}/tasks/{first['task_id']}",
        headers=HEADERS,
        json={"operation_id": "edit-one", "expected_version": 1,
              "title": "Send project examples", "details": "Two examples", "due_date": None},
    )
    assert edited.status_code == 200
    assert edited.json()["task"]["version"] == 2
    assert client.get("/v1/board", headers=HEADERS).json()["open"] == []
    assert app.state.people.get(pid)["version"] == person["version"]

    app.state.people._conn.close()
    app.state.store._conn.close()
    restarted = TestClient(app_at(tmp_path))
    global_tasks = restarted.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"]
    person_tasks = restarted.get(f"/v1/crm/tasks?person_id={pid}", headers=HEADERS).json()["tasks"]
    assert global_tasks == person_tasks
    assert len(global_tasks) == 2
    saved = next(t for t in global_tasks if t["task_id"] == first["task_id"])
    assert saved["title"] == "Send project examples"
    assert saved["details"] == "Two examples"
    assert saved["due_date"] is None
    assert saved["version"] == 2


def test_retry_after_restart_replays_original_receipt_and_changed_intent_conflicts(tmp_path):
    app = app_at(tmp_path)
    pid = keep(app)["person_id"]
    first = create(TestClient(app), pid).json()
    restarted = TestClient(app_at(tmp_path))
    retry = create(restarted, pid)
    assert retry.status_code == 201
    assert retry.json() == first
    different = create(restarted, pid, title="Something different")
    assert different.status_code == 409
    with sqlite3.connect(tmp_path / "people.db") as conn:
        for table in ("person_tasks", "crm_changes", "crm_operations"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1


def test_stale_task_edit_returns_current_version_without_overwriting(tmp_path):
    app = app_at(tmp_path)
    pid = keep(app)["person_id"]
    client = TestClient(app)
    task = create(client, pid).json()["task"]
    url = f"/v1/people/{pid}/tasks/{task['task_id']}"
    saved = client.patch(url, headers=HEADERS, json={
        "operation_id": "edit-a", "expected_version": 1, "title": "Newer title",
    })
    assert saved.status_code == 200
    stale = client.patch(url, headers=HEADERS, json={
        "operation_id": "edit-b", "expected_version": 1, "title": "My unsaved title",
    })
    assert stale.status_code == 409
    assert stale.json()["current"]["title"] == "Newer title"
    assert stale.json()["current"]["version"] == 2
    # Person changes don't make an independently versioned task stale.
    app.state.people.set_sequence(pid, "open", actor="director")
    assert client.patch(url, headers=HEADERS, json={
        "operation_id": "edit-c", "expected_version": 2, "details": "Still editable",
    }).status_code == 200


@pytest.mark.parametrize("fields", [
    {"title": " "}, {"title": "x" * 201}, {"title": 123},
    {"details": "x" * 8001}, {"details": None},
    {"due_date": "2026-02-30"}, {"due_date": "2026-13-01"},
    {"due_date": "2026-10-06T00:00:00Z"}, {"due_date": "tomorrow"},
    {"actor": "assistant"}, {"origin": "assistant"}, {"state": "completed"},
    {"source_refs": ["private-source"]}, {"operation_id": []},
])
def test_invalid_or_impersonated_writes_are_rejected(tmp_path, fields):
    app = app_at(tmp_path)
    pid = keep(app)["person_id"]
    assert create(TestClient(app), pid, **fields).status_code == 422
    assert app.state.crm.repository.list_tasks()["tasks"] == []
    assert app.state.people._conn.execute("SELECT count(*) FROM crm_changes").fetchone()[0] == 0


def test_missing_deleted_and_cross_person_tasks_are_out_of_scope(tmp_path):
    from coworker.crm_repository import CrmNotFound

    app = app_at(tmp_path)
    client = TestClient(app)
    pid = keep(app)["person_id"]
    other = keep(app, "Nora")["person_id"]
    task = create(client, pid).json()["task"]
    assert create(client, "missing").status_code == 404
    assert client.patch(f"/v1/people/{other}/tasks/{task['task_id']}", headers=HEADERS,
        json={"operation_id": "wrong-person", "expected_version": 1, "title": "Other"}).status_code == 404
    app.state.people.delete(pid, expected_version=1, actor="director", rationale_summary="Remove person")
    assert create(client, pid, "deleted-person").status_code == 404
    assert client.patch(f"/v1/people/{pid}/tasks/{task['task_id']}", headers=HEADERS,
        json={"operation_id": "deleted-edit", "expected_version": 1, "title": "Other"}).status_code == 404
    assert client.get(f"/v1/crm/tasks?person_id={pid}", headers=HEADERS).status_code == 404
    assert client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"] == []
    for parent in ("missing", pid):
        with pytest.raises(CrmNotFound):
            app.state.crm.save_task(parent, {"operation_id": "direct", "title": "Direct"})
        with pytest.raises(CrmNotFound):
            app.state.crm.repository.save(person_id=parent, task_id=None, operation_id="repo",
                fingerprint="hash", changes={"title": "Direct"}, expected_version=None, timezone="UTC")
        with pytest.raises(sqlite3.IntegrityError), app.state.people.crm_transaction() as conn:
            conn.execute("""INSERT INTO person_tasks(task_id, person_id, title, due_timezone, origin,
                version, created_at, updated_at) VALUES ('invalid', ?, 'Direct', 'UTC', 'human', 1, 'now', 'now')""", (parent,))
    assert app.state.people._conn.execute("SELECT count(*) FROM person_tasks").fetchone()[0] == 1
    assert app.state.people._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert app.state.people._conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("failing_table", ["crm_changes", "crm_operations"])
def test_failed_write_rolls_back_task_history_timeline_and_receipt(tmp_path, failing_table):
    app = app_at(tmp_path)
    pid = keep(app)["person_id"]
    conn = app.state.people._conn
    before_events = len(app.state.people.timeline(pid))
    conn.execute(f"CREATE TRIGGER fail_write BEFORE INSERT ON {failing_table} BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    conn.commit()
    failed = create(TestClient(app, raise_server_exceptions=False), pid)
    assert failed.status_code == 500
    for table in ("person_tasks", "crm_changes", "crm_operations"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert len(app.state.people.timeline(pid)) == before_events
    conn.execute("DROP TRIGGER fail_write")
    conn.commit()
    assert create(TestClient(app), pid).status_code == 201


def test_simultaneous_retries_from_two_connections_create_one_task(tmp_path):
    app = app_at(tmp_path)
    pid = keep(app)["person_id"]
    second = app_at(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda a: create(TestClient(a), pid), (app, second)))
    assert [r.status_code for r in responses] == [201, 201]
    assert responses[0].json() == responses[1].json()
    assert len(app.state.crm.repository.list_tasks()["tasks"]) == 1


def test_saved_timezone_and_calendar_dates_round_trip_across_dst(tmp_path):
    app = app_at(tmp_path)
    client = TestClient(app)
    pid = keep(app)["person_id"]
    assert client.put("/v1/crm/preferences", headers=HEADERS, json={"timezone": "Pacific/Auckland"}).status_code == 200
    for index, due in enumerate(("2026-03-08", "2026-11-01", "2024-02-29", "2026-01-01")):
        task = create(client, pid, f"date-{index}", due_date=due).json()["task"]
        assert task["due_date"] == due
        assert task["due_timezone"] == "Pacific/Auckland"
    assert client.put("/v1/crm/preferences", headers=HEADERS, json={"timezone": "Bad/Zone"}).status_code == 422
    restarted = TestClient(app_at(tmp_path))
    assert restarted.get("/v1/crm/preferences", headers=HEADERS).json()["timezone"] == "Pacific/Auckland"
    restarted.put("/v1/crm/preferences", headers=HEADERS, json={"timezone": "UTC"})
    # Replays keep the originally committed timezone even after settings change.
    assert create(restarted, pid, "date-0", due_date="2026-03-08").json()["task"]["due_timezone"] == "Pacific/Auckland"


def test_global_task_list_and_person_picker_are_bounded_and_include_kept_people(tmp_path):
    app = app_at(tmp_path)
    client = TestClient(app)
    person = keep(app)
    pid = person["person_id"]
    for index in range(3):
        assert create(client, pid, f"page-{index}").status_code == 201
    page = client.get("/v1/crm/tasks?limit=2", headers=HEADERS).json()
    assert len(page["tasks"]) == 2
    assert page["next_offset"] == 2
    last = client.get("/v1/crm/tasks?limit=2&offset=2", headers=HEADERS).json()
    assert len(last["tasks"]) == 1
    assert last["next_offset"] is None
    assert client.get("/v1/crm/tasks?limit=101", headers=HEADERS).status_code == 422
    picks = client.get("/v1/crm/people?query=Maya", headers=HEADERS).json()["people"]
    assert picks[0]["person_id"] == pid
    assert set(picks[0]) == {"person_id", "first_name", "last_name", "company"}
    assert client.get("/v1/crm/tasks").status_code == 401
