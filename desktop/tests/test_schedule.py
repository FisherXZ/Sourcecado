from fastapi.testclient import TestClient

from coworker.automation.scheduler import Scheduler
from coworker.gmail import FakeGmail
from coworker.inbox import Inbox
from coworker.server import TOKEN_HEADER, create_app
from coworker.store import ConversationStore

TOKEN = "test-token-schedule"


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
    assert ran["status"] == "ok"
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
    assert res.status_code == 200
    assert res.json()["run"]["status"] == "ok"
    assert application.state.store.list_schedule()["jobs"][0]["next_run_at"] == "2099-01-01T09:00:00-08:00"


def test_post_schedule_run_unknown_404(tmp_path):
    res = TestClient(create_app(token=TOKEN, state=tmp_path)).post(
        "/v1/schedule/99/run", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 404


def test_scheduler_due_job_records_run(tmp_path):
    store = ConversationStore(tmp_path)
    inbox = Inbox(store)
    store.add_job("0 9 * * 1", "weekly check-in", next_run_at="2020-01-01T09:00:00")
    ran = Scheduler(store, inbox).tick(now="2020-01-06T10:00:00")
    assert len(ran) == 1
    assert ran[0]["status"] == "ok"
    listing = store.list_schedule()
    assert listing["runs"][0]["status"] == "ok"
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
    assert ran[0]["status"] == "ok"
    sid = f"sched-{job['id']}"
    msgs = store.load(sid)
    assert msgs[0]["role"] == "user"
    assert "weekly sourcing check-in" in msgs[0]["content"]
    assert any(m.get("role") == "assistant" for m in msgs)


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
    assert ran[0]["status"] == "error"
    assert "exploded" in ran[0]["result"]
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
