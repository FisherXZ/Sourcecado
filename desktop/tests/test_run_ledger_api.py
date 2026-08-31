"""S5: the run ledger's HTTP surface — read only, bounded, and content free."""

import importlib.util
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from coworker.agent_run_repository import AgentRunRepository
from coworker.run_ledger import MAX_QUERY_LIMIT, RunLedger
from coworker.run_ledger_api import run_ledger_router
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "run-ledger-token"
EPOCH = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
SECRET = "sk-proj-WWWWWWWWWWWWWWWWWWWWWWWWWWWW"
BODY = "the director wrote something private here"


def test_run_ledger_api_module_exists():
    assert importlib.util.find_spec("coworker.run_ledger_api") is not None


def _client(tmp_path):
    repo = AgentRunRepository(tmp_path / "runs")
    ledger = RunLedger(repo)
    app = FastAPI()
    app.include_router(run_ledger_router(ledger=ledger))
    return TestClient(app), repo, repo.registry.register()


def _run(repo, owner, **kwargs):
    return repo.create_run(
        session_id=kwargs.pop("session_id", "sess-1"),
        trigger=kwargs.pop("trigger", "chat"),
        goal="Find three Codeology leads at Ramp",
        owner=owner,
        **kwargs,
    )


def test_a_run_opens_from_its_id_and_the_body_is_evidence_only(tmp_path):
    client, repo, owner = _client(tmp_path)
    started = _run(repo, owner, person_id="person-7")
    commit = repo.checkpoint(
        started.lease,
        kind="tool_completed",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "web_search",
            "status": "ok",
            "message": BODY,
            "error_summary": f"noise {SECRET}",
        },
        source_refs=[
            {"id": "src-1", "title": "Ramp", "url": f"https://ramp.test/a?token={SECRET}"}
        ],
    )
    repo.checkpoint(
        commit.lease, kind="terminal", state="complete",
        terminal_result={"status": "complete", "text": BODY},
    )

    response = client.get(f"/v1/agent-runs/{started.run['run_id']}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    receipt = response.json()["receipt"]
    assert receipt["run"]["run_id"] == started.run["run_id"]
    assert receipt["tools"]["calls"][0]["tool_name"] == "web_search"
    assert receipt["sources"]["refs"][0]["url"] == "https://ramp.test/a"
    assert receipt["outcome"]["result"]["text_length"] == len(BODY)
    assert SECRET not in response.text
    assert BODY not in response.text

    missing = client.get("/v1/agent-runs/run-that-never-was")
    assert missing.status_code == 404
    assert missing.json() == {"error": "run_not_found"}


def test_every_entry_point_reaches_the_same_run(tmp_path):
    client, repo, owner = _client(tmp_path)
    chat = _run(repo, owner, session_id="sess-chat", person_id="person-a", now=EPOCH)
    sched = _run(
        repo, owner, session_id="sched-3", trigger="scheduled",
        person_id="person-a", now=EPOCH + timedelta(minutes=5),
    )

    # chat activity, a scheduled receipt, a person timeline, a diagnostic id
    by_session = client.get("/v1/agent-runs", params={"session_id": "sess-chat"})
    by_schedule = client.get("/v1/agent-runs", params={"session_id": "sched-3"})
    by_person = client.get("/v1/agent-runs", params={"person_id": "person-a"})
    by_id = client.get(f"/v1/agent-runs/{sched.run['run_id']}")

    assert [row["run_id"] for row in by_session.json()["runs"]] == [chat.run["run_id"]]
    assert [row["run_id"] for row in by_schedule.json()["runs"]] == [sched.run["run_id"]]
    assert {row["run_id"] for row in by_person.json()["runs"]} == {
        chat.run["run_id"], sched.run["run_id"],
    }
    assert by_id.json()["receipt"]["run"]["run_id"] == sched.run["run_id"]


def test_filters_are_bounded_and_bad_ones_are_refused(tmp_path):
    client, repo, owner = _client(tmp_path)
    first = _run(repo, owner, session_id="sess-a", now=EPOCH)
    repo.checkpoint(
        first.lease, kind="terminal", state="failed", now=EPOCH + timedelta(seconds=1)
    )
    _run(repo, owner, session_id="sess-a", now=EPOCH + timedelta(days=1))

    assert [row["run_id"] for row in client.get(
        "/v1/agent-runs", params={"status": "failed"}
    ).json()["runs"]] == [first.run["run_id"]]
    windowed = client.get(
        "/v1/agent-runs",
        params={"since": EPOCH.isoformat(), "until": (EPOCH + timedelta(hours=1)).isoformat()},
    )
    assert [row["run_id"] for row in windowed.json()["runs"]] == [first.run["run_id"]]

    capped = client.get("/v1/agent-runs", params={"limit": 5000})
    assert capped.json()["limit"] == MAX_QUERY_LIMIT
    assert client.get("/v1/agent-runs", params={"limit": 1}).json()["truncated"] is True

    for bad in ({"status": "vibing"}, {"trigger": "telepathy"}, {"since": "yesterday"}):
        response = client.get("/v1/agent-runs", params=bad)
        assert response.status_code == 400, bad
        assert "error" in response.json()


def test_the_ledger_surface_has_no_write_verb(tmp_path):
    client, repo, owner = _client(tmp_path)
    started = _run(repo, owner)
    run_id = started.run["run_id"]
    for method, path in (
        ("POST", "/v1/agent-runs"),
        ("POST", f"/v1/agent-runs/{run_id}"),
        ("PUT", f"/v1/agent-runs/{run_id}"),
        ("PATCH", f"/v1/agent-runs/{run_id}"),
        ("DELETE", f"/v1/agent-runs/{run_id}"),
    ):
        response = client.request(method, path, json={"current_state": "complete"})
        assert response.status_code in {404, 405}, (method, path, response.status_code)
    assert repo.get_run(run_id)["current_state"] == "running"


def test_the_backend_serves_the_ledger_behind_its_token(tmp_path):
    app = create_app(token=TOKEN, provider=None, state=tmp_path)
    client = TestClient(app)
    started = app.state.agent_runs.create_run(
        session_id="sess-1",
        trigger="chat",
        goal="Find three Codeology leads at Ramp",
        owner=app.state.agent_runs.registry.register(),
    )

    assert client.get("/v1/agent-runs").status_code == 401
    listed = client.get("/v1/agent-runs", headers={TOKEN_HEADER: TOKEN})
    assert listed.status_code == 200
    assert [row["run_id"] for row in listed.json()["runs"]] == [started.run["run_id"]]
    receipt = client.get(
        f"/v1/agent-runs/{started.run['run_id']}", headers={TOKEN_HEADER: TOKEN}
    )
    assert receipt.json()["receipt"]["run"]["session_id"] == "sess-1"
