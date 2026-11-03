import threading
import time

import pytest
from fastapi.testclient import TestClient

from coworker.automation.scheduler import Scheduler
from coworker.gmail import FakeGmail
from coworker.inbox import Inbox
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore

TOKEN = "test-token-schedule"


def _wait_for_finished_run(store, run_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = next(r for r in store.list_schedule()["runs"] if r["id"] == run_id)
        if run["status"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never finished")


def test_create_routine_api_validates_supported_template_cadence_and_prompt(tmp_path):
    client = TestClient(create_app(token=TOKEN, state=tmp_path))

    response = client.post(
        "/v1/schedule",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "template_id": "unknown-template",
            "cadence": "every-minute",
            "name": " ",
            "prompt": "sk-secret-should-never-echo " * 200,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_routine"
    assert set(body["fields"]) == {"template_id", "cadence", "name", "prompt"}
    assert "sk-secret-should-never-echo" not in str(body)


def test_create_routine_api_persists_template_identity_and_next_run(tmp_path):
    client = TestClient(create_app(token=TOKEN, state=tmp_path))

    response = client.post(
        "/v1/schedule",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "template_id": "weekly_sourcing_review",
            "cadence": "weekly_monday_0900",
            "name": "Weekly priority review",
            "prompt": "Review the highest-priority sourcing work for this week.",
        },
    )

    assert response.status_code == 201
    job = response.json()["job"]
    assert job["template_id"] == "weekly_sourcing_review"
    assert job["cadence"] == "weekly_monday_0900"
    assert job["name"] == "Weekly priority review"
    assert job["cron"] == "0 9 * * 1"
    assert job["next_run_at"]
    restarted = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/schedule", headers={TOKEN_HEADER: TOKEN}
    ).json()
    assert restarted["jobs"] == [job]
    assert restarted["templates"][0]["id"] == "weekly_sourcing_review"


def test_list_schedule_includes_next_run_at(tmp_path):
    store = ConversationStore(tmp_path)
    store.add_job("0 9 * * 1", "weekly", next_run_at="2026-01-01T09:00:00")
    listing = store.list_schedule()
    assert listing["jobs"][0]["next_run_at"] == "2026-01-01T09:00:00"


def test_store_job_and_run(tmp_path):
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "weekly check-in")
    assert job["cron"] == "0 9 * * 1"
    assert job["prompt"] == "weekly check-in"
    run = store.record_run(job["id"], "ok", "ran")
    assert run["job_id"] == job["id"]
    assert run["status"] == "ok"
    listing = store.list_schedule()
    assert listing["jobs"][0]["cron"] == "0 9 * * 1"
    assert listing["runs"][0]["result"] == "ran"


def test_scheduler_persists_running_then_durable_success_receipt(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    job = store.add_job(
        "0 9 * * 1",
        "weekly review",
        next_run_at="2026-08-31T09:00:00-07:00",
    )

    def runner(current_job):
        running = store.list_schedule()["runs"][0]
        assert running["status"] == "running"
        assert running["finished_at"] is None
        assert running["session_id"] == f"sched-{current_job['id']}"
        return {
            "status": "ok",
            "result": "Found three priority contacts.",
            "summary": "Three priority contacts are ready for review.",
            "artifacts": [
                {
                    "id": "artifact-shortlist",
                    "artifact_type": "shortlist",
                    "title": "Priority shortlist",
                    "external_url": "https://example.test/shortlist",
                    "raw_payload": "secret-never-persist",
                }
            ],
        }

    receipt = Scheduler(store, inbox).run_job(int(job["id"]), runner=runner)

    assert receipt["status"] == "success"
    assert receipt["summary"] == "Three priority contacts are ready for review."
    assert receipt["result"] == "Found three priority contacts."
    assert receipt["session_id"] == f"sched-{job['id']}"
    assert receipt["duration_ms"] >= 0
    assert receipt["started_at"]
    assert receipt["finished_at"]
    assert receipt["artifacts"] == [
        {
            "id": "artifact-shortlist",
            "artifact_type": "shortlist",
            "title": "Priority shortlist",
            "external_url": "https://example.test/shortlist",
        }
    ]
    assert "secret-never-persist" not in str(receipt)
    restarted = ConversationStore(tmp_path).list_schedule()["runs"][0]
    assert restarted == receipt


def test_scheduler_collects_artifact_metadata_from_durable_turn_events(tmp_path):
    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly")
    session_id = f"sched-{job['id']}"

    def runner(_job):
        store.append_event(
            session_id,
            {
                "type": "tool_finished",
                "artifacts": [
                    {
                        "id": "draft-1",
                        "artifact_type": "draft",
                        "title": "Review-ready draft",
                        "external_url": "https://example.test/draft",
                        "preview": "Private draft body must not enter receipt metadata.",
                    }
                ],
            },
        )
        return {"status": "ok", "text": "Draft prepared."}

    receipt = scheduler.run_job(int(job["id"]), runner=runner)

    assert receipt["artifacts"] == [
        {
            "id": "draft-1",
            "artifact_type": "draft",
            "title": "Review-ready draft",
            "external_url": "https://example.test/draft",
        }
    ]
    assert "Private draft body" not in str(receipt)


@pytest.mark.parametrize(
    ("runner_status", "receipt_status"),
    [
        ("ok", "success"),
        ("error", "failed"),
        ("waiting", "waiting_approval"),
        ("partial", "partial"),
    ],
)
def test_scheduler_normalizes_durable_receipt_statuses(
    tmp_path, runner_status, receipt_status
):
    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly")

    receipt = scheduler.run_job(
        int(job["id"]),
        runner=lambda _job: {"status": runner_status, "result": "receipt detail"},
    )

    assert receipt["status"] == receipt_status


def test_waiting_approval_receipt_counts_pending_context_without_resuming(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    scheduler = Scheduler(store, inbox)
    job = store.add_job("0 9 * * 1", "draft review")
    session_id = f"sched-{job['id']}"

    def wait_for_approval(_job):
        inbox.park(
            "gmail_draft",
            {"to": "operator@example.com"},
            item_id="approval-scheduled",
            session_id=session_id,
            run_id="turn-scheduled",
            message_id="message-scheduled",
            part_id="part-scheduled",
        )
        return {"status": "waiting", "result": "Draft review is waiting for approval."}

    receipt = scheduler.run_job(int(job["id"]), runner=wait_for_approval)
    before_resolution = len(store.list_schedule()["runs"])
    restarted_store = ConversationStore(tmp_path)
    restarted_inbox = Inbox(restarted_store)

    assert receipt["status"] == "waiting_approval"
    assert receipt["waiting_approval_count"] == 1
    assert restarted_store.list_schedule()["jobs"][0]["id"] == job["id"]
    assert restarted_store.list_schedule()["runs"][0] == receipt
    assert restarted_inbox.pending()[0]["id"] == "approval-scheduled"

    restarted_inbox.resolve("approval-scheduled", "allow", actor="operator")

    assert len(restarted_store.list_schedule()["runs"]) == before_resolution


def test_scheduler_runner_exception_persists_safe_failure_summary(tmp_path):
    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly")

    def fail(_job):
        raise RuntimeError("token=provider-secret /private/operator/state")

    receipt = scheduler.run_job(int(job["id"]), runner=fail)

    assert receipt["status"] == "failed"
    assert receipt["summary"] == "The routine failed before it could finish."
    assert "provider-secret" not in str(receipt)
    assert "/private/operator" not in str(receipt)


def test_schedule_api_lists_job_and_run(tmp_path):
    application = create_app(token=TOKEN, state=tmp_path)
    application.state.store.add_job("0 9 * * 1", "weekly check-in")
    application.state.store.record_run(1, "ok", "caught up")
    res = TestClient(application).get("/v1/schedule", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["jobs"][0]["cron"] == "0 9 * * 1"
    assert body["jobs"][0]["prompt"] == "weekly check-in"
    assert body["runs"][0]["status"] == "ok"
    assert body["runs"][0]["result"] == "caught up"


def test_tick_sets_next_monday_0900(tmp_path):
    from coworker.automation.scheduler import next_monday_0900

    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    store.add_job("0 9 * * 1", "weekly", next_run_at="2020-01-01T09:00:00")
    Scheduler(store, inbox).tick(now="2020-01-06T10:00:00-08:00")
    nxt = store.list_schedule()["jobs"][0]["next_run_at"]
    assert nxt == next_monday_0900("2020-01-06T10:00:00-08:00")
    assert nxt.startswith("2020-01-13T09:00:00")


def test_run_now_does_not_move_next_run_at(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    job = store.add_job("0 9 * * 1", "weekly", next_run_at="2026-08-31T09:00:00-07:00")
    ran = Scheduler(store, inbox).run_job(int(job["id"]))
    assert ran["status"] == "success"
    assert store.list_schedule()["jobs"][0]["next_run_at"] == "2026-08-31T09:00:00-07:00"
    assert store.list_schedule()["runs"][0]["id"] == ran["id"]


def test_post_schedule_run_fires_undue_job(tmp_path):
    from coworker.provider import FakeProvider

    application = create_app(token=TOKEN, state=tmp_path, provider=FakeProvider(deltas=("ok",)))
    job = application.state.store.add_job(
        "0 9 * * 1", "weekly", next_run_at="2099-01-01T09:00:00-08:00"
    )
    res = TestClient(application).post(
        f"/v1/schedule/{job['id']}/run", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 202
    assert res.json()["run"]["status"] == "running"
    finished = _wait_for_finished_run(application.state.store, res.json()["run"]["id"])
    assert finished["status"] == "success"
    assert application.state.store.list_schedule()["jobs"][0]["next_run_at"] == "2099-01-01T09:00:00-08:00"


def test_post_schedule_run_unknown_404(tmp_path):
    res = TestClient(create_app(token=TOKEN, state=tmp_path)).post(
        "/v1/schedule/99/run", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 404


def test_post_schedule_run_already_running_returns_safe_context(tmp_path):
    application = create_app(token=TOKEN, state=tmp_path)
    job = application.state.store.add_job("0 9 * * 1", "weekly")
    application.state.scheduler._running.add(int(job["id"]))

    response = TestClient(application).post(
        f"/v1/schedule/{job['id']}/run", headers={TOKEN_HEADER: TOKEN}
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "already_running",
        "message": "This routine is already running. Wait for its current receipt.",
    }


def test_scheduler_due_job_records_run(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    store.add_job("0 9 * * 1", "weekly check-in", next_run_at="2020-01-01T09:00:00")
    ran = Scheduler(store, inbox).tick(now="2020-01-06T10:00:00")
    assert len(ran) == 1
    assert ran[0]["status"] == "success"
    listing = store.list_schedule()
    assert listing["runs"][0]["status"] == "success"
    assert store.due_jobs("2020-01-06T10:00:00") == []


def test_scheduler_skips_overlap(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    sched = Scheduler(store, inbox)
    store.add_job("0 9 * * 1", "weekly", next_run_at="2020-01-01T09:00:00")

    def runner(job):
        nested = sched.tick(now="2020-01-01T09:00:00", runner=lambda j: {"status": "nested"})
        assert nested == []
        return {"status": "ok", "result": "outer"}

    ran = sched.tick(now="2020-01-01T09:00:00", runner=runner)
    assert ran[0]["result"] == "outer"


def test_scheduler_due_job_runs_turn_on_sched_session(tmp_path):
    from coworker.provider import FakeProvider

    fake = FakeProvider(deltas=("weekly ping",))
    application = create_app(token=TOKEN, provider=fake, state=tmp_path)
    store = application.state.store
    job = store.add_job("0 9 * * 1", "weekly sourcing check-in", next_run_at="2020-01-01T09:00:00")
    ran = application.state.scheduler.tick(now="2020-01-06T10:00:00")
    assert ran[0]["status"] == "success"
    sid = f"sched-{job['id']}"
    msgs = store.load(sid)
    assert msgs[0]["role"] == "user"
    assert "weekly sourcing check-in" in msgs[0]["content"]
    assert any(m.get("role") == "assistant" for m in msgs)


def test_opening_scheduled_thread_restores_events_without_replacing_normal_chat(tmp_path):
    from coworker.provider import FakeProvider

    application = create_app(
        token=TOKEN,
        provider=FakeProvider(deltas=("weekly ping",)),
        state=tmp_path,
    )
    store = application.state.store
    normal_session_id = store.open_session_id()
    job = store.add_job(
        "0 9 * * 1",
        "weekly sourcing check-in",
        next_run_at="2020-01-01T09:00:00",
    )
    application.state.scheduler.tick(now="2020-01-06T10:00:00")
    scheduled_session_id = f"sched-{job['id']}"

    response = TestClient(application).get(
        f"/v1/sessions/{scheduled_session_id}", headers={TOKEN_HEADER: TOKEN}
    )

    assert response.status_code == 200
    assert response.json()["events"]
    assert response.json()["events"][0]["type"] == "turn_start"
    assert response.json()["messages"][0]["content"] == "weekly sourcing check-in"
    assert store.open_session_id() == normal_session_id
    assert all(
        session["session_id"] != scheduled_session_id
        for session in store.list_sessions()
    )


def test_scheduler_ask_parks_inbox_no_draft_live(tmp_path):
    from coworker.provider import FakeProvider, ToolCall

    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="c1",
                        name="gmail_draft",
                        arguments={
                            "to": "alyssa@berkeley.edu",
                            "subject": "hi",
                            "body": "hello",
                        },
                    )
                ]
            }
        ]
    )
    gmail = FakeGmail()
    application = create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    store = application.state.store
    store.add_job("0 9 * * 1", "draft Alyssa", next_run_at="2020-01-01T09:00:00")
    application.state.scheduler.tick(now="2020-01-01T09:00:00")
    assert application.state.inbox.pending()[0]["name"] == "gmail_draft"
    assert gmail.drafts == []


def test_scheduler_tick_records_runner_error(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    sched = Scheduler(store, inbox)
    store.add_job("0 9 * * 1", "boom", next_run_at="2020-01-01T09:00:00")

    def boom(job):
        raise RuntimeError("tick exploded")

    ran = sched.tick(now="2020-01-01T09:00:00", runner=boom)
    assert ran[0]["status"] == "failed"
    assert ran[0]["result"] == "The routine failed before it could finish."
    assert store.due_jobs("2020-01-01T09:00:00") == []


def test_scheduler_ask_tool_parks_inbox_no_draft(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    gmail = FakeGmail()
    store.add_job("0 9 * * 1", "draft Alyssa", next_run_at="2020-01-01T09:00:00")

    def runner(job):
        inbox.park(
            "gmail_draft",
            {"to": "alyssa@berkeley.edu", "subject": "hi", "body": "hello"},
            item_id="sched-1",
        )
        return {"status": "waiting", "result": "parked"}

    Scheduler(store, inbox).tick(now="2020-01-01T09:00:00", runner=runner)
    assert inbox.pending()[0]["name"] == "gmail_draft"
    assert gmail.drafts == []


def test_run_now_race_starts_exactly_one_concurrent_run(tmp_path):
    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly", next_run_at=None)
    barrier = threading.Barrier(2)

    class RacingSet(set):
        """Holds both threads inside the check-then-act window."""

        def __contains__(self, item):
            hit = set.__contains__(self, item)
            try:
                barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
            return hit

    scheduler._running = RacingSet()
    runs = []

    def runner(job_row):
        runs.append(int(job_row["id"]))
        time.sleep(0.05)
        return {"status": "ok", "result": "tick"}

    outcomes = []

    def fire():
        try:
            outcomes.append(scheduler.run_job(int(job["id"]), runner=runner))
        except RuntimeError:
            outcomes.append("already_running")

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(runs) == 1
    assert outcomes.count("already_running") == 1
    statuses = [run["status"] for run in store.list_schedule()["runs"]]
    assert statuses == ["success"]


def test_ws_chat_cannot_write_a_scheduled_session(tmp_path):
    from coworker.provider import FakeProvider

    fake = FakeProvider(deltas=("must not run",))
    app = create_app(token="test-token-schedule", provider=fake, state=tmp_path)
    app.state.store.create_session("sched-7")
    client = TestClient(app)

    with client.websocket_connect(
        "/ws/chat", subprotocols=["club", "test-token-schedule"]
    ) as ws:
        ws.send_json(
            {"type": "chat", "text": "steer the routine", "session_id": "sched-7"}
        )
        chat_reply = ws.receive_json()
        ws.send_json(
            {
                "type": "queue_add",
                "session_id": "sched-7",
                "command_id": "sched-queue-add",
                "item_id": "sched-item",
                "text": "queued steer",
            }
        )
        queue_reply = ws.receive_json()

    assert chat_reply["type"] == "error"
    assert queue_reply["type"] == "error"
    assert app.state.store.load("sched-7") == []
    assert app.state.store.list_queue("sched-7") == []
    assert fake.calls == []


def test_store_reopen_heals_orphaned_running_run_to_interrupted(tmp_path):
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "weekly", next_run_at=None)
    running = store.start_run(
        int(job["id"]),
        session_id=f"sched-{job['id']}",
        started_at="2026-08-26T09:00:00+00:00",
    )
    assert running["status"] == "running"
    assert running["finished_at"] is None

    healed = ConversationStore(tmp_path).list_schedule()["runs"][0]

    assert healed["status"] == "interrupted"
    assert healed["finished_at"]
    assert "restarted" in healed["summary"].lower()
    assert "success" not in healed["summary"].lower()


def test_legacy_ok_run_normalizes_to_success_on_restart(tmp_path):
    """P2: production rows written before the canonical status vocabulary
    existed used run_turn's raw "ok" directly. Restoring the store must heal
    them to "success" so the UI renders them as Completed, not Failed."""
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "weekly check-in")
    store.record_run(job["id"], "ok", "Found three priority contacts.")

    restarted = ConversationStore(tmp_path).list_schedule()["runs"][0]

    assert restarted["status"] == "success"
    assert restarted["result"] == "Found three priority contacts."


def test_reopen_recovers_true_success_from_the_scheduled_transcript(tmp_path):
    """P2: a crash can land between the transcript's terminal event and the
    matching runs-table update. When the transcript already recorded the
    outcome, trust it instead of blindly stamping 'interrupted'."""
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "weekly", next_run_at=None)
    session_id = f"sched-{job['id']}"
    store.start_run(
        int(job["id"]), session_id=session_id, started_at="2026-08-26T09:00:00+00:00"
    )
    store.append_event(session_id, {"type": "turn_start", "state": "running"})
    store.append_event(
        session_id,
        {"type": "turn_end", "text": "Found three priority contacts.", "state": "complete"},
    )

    healed = ConversationStore(tmp_path).list_schedule()["runs"][0]

    assert healed["status"] == "success"
    assert healed["result"] == "Found three priority contacts."
    assert healed["finished_at"]


def test_reopen_recovers_true_failure_from_the_scheduled_transcript(tmp_path):
    """P2: same as above, but the transcript's terminal event was an error."""
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "weekly", next_run_at=None)
    session_id = f"sched-{job['id']}"
    store.start_run(
        int(job["id"]), session_id=session_id, started_at="2026-08-26T09:00:00+00:00"
    )
    store.append_event(
        session_id, {"type": "error", "state": "failed", "message": "provider timed out"}
    )

    healed = ConversationStore(tmp_path).list_schedule()["runs"][0]

    assert healed["status"] == "failed"
    assert healed["result"] == "provider timed out"


def test_reopen_does_not_copy_last_weeks_completed_receipt_when_this_attempt_never_turned(
    tmp_path,
):
    """Scheduled jobs reuse sched-{job_id}. A crash after start_run and
    before this week's turn_start must not adopt last week's terminal
    event as this week's outcome."""
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "weekly", next_run_at=None)
    session_id = f"sched-{job['id']}"

    week1 = store.start_run(
        int(job["id"]),
        session_id=session_id,
        started_at="2026-08-17T16:00:00+00:00",
    )
    store.append_event(session_id, {"type": "turn_start", "state": "running"})
    store.append_event(
        session_id,
        {
            "type": "turn_end",
            "text": "Week 1 found three contacts.",
            "state": "complete",
        },
    )
    store.finish_run(
        int(week1["id"]),
        status="success",
        result="Week 1 found three contacts.",
        summary="Week 1 found three contacts.",
        artifacts=[],
        duration_ms=1500,
        finished_at="2026-08-17T16:00:02+00:00",
    )

    week2 = store.start_run(
        int(job["id"]),
        session_id=session_id,
        started_at="2026-08-24T16:00:00+00:00",
    )
    assert week2["status"] == "running"
    assert week2["duration_ms"] is None

    first, second = ConversationStore(tmp_path).list_schedule()["runs"]

    assert first["status"] == "success"
    assert first["result"] == "Week 1 found three contacts."
    assert second["id"] == week2["id"]
    assert second["status"] == "interrupted"
    assert second["result"] != "Week 1 found three contacts."
    assert second["summary"] != "Week 1 found three contacts."
    assert "restarted" in second["summary"].lower()


def test_scheduled_run_statuses_stay_inside_the_shared_contract(tmp_path):
    """S4: every status the backend writes to the runs table is declared, so
    the client never coerces an honest outcome into 'Failed'."""
    from coworker.automation.scheduler import (
        RECEIPT_STATUSES,
        SCHEDULE_RUN_STATUSES,
    )

    # The full declared vocabulary, including the restart reconciler's status.
    assert set(RECEIPT_STATUSES.values()) <= SCHEDULE_RUN_STATUSES
    assert "running" in SCHEDULE_RUN_STATUSES
    assert "interrupted" in SCHEDULE_RUN_STATUSES
    # run_turn's whole status vocabulary is mapped; nothing passes through.
    assert set(RECEIPT_STATUSES) == {"ok", "error", "waiting", "partial", "stopped"}

    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly")
    # A scheduled run that exhausts its step budget did real, incomplete work.
    receipt = scheduler.run_job(
        int(job["id"]),
        runner=lambda j: {"status": "stopped", "text": "hit the step limit"},
    )
    assert receipt["status"] == "partial"
    assert receipt["status"] in SCHEDULE_RUN_STATUSES

    # The restart reconciler's status is part of the same contract.
    store.start_run(
        int(job["id"]),
        session_id=f"sched-{job['id']}",
        started_at="2026-08-26T00:00:00+00:00",
    )
    reopened = ConversationStore(tmp_path)
    statuses = [run["status"] for run in reopened.list_schedule()["runs"]]
    assert "interrupted" in statuses
    assert set(statuses) <= SCHEDULE_RUN_STATUSES


def _create(client, **overrides):
    body = {
        "template_id": "weekly_sourcing_review",
        "cadence": "weekly_monday_0900",
        "name": "Weekly priority review",
        "prompt": "Review the highest-priority sourcing work for this week.",
        **overrides,
    }
    response = client.post("/v1/schedule", headers={TOKEN_HEADER: TOKEN}, json=body)
    assert response.status_code == 201
    return response.json()["job"]


@pytest.mark.parametrize(
    ("cadence", "now", "expected"),
    [
        # Friday after 9:00 moves to Monday for weekly and weekday cadences.
        ("weekly_monday_0900", "2026-11-06T10:00:00-08:00", "2026-11-09T09:00:00-08:00"),
        ("weekdays_0900", "2026-11-06T10:00:00-08:00", "2026-11-09T09:00:00-08:00"),
        ("daily_0900", "2026-11-06T10:00:00-08:00", "2026-11-07T09:00:00-08:00"),
        # Before 9:00 on a matching day, the same day's slot is next.
        ("weekdays_0900", "2026-11-04T08:59:00-08:00", "2026-11-04T09:00:00-08:00"),
        # Exactly 9:00 is not "after now", so the slot moves on.
        ("daily_0900", "2026-11-04T09:00:00-08:00", "2026-11-05T09:00:00-08:00"),
        # Daylight saving ends on 2026-11-01; the slot stays at 9:00 local.
        ("daily_0900", "2026-10-31T10:00:00-07:00", "2026-11-01T09:00:00-08:00"),
    ],
)
def test_next_run_for_each_preset_cadence(cadence, now, expected):
    from coworker.automation.scheduler import next_run_for

    assert next_run_for(cadence, now) == expected


def test_schedule_lists_cadences_and_reads_legacy_cron_jobs_as_presets(tmp_path):
    application = create_app(token=TOKEN, state=tmp_path)
    application.state.store.add_job(
        "0 9 * * 1", "weekly sourcing check-in", next_run_at="2099-01-05T09:00:00-08:00"
    )
    application.state.store.add_job("0 9 * * 1", "never scheduled")

    body = TestClient(application).get("/v1/schedule", headers={TOKEN_HEADER: TOKEN}).json()

    assert [c["id"] for c in body["cadences"]] == [
        "weekly_monday_0900",
        "weekdays_0900",
        "daily_0900",
    ]
    assert body["cadences"][0]["label"] == "Every Monday at 9:00 AM"
    assert [job["cadence"] for job in body["jobs"]] == ["weekly_monday_0900"] * 2
    assert [job["enabled"] for job in body["jobs"]] == [True, False]


def test_edit_routine_changes_prompt_name_and_cadence_and_survives_restart(tmp_path):
    client = TestClient(create_app(token=TOKEN, state=tmp_path))
    job = _create(client)

    response = client.patch(
        f"/v1/schedule/{job['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "name": "  Daily replies  ",
            "prompt": "Check replies from open sequences.",
            "cadence": "weekdays_0900",
        },
    )

    assert response.status_code == 200
    edited = response.json()["job"]
    assert edited["name"] == "Daily replies"
    assert edited["prompt"] == "Check replies from open sequences."
    assert edited["cadence"] == "weekdays_0900"
    assert edited["cron"] == "0 9 * * 1-5"
    assert edited["enabled"] is True
    from datetime import datetime

    assert datetime.fromisoformat(edited["next_run_at"]).weekday() < 5
    restarted = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/schedule", headers={TOKEN_HEADER: TOKEN}
    ).json()
    assert restarted["jobs"] == [edited]


def test_edit_routine_rejects_invalid_fields_without_changing_the_job(tmp_path):
    client = TestClient(create_app(token=TOKEN, state=tmp_path))
    job = _create(client)

    response = client.patch(
        f"/v1/schedule/{job['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json={"name": " ", "prompt": "x" * 2001, "cadence": "hourly", "enabled": "no"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_routine"
    assert set(response.json()["fields"]) == {"name", "prompt", "cadence", "enabled"}
    listing = client.get("/v1/schedule", headers={TOKEN_HEADER: TOKEN}).json()
    assert listing["jobs"] == [job]
    missing = client.patch(
        "/v1/schedule/999", headers={TOKEN_HEADER: TOKEN}, json={"enabled": False}
    )
    assert missing.status_code == 404


def test_pause_stops_ticks_and_resume_starts_from_the_next_slot(tmp_path):
    application = create_app(token=TOKEN, state=tmp_path)
    client = TestClient(application)
    store = application.state.store
    job = store.add_job(
        "0 9 * * 1",
        "weekly",
        next_run_at="2020-01-06T09:00:00-08:00",
        name="Weekly",
        template_id="weekly_sourcing_review",
        cadence="weekly_monday_0900",
    )

    paused = client.patch(
        f"/v1/schedule/{job['id']}", headers={TOKEN_HEADER: TOKEN}, json={"enabled": False}
    ).json()["job"]
    assert paused["enabled"] is False
    assert paused["next_run_at"] is None
    assert application.state.scheduler.tick(now="2020-01-06T10:00:00-08:00") == []

    resumed = client.patch(
        f"/v1/schedule/{job['id']}", headers={TOKEN_HEADER: TOKEN}, json={"enabled": True}
    ).json()["job"]
    assert resumed["enabled"] is True
    # Resume never catches up on the 2020 slot it skipped while paused.
    from datetime import UTC, datetime

    assert datetime.fromisoformat(resumed["next_run_at"]) > datetime.now(UTC)
    assert store.list_schedule()["runs"] == []


def test_pausing_during_a_tick_run_keeps_the_job_paused(tmp_path):
    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly", next_run_at="2020-01-06T09:00:00-08:00")

    def pause_mid_run(current):
        store.set_job_next_run(int(current["id"]), None)
        return {"status": "ok", "result": "done"}

    ran = scheduler.tick(now="2020-01-06T10:00:00-08:00", runner=pause_mid_run)

    assert [run["status"] for run in ran] == ["success"]
    assert store.get_job(int(job["id"]))["next_run_at"] is None


def test_start_job_returns_running_receipt_then_finishes_in_background(tmp_path):
    store = ConversationStore(tmp_path)
    scheduler = Scheduler(store, Inbox(store))
    job = store.add_job("0 9 * * 1", "weekly", next_run_at="2099-01-05T09:00:00-08:00")
    release = threading.Event()

    def slow(_job):
        release.wait(timeout=5)
        return {"status": "ok", "result": "Two replies need a response."}

    running = scheduler.start_job(int(job["id"]), runner=slow)

    assert running["status"] == "running"
    assert running["session_id"] == f"sched-{job['id']}"
    with pytest.raises(RuntimeError):
        scheduler.start_job(int(job["id"]), runner=slow)
    release.set()
    finished = _wait_for_finished_run(store, running["id"])
    assert finished["status"] == "success"
    assert finished["summary"] == "Two replies need a response."
    assert store.get_job(int(job["id"]))["next_run_at"] == "2099-01-05T09:00:00-08:00"
    # The slot is free again once the background run finishes.
    deadline = time.monotonic() + 2
    while int(job["id"]) in scheduler._running and time.monotonic() < deadline:
        time.sleep(0.01)
    assert int(job["id"]) not in scheduler._running
    with pytest.raises(KeyError):
        scheduler.start_job(999)
