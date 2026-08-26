from fastapi.testclient import TestClient

from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.sourcing_index import SourcingIndex
from coworker.tools import OPENAI_TOOLS, execute

TOKEN = "test-token-board-tools"


def test_board_tool_contract_exposes_reads_reversible_writes_and_approved_delete():
    names = {schema["function"]["name"] for schema in OPENAI_TOOLS}

    assert {"board_get", "board_query", "board_upsert", "board_mutate", "board_delete"} <= names
    for name in ("board_get", "board_query", "board_upsert", "board_mutate"):
        decision = decide(name)
        assert decision.allowed is True
        assert decision.needs_user is False
    delete = decide("board_delete")
    assert delete.allowed is False
    assert delete.needs_user is True

    for schema in OPENAI_TOOLS:
        if not schema["function"]["name"].startswith("board_"):
            continue
        properties = schema["function"]["parameters"].get("properties", {})
        assert not {"actor", "session_id", "run_id", "allowed_source_ids"} & set(properties)


def test_board_upsert_injects_execution_identity_and_query_reads_it_back(tmp_path):
    index = SourcingIndex(tmp_path)

    ok, created = execute(
        "board_upsert",
        {
            "record_type": "company",
            "fields": {"name": "Analytic Engines"},
            "idempotency_key": "company:analytic-engines",
            "rationale_summary": "Create the target company from sourced evidence.",
            "source_refs": ["source:web-1"],
        },
        sourcing_index=index,
        actor="assistant",
        session_id="session-tools",
        run_id="run-tools",
    )

    assert ok is True
    assert created["record"]["type"] == "company"
    receipt = index.receipts(created["record"]["id"])[0]
    assert receipt["actor"] == "assistant"
    assert receipt["session_id"] == "session-tools"
    assert receipt["run_id"] == "run-tools"

    ok, queried = execute(
        "board_query",
        {"record_type": "company", "filters": {"name": "Analytic Engines"}},
        sourcing_index=index,
    )
    assert ok is True
    assert [record["id"] for record in queried["records"]] == [created["record"]["id"]]


def test_board_mutate_routes_patch_and_delete_preserves_audit(tmp_path):
    index = SourcingIndex(tmp_path)
    created = index.upsert(
        record_type="action",
        fields={"title": "Review target", "status": "open"},
        idempotency_key="action:tool-mutate",
        actor="assistant",
        rationale_summary="Create the review action.",
    )

    ok, patched = execute(
        "board_mutate",
        {
            "action": "patch",
            "record_id": created["id"],
            "expected_version": 1,
            "fields": {"owner": "fisher"},
            "rationale_summary": "Assign the review action.",
        },
        sourcing_index=index,
        actor="assistant",
        session_id="session-mutate",
        run_id="run-mutate",
    )
    assert ok is True
    assert patched["record"]["fields"]["owner"] == "fisher"

    ok, deleted = execute(
        "board_delete",
        {
            "record_id": created["id"],
            "expected_version": 2,
            "rationale_summary": "Delete after the approval layer allowed it.",
        },
        sourcing_index=index,
        actor="director",
        session_id="session-delete",
        run_id="run-delete",
    )
    assert ok is True
    assert deleted["deleted"] is True
    assert index.receipts(created["id"])[-1]["operation"] == "delete"


def test_chat_board_write_injects_durable_session_and_run_identity(tmp_path):
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="board-create-1",
                        name="board_upsert",
                        arguments={
                            "record_type": "contact",
                            "fields": {"name": "Ada"},
                            "idempotency_key": "contact:chat-ada",
                            "rationale_summary": "Create the sourced contact.",
                            "source_refs": ["source:web-ada"],
                        },
                    )
                ]
            },
            {"deltas": ("Ada is now on the Board.",)},
        ]
    )
    app = create_app(token=TOKEN, provider=provider, state=tmp_path)
    session_id = app.state.store.open_session_id()

    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "Add Ada", "session_id": session_id})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] in {"turn_end", "error"}:
                break

    finished = next(event for event in events if event["type"] == "tool_finished")
    assert finished["ok"] is True
    record_id = finished["result"]["record"]["id"]
    receipt = app.state.sourcing_index.receipts(record_id)[0]
    assert receipt["actor"] == "assistant"
    assert receipt["session_id"] == session_id
    assert receipt["run_id"] == finished["run_id"]


def test_approved_board_delete_receipt_uses_the_human_actor(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    created = app.state.sourcing_index.upsert(
        record_type="action",
        fields={"title": "Duplicate"},
        idempotency_key="action:approved-delete",
        actor="assistant",
        rationale_summary="Create the duplicate fixture.",
    )
    item = app.state.inbox.park(
        "board_delete",
        {
            "record_id": created["id"],
            "expected_version": 1,
            "rationale_summary": "Delete the duplicate after review.",
        },
        item_id="board-delete-approved",
        session_id="session-delete-approved",
        run_id="run-delete-approved",
    )

    response = TestClient(app).post(
        f"/v1/inbox/{item['id']}",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "Fisher", "scope": "once"},
    )

    assert response.status_code == 200
    receipt = app.state.sourcing_index.receipts(created["id"])[-1]
    assert receipt["operation"] == "delete"
    assert receipt["actor"] == "Fisher"
    assert receipt["session_id"] == "session-delete-approved"
    assert receipt["run_id"] == "run-delete-approved"
