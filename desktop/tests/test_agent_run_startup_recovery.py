import json
import time

from fastapi.testclient import TestClient

from coworker.agent_run_execution import AgentRunExecution
from coworker.events import TurnIdentity, build_event
from coworker.provider import FakeProvider
from coworker.server import create_app
from coworker.store import ConversationStore


TOKEN = "test-token-startup-recovery"


def _identity(run_id: str, session_id: str | None = None) -> TurnIdentity:
    return TurnIdentity(
        session_id=session_id or f"thread-{run_id}",
        run_id=run_id,
        message_id=f"message-{run_id}",
        part_id=f"part-{run_id}",
    )


def _seed_interrupted_model(
    state_dir, run_id: str, *, session_id: str | None = None
) -> TurnIdentity:
    store = ConversationStore(state_dir)
    identity = _identity(run_id, session_id)
    execution = AgentRunExecution.start(
        store,
        identity,
        "Find candidates",
        "chat",
        "fake",
        8,
        owner_id="crashed-owner",
        lease_seconds=30,
    )
    store.append(identity.session_id, {"role": "user", "content": "Find candidates"})
    store.append_event(
        identity.session_id,
        build_event(
            identity,
            "turn_start",
            event_id=f"event-{run_id}-start",
            state="running",
        ),
    )
    execution.user_input(
        store.load(identity.session_id), store.load_events(identity.session_id), 15
    )
    execution.model_pending(
        store.load(identity.session_id), store.load_events(identity.session_id), 0
    )
    with store._lock:
        store._conn.execute(
            "UPDATE agent_runs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00+00:00", run_id),
        )
        store._conn.commit()
    store.agent_runs.reconcile_expired_leases()
    return identity


def _wait_for_terminal(store: ConversationStore, run_id: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = store.get_agent_run(run_id)
        if run and run["current_state"] in {"complete", "partial", "failed", "stopped"}:
            return run
        time.sleep(0.01)
    return store.get_agent_run(run_id)


def test_app_startup_resumes_interrupted_model_under_same_identity(tmp_path):
    identity = _seed_interrupted_model(tmp_path, "run-startup-model")
    provider = FakeProvider(deltas=("Recovered on startup",))
    app = create_app(token=TOKEN, provider=provider, state=tmp_path)

    with TestClient(app):
        run = _wait_for_terminal(app.state.store, identity.run_id)

    assert run["current_state"] == "complete"
    assert provider.i == 1
    assert [message["role"] for message in app.state.store.load(identity.session_id)] == [
        "user",
        "assistant",
    ]
    assert json.loads(json.dumps(run["continuation"]))["identity"] == {
        "message_id": identity.message_id,
        "part_id": identity.part_id,
    }
    assert {
        (event["run_id"], event["message_id"], event["part_id"])
        for event in app.state.store.load_events(identity.session_id)
    } == {(identity.run_id, identity.message_id, identity.part_id)}


def test_app_startup_leaves_review_required_run_parked(tmp_path):
    identity = _seed_interrupted_model(tmp_path, "run-startup-review")
    store = ConversationStore(tmp_path)
    run = store.get_agent_run(identity.run_id)
    continuation = run["continuation"]
    continuation["cursor"]["phase"] = "review_required"
    with store._lock:
        store._conn.execute(
            "UPDATE agent_runs SET continuation = ? WHERE run_id = ?",
            (json.dumps(continuation), identity.run_id),
        )
        store._conn.commit()
    provider = FakeProvider(deltas=("must not run",))
    app = create_app(token=TOKEN, provider=provider, state=tmp_path)

    with TestClient(app):
        time.sleep(0.05)

    assert app.state.store.get_agent_run(identity.run_id)["current_state"] == "interrupted"
    assert provider.i == 0


def test_app_startup_updates_linked_schedule_receipt_after_resume(tmp_path):
    store = ConversationStore(tmp_path)
    job = store.add_job("0 9 * * 1", "recover scheduled work")
    session_id = f"sched-{job['id']}"
    identity = _seed_interrupted_model(
        tmp_path, "run-startup-schedule", session_id=session_id
    )
    store.start_run(
        int(job["id"]),
        session_id=session_id,
        started_at="2026-08-26T12:00:00+00:00",
        agent_run_id=identity.run_id,
    )
    app = create_app(
        token=TOKEN,
        provider=FakeProvider(deltas=("Recovered scheduled work",)),
        state=tmp_path,
    )

    with TestClient(app):
        _wait_for_terminal(app.state.store, identity.run_id)

    receipt = next(
        run
        for run in app.state.store.list_schedule()["runs"]
        if run["agent_run_id"] == identity.run_id
    )
    assert receipt["status"] == "success"
    assert receipt["result"] == "Recovered scheduled work"
