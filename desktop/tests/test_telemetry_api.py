import json
import time

from fastapi.testclient import TestClient

from coworker.provider import (
    FakeProvider,
    ModelUsage,
    ProviderTerminal,
    StreamChunk,
    ToolCall,
)
from coworker.server import TOKEN_HEADER, create_app
from coworker.telemetry import InMemoryTelemetryAdapter, TelemetryRecorder
from coworker.telemetry.schema import (
    AgentTurnSpan,
    RetryEvent,
    SpanEventRecord,
    SpanSettledRecord,
    SpanStartedRecord,
    SpanStatus,
    ToolSpan,
)

TOKEN = "test-token-telemetry"


class UsageProvider:
    provider_id = "deepseek"
    model_id = "deepseek-v4-pro"

    async def astream(self, *, messages, tools):
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        yield StreamChunk(text_delta="safe response")
        usage = ModelUsage(
            input_tokens=80,
            output_tokens=20,
            total_tokens=100,
            cached_input_tokens=30,
            uncached_input_tokens=50,
            reasoning_tokens=4,
            cache_write_input_tokens=5,
        )
        yield StreamChunk(usage=usage)
        yield StreamChunk(
            finish_reason="stop",
            terminal=ProviderTerminal(
                stop_reason="stop",
                usage=usage,
                latency_ms=12.5,
                estimated_cost_usd=0.00003925875,
            ),
        )


def test_create_app_owns_one_in_memory_telemetry_recorder(tmp_path):
    app = create_app(token=TOKEN, provider=UsageProvider(), state=tmp_path)

    assert isinstance(app.state.telemetry_adapter, InMemoryTelemetryAdapter)
    assert isinstance(app.state.telemetry_recorder, TelemetryRecorder)


def test_websocket_turn_uses_the_composition_root_recorder(tmp_path):
    app = create_app(token=TOKEN, provider=UsageProvider(), state=tmp_path)
    sid = app.state.store.open_session_id()

    with TestClient(app).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "measure", "session_id": sid})
        events = []
        while not events or events[-1]["type"] not in {"turn_end", "error"}:
            events.append(ws.receive_json())

    started = next(
        record
        for record in app.state.telemetry_adapter.records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, AgentTurnSpan)
    )
    assert started.context.session_id == sid
    assert started.context.run_id == events[0]["run_id"]


def test_current_run_api_returns_only_allowlisted_measurements(tmp_path):
    app = create_app(token=TOKEN, provider=UsageProvider(), state=tmp_path)
    client = TestClient(app)
    sid = app.state.store.open_session_id()
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json(
            {
                "type": "chat",
                "text": "private prompt sk-live-PLANTED",
                "session_id": sid,
            }
        )
        events = []
        while not events or events[-1]["type"] not in {"turn_end", "error"}:
            events.append(ws.receive_json())

    response = client.get(
        f"/v1/sessions/{sid}/telemetry/current",
        headers={TOKEN_HEADER: TOKEN},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == 1
    assert body["session_id"] == sid
    assert body["current_run"] == {
        "run_id": events[0]["run_id"],
        "status": "success",
        "input_tokens": 80,
        "output_tokens": 20,
        "total_tokens": 100,
        "cache_hit_input_tokens": 30,
        "cache_miss_input_tokens": 50,
        "cache_write_input_tokens": 5,
        "reasoning_tokens": 4,
        "current_context_tokens": 80,
        "context_window_tokens": 1_000_000,
        "context_use_ratio": 0.00008,
        "elapsed_ms": body["current_run"]["elapsed_ms"],
        "estimated_cost_usd": 0.00003925875,
        "retry_count": 0,
        "compaction_count": 0,
    }
    assert isinstance(body["current_run"]["elapsed_ms"], (int, float))
    encoded = json.dumps(body)
    assert "PLANTED" not in encoded
    assert "prompt" not in encoded
    assert "response" not in encoded
    assert "arguments" not in encoded
    assert "result" not in encoded
    assert "raw_error" not in encoded


def test_manual_safe_retry_records_one_typed_retry_and_nested_tool_span(
    tmp_path, monkeypatch
):
    provider = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="call-drive",
                        name="drive_search",
                        arguments={"query": "Codeology"},
                    )
                ]
            },
            {"deltas": ("partial",)},
        ]
    )
    attempts = 0

    def flaky_execute(name, arguments, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False, {"error": "Drive timed out"}
        return True, {"files": []}

    monkeypatch.setattr("coworker.turn.execute", flaky_execute)
    app = create_app(token=TOKEN, provider=provider, state=tmp_path)
    sid = app.state.store.open_session_id()
    with TestClient(app).websocket_connect(
        "/ws/chat", subprotocols=["club", TOKEN]
    ) as ws:
        ws.send_json({"type": "chat", "text": "search", "session_id": sid})
        events = []
        while not events or events[-1]["type"] not in {"turn_end", "error"}:
            events.append(ws.receive_json())
        ws.send_json(
            {
                "type": "retry_failed_step",
                "session_id": sid,
                "run_id": events[0]["run_id"],
                "call_id": "call-drive",
                "command_id": "retry-1",
            }
        )
        time.sleep(0.08)

    records = app.state.telemetry_adapter.records
    retries = [
        record.event
        for record in records
        if isinstance(record, SpanEventRecord)
        and isinstance(record.event, RetryEvent)
    ]
    assert len(retries) == 1
    assert retries[0].retry_count == 1
    agents = [
        record
        for record in records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, AgentTurnSpan)
    ]
    assert [record.span.operation for record in agents] == [
        "agent.turn",
        "agent.recovery",
    ]
    tools = [
        record
        for record in records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, ToolSpan)
    ]
    assert len(tools) == 2
    assert tools[-1].parent_span_id == agents[-1].span_id
    retry_terminal = next(
        record.terminal
        for record in records
        if isinstance(record, SpanSettledRecord)
        and record.span_id == tools[-1].span_id
    )
    assert retry_terminal.status is SpanStatus.SUCCESS
    assert retry_terminal.retry_count == 1
    metrics = app.state.telemetry_adapter.current_session_metrics(
        session_id=sid,
        now_ns=time.monotonic_ns(),
    )
    assert metrics.retry_count == 1
    assert metrics.compaction_count == 0


def test_http_claimed_tool_execution_has_session_run_parentage(tmp_path):
    app = create_app(token=TOKEN, provider=UsageProvider(), state=tmp_path)
    sid = app.state.store.open_session_id()
    app.state.inbox.park(
        "now",
        {},
        item_id="approval-now",
        reason="approval test",
        session_id=sid,
        run_id="run-approved",
        message_id="message-approved",
        part_id="part-approved",
    )

    response = TestClient(app).post(
        "/v1/inbox/approval-now",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "operator", "scope": "once"},
    )

    assert response.status_code == 200
    records = app.state.telemetry_adapter.records
    agent = next(
        record
        for record in records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, AgentTurnSpan)
    )
    tool = next(
        record
        for record in records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, ToolSpan)
    )
    assert agent.span.operation == "agent.approval"
    assert agent.context.session_id == sid
    assert agent.context.run_id == "run-approved"
    assert tool.parent_span_id == agent.span_id
    assert tool.span.tool_name == "now"
    terminals = {
        record.span_id: record.terminal.status
        for record in records
        if isinstance(record, SpanSettledRecord)
    }
    assert terminals[agent.span_id] is SpanStatus.SUCCESS
    assert terminals[tool.span_id] is SpanStatus.SUCCESS


def test_approved_recovery_records_retry_only_when_the_tool_executes(tmp_path):
    app = create_app(token=TOKEN, provider=UsageProvider(), state=tmp_path)
    sid = app.state.store.open_session_id()
    app.state.inbox.park(
        "now",
        {},
        item_id="recovery-now",
        reason="retry approval test",
        session_id=sid,
        run_id="run-recovery",
        message_id="message-recovery",
        part_id="part-recovery",
        kind="recovery_approval",
        recovery_command_id="recovery-command",
        original_call_id="original-call",
    )

    response = TestClient(app).post(
        "/v1/inbox/recovery-now",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "operator", "scope": "once"},
    )

    assert response.status_code == 200
    records = app.state.telemetry_adapter.records
    retry = next(
        record.event
        for record in records
        if isinstance(record, SpanEventRecord)
        and isinstance(record.event, RetryEvent)
    )
    assert retry.operation == "tool.execute"
    assert retry.retry_count == 1
    agent = next(
        record
        for record in records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, AgentTurnSpan)
    )
    tool = next(
        record
        for record in records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, ToolSpan)
    )
    assert agent.span.operation == "agent.recovery"
    assert tool.parent_span_id == agent.span_id


def test_later_inbox_allow_does_not_replace_the_current_chat_run(tmp_path):
    app = create_app(token=TOKEN, provider=UsageProvider(), state=tmp_path)
    client = TestClient(app)
    sid = app.state.store.open_session_id()

    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        def chat(text: str) -> str:
            ws.send_json({"type": "chat", "text": text, "session_id": sid})
            events = []
            while not events or events[-1]["type"] not in {"turn_end", "error"}:
                events.append(ws.receive_json())
            start = next(event for event in events if event["type"] == "turn_start")
            return start["run_id"]

        first_run_id = chat("first")
        second_run_id = chat("second")
    app.state.inbox.park(
        "now",
        {},
        item_id="leftover-allow",
        reason="leftover approval from the first run",
        session_id=sid,
        run_id=first_run_id,
        message_id="message-first",
        part_id="part-first",
    )

    allow = client.post(
        "/v1/inbox/leftover-allow",
        headers={TOKEN_HEADER: TOKEN},
        json={"decision": "allow", "actor": "operator", "scope": "once"},
    )
    response = client.get(
        f"/v1/sessions/{sid}/telemetry/current",
        headers={TOKEN_HEADER: TOKEN},
    )

    assert first_run_id != second_run_id
    assert allow.status_code == 200
    assert response.status_code == 200
    assert response.json()["current_run"]["run_id"] == second_run_id
