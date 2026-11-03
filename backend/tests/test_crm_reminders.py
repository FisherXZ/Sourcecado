"""Optional reminders reuse ordinary tasks and never repeat a Gmail action."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from coworker.crm_tasks import TaskService
from coworker.gmail import GmailError, GmailOutcomeUnknown
from test_approved_send import HEADERS, _app, _bound_person, _decide, _draft, _gmail, _park


def sent_message(root):
    gmail = _gmail()
    app = _app(root, gmail)
    client = TestClient(app)
    pid, sid = _bound_person(app)
    draft = _draft(client, pid, sid)
    approval = _park(client, pid, sid, draft)
    sent = _decide(client, approval).json()
    assert sent["ok"] is True and sent["result"]["sent"] is True
    return app, client, gmail, pid, sent["result"]["message_id"]


def path(pid, message):
    return f"/v1/people/{pid}/sent-messages/{message}/reminder"


def choice(client, pid, message, **changes):
    response = client.get(path(pid, message), headers=HEADERS)
    assert response.status_code == 200, response.text
    context = response.json()
    return {"title": "Ask Ada about Thursday", "due_date": "2026-11-06",
            "operation_id": context["operation_id"], "timezone": context["timezone"], **changes}


def test_confirmed_send_offers_context_without_creating_a_task_or_choosing_a_date(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    app.state.store.set_setting("operator_timezone", "Pacific/Kiritimati")
    response = client.get(path(pid, mid), headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["timezone"] == "Pacific/Kiritimati"
    assert response.json()["task"] is None
    assert "due_date" not in response.json()
    assert TaskService(app.state.people).list(timezone="Pacific/Kiritimati")["tasks"] == []
    assert len(gmail.send_attempts) == 1


def test_one_task_links_the_confirmed_message_in_global_and_person_lists(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    before_sequence = app.state.people.get(pid)["sequence_state"]
    payload = choice(client, pid, mid)
    saved = client.post(path(pid, mid), headers=HEADERS, json=payload)
    assert saved.status_code == 201, saved.text
    task = saved.json()["task"]
    assert task["title"] == payload["title"] and task["due_date"] == "2026-11-06"
    assert task["due_timezone"] == payload["timezone"] and task["origin"] == "human"
    assert task["source_refs"] and task["source_links"][0]["url"].endswith("thread_1")
    source = next(s for s in app.state.people.get(pid, expand_sources=True)["sources"] if s["id"] == task["source_refs"][0])
    assert source["fields"]["message_id"] == mid
    global_tasks = client.get("/v1/crm/tasks", headers=HEADERS).json()["tasks"]
    person_tasks = client.get("/v1/crm/tasks", headers=HEADERS, params={"person_id": pid}).json()["tasks"]
    assert global_tasks[0]["task_id"] == person_tasks[0]["task_id"] == task["task_id"]
    assert app.state.people.get(pid)["sequence_state"] == before_sequence
    assert len(gmail.send_attempts) == 1


def test_repeated_concurrent_saves_and_restart_return_the_same_task(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    payload = choice(client, pid, mid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(path(pid, mid), headers=HEADERS, json=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    ids = {response.json()["task"]["task_id"] for response in responses}
    assert len(ids) == 1
    restarted = _app(tmp_path, gmail)
    again = TestClient(restarted).post(path(pid, mid), headers=HEADERS, json=payload)
    assert again.status_code == 200 and again.json()["task"]["task_id"] in ids
    assert restarted.state.people._conn.execute("SELECT count(*) FROM person_tasks").fetchone()[0] == 1
    assert restarted.state.people._conn.execute("SELECT count(*) FROM crm_changes WHERE entity_type='task'").fetchone()[0] == 1
    assert len(gmail.send_attempts) == 1


def test_a_changed_duplicate_save_conflicts_without_losing_the_original_task(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    payload = choice(client, pid, mid)
    original = client.post(path(pid, mid), headers=HEADERS, json=payload).json()["task"]
    changed = client.post(path(pid, mid), headers=HEADERS, json={**payload, "due_date": "2026-11-07"})
    assert changed.status_code == 409
    assert TaskService(app.state.people).get(pid, original["task_id"])["due_date"] == "2026-11-06"
    assert len(gmail.send_attempts) == 1


def test_invalid_date_and_failed_task_write_roll_back_the_source_and_allow_retry(tmp_path, monkeypatch):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    payload = choice(client, pid, mid)
    before = app.state.people._conn.execute("SELECT count(*) FROM person_attachments").fetchone()[0]
    invalid = client.post(path(pid, mid), headers=HEADERS, json={**payload, "due_date": "2026-02-30"})
    assert invalid.status_code == 400
    assert app.state.people._conn.execute("SELECT count(*) FROM person_attachments").fetchone()[0] == before
    original = TaskService._create_uncommitted

    def fail_after_task(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("local disk write failed")

    monkeypatch.setattr(TaskService, "_create_uncommitted", fail_after_task)
    failing = TestClient(app, raise_server_exceptions=False)
    assert failing.post(path(pid, mid), headers=HEADERS, json=payload).status_code == 503
    assert app.state.people._conn.execute("SELECT count(*) FROM person_tasks").fetchone()[0] == 0
    assert app.state.people._conn.execute("SELECT count(*) FROM person_attachments").fetchone()[0] == before
    monkeypatch.undo()
    assert client.post(path(pid, mid), headers=HEADERS, json=payload).status_code == 201
    assert len(gmail.send_attempts) == 1


def test_reply_and_external_gmail_activity_leave_the_human_reminder_open(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    payload = choice(client, pid, mid)
    task = client.post(path(pid, mid), headers=HEADERS, json=payload).json()["task"]
    app.state.people.file_inbound_reply(pid, message_id="reply_later", thread_id="thread_1",
        sender="ada@analytic.example", subject="Re: Thursday?", snippet="Thanks", received_at=None)
    gmail.deliver(thread_id="thread_1", message_id="external_reply", sender="director@sourcecado.test", to="ada@analytic.example", subject="Re: Thursday?")
    tasks = TaskService(app.state.people)
    assert tasks.get(pid, task["task_id"])["state"] == "open"
    edited = tasks.update(pid, task["task_id"], changes={"due_date": "2026-11-08"}, expected_version=1,
        operation_id="reminder-edit-date", actor="director", timezone=payload["timezone"])
    tasks.undo(pid, edited["change"]["change_id"], expected_version=2, operation_id="reminder-undo-date", actor="director")
    assert tasks.get(pid, task["task_id"])["due_date"] == "2026-11-06"
    assert len(gmail.send_attempts) == 1


def test_calendar_day_is_kept_in_saved_timezone_at_utc_boundary(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    app.state.store.set_setting("operator_timezone", "Pacific/Kiritimati")
    payload = choice(client, pid, mid, due_date="2026-01-02")
    assert client.post(path(pid, mid), headers=HEADERS, json=payload).status_code == 201
    result = TaskService(app.state.people).list(timezone="Pacific/Kiritimati", now=datetime(2026, 1, 1, 12, tzinfo=UTC))
    assert result["today"] == "2026-01-02"
    assert result["tasks"][0]["due_status"] == "today"
    assert len(gmail.send_attempts) == 1


def test_a_confirmed_reply_gets_a_new_reminder_without_completing_the_original_task(tmp_path):
    from test_follow_up_reply import _flow, _reply_draft, _park as park_reply
    gmail, app, client, pid, sid, thread, original_task = _flow(tmp_path)
    draft = _reply_draft(client, pid, sid, thread_id=thread, task_id=original_task["task_id"]).json()["draft"]
    parked = park_reply(client, pid, sid, draft, thread_id=thread, task_id=original_task["task_id"]).json()["item"]
    # This fixture uses the follow-up suite's token, as does its TestClient.
    from test_follow_up_reply import _decide as decide_reply, HEADERS as REPLY_HEADERS
    sent = decide_reply(client, parked["id"]).json()
    assert sent["ok"] is True
    mid = sent["result"]["message_id"]
    context = client.get(path(pid, mid), headers=REPLY_HEADERS).json()
    saved = client.post(path(pid, mid), headers=REPLY_HEADERS, json={
        "title": "Check the deck", "due_date": "2026-11-06",
        "operation_id": context["operation_id"], "timezone": context["timezone"],
    })
    assert saved.status_code == 201, saved.text
    assert saved.json()["task"]["task_id"] != original_task["task_id"]
    assert TaskService(app.state.people).get(pid, original_task["task_id"])["state"] == "open"
    assert len(gmail.send_attempts) == 1


def test_another_person_or_unconfirmed_message_cannot_authorize_a_reminder(tmp_path):
    app, client, gmail, pid, mid = sent_message(tmp_path)
    other, _sid = _bound_person(app, apollo_id="other")
    for target, message in [(other, mid), (pid, "missing")]:
        response = client.get(path(target, message), headers=HEADERS)
        assert response.status_code == 409
        assert response.json()["code"] == "send_not_confirmed"
        assert client.post(path(target, message), headers=HEADERS, json={}).status_code == 409
    # An event claiming "sent" without its succeeded reviewed approval is insufficient.
    app.state.people.record_approved_send(pid, message_id="unverified", thread_id="thread_1",
        draft_id="unknown", to="ada@analytic.example", subject="Claimed sent", body_digest="digest",
        account="director@sourcecado.test", approval_id="missing_approval")
    assert client.get(path(pid, "unverified"), headers=HEADERS).status_code == 409
    assert TaskService(app.state.people).list(timezone="America/Los_Angeles")["tasks"] == []


@pytest.mark.parametrize("decision", ["pending", "deny", "failed", "unknown"])
def test_pending_denied_failed_and_unknown_sends_offer_no_reminder(tmp_path, decision):
    gmail = _gmail()
    app = _app(tmp_path, gmail)
    client = TestClient(app)
    pid, sid = _bound_person(app)
    draft = _draft(client, pid, sid)
    approval = _park(client, pid, sid, draft)
    if decision in {"failed", "unknown"}:
        def fail(*, draft_id):
            gmail.send_attempts.append(draft_id)
            raise (GmailOutcomeUnknown("No answer") if decision == "unknown" else GmailError("Refused"))
        gmail.send = fail
    if decision != "pending":
        _decide(client, approval, "deny" if decision == "deny" else "allow")
    assert len(gmail.send_attempts) == (1 if decision in {"failed", "unknown"} else 0)
    response = client.get(path(pid, "message_1"), headers=HEADERS)
    assert response.status_code == 409
    assert TaskService(app.state.people).list(timezone="America/Los_Angeles")["tasks"] == []
