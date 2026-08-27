import asyncio
import json

import pytest

from coworker.events import TurnIdentity
from coworker.inbox import Inbox
from coworker.permissions import decide
from coworker.provider import ModelUsage, StreamChunk, ToolCall
from coworker.store import ConversationStore
from coworker.telemetry import InMemoryTelemetryAdapter, TelemetryRecorder
from coworker.telemetry.schema import (
    AgentTurnSpan,
    ErrorKind,
    ProviderSpan,
    SpanEventRecord,
    SpanSettledRecord,
    SpanStartedRecord,
    SpanStatus,
    StopReason,
    ToolSpan,
    UsageEvent,
    record_to_dict,
)
from coworker.turn import RunControl, _telemetry_provider_name, run_turn


class ContractProvider:
    model_id = "contract-model"

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self.chunks = chunks

    async def astream(self, *, messages, tools):
        for chunk in self.chunks:
            yield chunk


class StepProvider:
    model_id = "step-model"

    def __init__(self, steps: list[list[StreamChunk]]) -> None:
        self.steps = steps
        self.index = 0

    async def astream(self, *, messages, tools):
        chunks = self.steps[self.index]
        self.index += 1
        for chunk in chunks:
            yield chunk


def test_openai_compatible_provider_identity_uses_the_configured_vendor_host():
    MoonshotAdapter = type(
        "OpenAICompatProvider",
        (),
        {"base_url": "https://api.moonshot.ai/v1"},
    )
    OpenAIAdapter = type(
        "OpenAICompatProvider",
        (),
        {"base_url": "https://api.openai.com/v1"},
    )

    assert _telemetry_provider_name(MoonshotAdapter()) == "moonshot"
    assert _telemetry_provider_name(OpenAIAdapter()) == "openai"


def run_with_telemetry(tmp_path, provider, *, control=None, text="find private candidates"):
    store = ConversationStore(tmp_path)
    store.create_session("session-1")
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter)
    result = asyncio.run(
        run_turn(
            text=text,
            sid="session-1",
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=TurnIdentity(
                session_id="session-1",
                run_id="run-1",
                message_id="message-1",
                part_id="part-1",
            ),
            telemetry=recorder,
            control=control,
        )
    )
    return result, adapter


def test_run_turn_records_provider_usage_under_the_authoritative_agent_span(tmp_path):
    usage = ModelUsage(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        cached_input_tokens=20,
        uncached_input_tokens=100,
        reasoning_tokens=8,
    )
    result, adapter = run_with_telemetry(
        tmp_path,
        ContractProvider(
            [
                StreamChunk(text_delta="done"),
                StreamChunk(usage=usage),
                StreamChunk(finish_reason="stop"),
            ]
        ),
    )

    assert result == {"status": "ok", "text": "done"}
    starts = [
        record for record in adapter.records if isinstance(record, SpanStartedRecord)
    ]
    assert [type(record.span) for record in starts] == [AgentTurnSpan, ProviderSpan]
    assert starts[0].context.session_id == "session-1"
    assert starts[0].context.run_id == "run-1"
    assert starts[1].parent_span_id == starts[0].span_id
    assert starts[1].span.provider == "contract"
    assert starts[1].span.model == "contract-model"
    assert starts[1].span.operation == "provider.request"

    usage_records = [
        record
        for record in adapter.records
        if isinstance(record, SpanEventRecord)
        and isinstance(record.event, UsageEvent)
    ]
    assert [record.event for record in usage_records] == [
        UsageEvent(
            input_tokens=120,
            output_tokens=30,
            total_tokens=150,
            cache_hit_input_tokens=20,
            cache_miss_input_tokens=100,
            reasoning_tokens=8,
            current_context_tokens=120,
        )
    ]
    terminals = [
        record
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
    ]
    assert terminals[0].terminal.status is SpanStatus.SUCCESS
    assert terminals[0].terminal.stop_reason is StopReason.COMPLETED
    assert terminals[0].terminal.usage == usage_records[0].event
    assert terminals[1].terminal.status is SpanStatus.SUCCESS


def test_live_telemetry_never_captures_prompt_response_or_transient_reasoning(tmp_path):
    planted = "sk-live-PLANTED-DO-NOT-CAPTURE"
    result, adapter = run_with_telemetry(
        tmp_path,
        ContractProvider(
            [
                StreamChunk(transient_reasoning_delta=f"reasoning {planted}"),
                StreamChunk(text_delta=f"response {planted}"),
                StreamChunk(finish_reason="stop"),
            ]
        ),
        text=f"prompt {planted}",
    )

    assert result["status"] == "ok"
    serialized = json.dumps([record_to_dict(record) for record in adapter.records])
    assert planted not in serialized
    assert "prompt" not in serialized
    assert "response" not in serialized
    assert "reasoning " not in serialized


def test_run_turn_records_successful_tool_execution_as_an_agent_child(tmp_path):
    provider = StepProvider(
        [
            [
                StreamChunk(
                    tool_calls=[ToolCall(id="call-1", name="now", arguments={})]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ],
            [StreamChunk(text_delta="done"), StreamChunk(finish_reason="stop")],
        ]
    )

    result, adapter = run_with_telemetry(tmp_path, provider)

    assert result["status"] == "ok"
    starts = [
        record for record in adapter.records if isinstance(record, SpanStartedRecord)
    ]
    agent = next(record for record in starts if isinstance(record.span, AgentTurnSpan))
    tool = next(record for record in starts if isinstance(record.span, ToolSpan))
    assert tool.parent_span_id == agent.span_id
    assert tool.span.tool_name == "now"
    assert tool.span.operation == "tool.execute"
    terminal = next(
        record
        for record in adapter.records
        if isinstance(record, SpanSettledRecord) and record.span_id == tool.span_id
    )
    assert terminal.terminal.status is SpanStatus.SUCCESS


def test_run_turn_marks_failed_tool_and_agent_spans_partial(tmp_path):
    provider = StepProvider(
        [
            [
                StreamChunk(
                    tool_calls=[
                        ToolCall(
                            id="call-1",
                            name="drive_read",
                            arguments={"file_id": "private-file"},
                        )
                    ]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ],
            [StreamChunk(text_delta="partial"), StreamChunk(finish_reason="stop")],
        ]
    )

    result, adapter = run_with_telemetry(tmp_path, provider)

    assert result["status"] == "partial"
    starts = [
        record for record in adapter.records if isinstance(record, SpanStartedRecord)
    ]
    tool = next(record for record in starts if isinstance(record.span, ToolSpan))
    agent = next(record for record in starts if isinstance(record.span, AgentTurnSpan))
    terminals = {
        record.span_id: record.terminal
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
    }
    assert terminals[tool.span_id].status is SpanStatus.PARTIAL
    assert terminals[tool.span_id].error_kind is ErrorKind.TOOL
    assert terminals[agent.span_id].status is SpanStatus.PARTIAL


def test_run_turn_settles_provider_and_agent_spans_on_provider_failure(tmp_path):
    planted = "raw provider error sk-live-PLANTED-DO-NOT-CAPTURE"

    class FailingProvider:
        model_id = "failing-model"

        async def astream(self, *, messages, tools):
            if False:
                yield StreamChunk()
            raise RuntimeError(planted)

    result, adapter = run_with_telemetry(tmp_path, FailingProvider())

    assert result["status"] == "error"
    terminals = [
        record.terminal
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
    ]
    assert [terminal.status for terminal in terminals] == [
        SpanStatus.FAILED,
        SpanStatus.FAILED,
    ]
    assert [terminal.error_kind for terminal in terminals] == [
        ErrorKind.PROVIDER,
        ErrorKind.PROVIDER,
    ]
    assert planted not in json.dumps(
        [record_to_dict(record) for record in adapter.records]
    )


def test_run_turn_settles_provider_and_agent_spans_when_cancelled(tmp_path):
    identity = TurnIdentity(
        session_id="session-1",
        run_id="run-1",
        message_id="message-1",
        part_id="part-1",
    )
    control = RunControl(identity)
    control.cancel_requested.set()
    result, adapter = run_with_telemetry(
        tmp_path,
        ContractProvider([StreamChunk(text_delta="must not finish")]),
        control=control,
    )

    assert result["status"] == "stopped"
    terminals = [
        record.terminal
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
    ]
    assert [terminal.status for terminal in terminals] == [
        SpanStatus.CANCELLED,
        SpanStatus.CANCELLED,
    ]


def test_run_turn_preserves_missing_provider_usage_as_unknown(tmp_path):
    result, adapter = run_with_telemetry(
        tmp_path,
        ContractProvider(
            [StreamChunk(text_delta="done"), StreamChunk(finish_reason="stop")]
        ),
    )

    assert result["status"] == "ok"
    provider_terminal = next(
        record.terminal
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
        and record.terminal.stop_reason is StopReason.COMPLETED
        and record.terminal.usage is None
    )
    assert provider_terminal.usage is None


def test_run_turn_settles_the_agent_span_when_no_provider_is_configured(tmp_path):
    result, adapter = run_with_telemetry(tmp_path, None)

    assert result["status"] == "error"
    terminal = next(
        record.terminal
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
    )
    assert terminal.status is SpanStatus.FAILED
    assert terminal.error_kind is ErrorKind.PROVIDER


def test_unsafe_provider_model_identifier_cannot_change_the_turn_outcome(tmp_path):
    provider = ContractProvider(
        [StreamChunk(text_delta="done"), StreamChunk(finish_reason="stop")]
    )
    provider.model_id = "sk-live-PLANTED-DO-NOT-CAPTURE"

    result, adapter = run_with_telemetry(tmp_path, provider)

    assert result == {"status": "ok", "text": "done"}
    provider_start = next(
        record
        for record in adapter.records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, ProviderSpan)
    )
    assert provider_start.span.model == "unknown"
    assert "PLANTED" not in json.dumps(
        [record_to_dict(record) for record in adapter.records]
    )


def test_invalid_auto_allowed_tool_name_cannot_change_the_turn_outcome(tmp_path):
    name = "mcp__granola__list meetings"
    assert decide(name).allowed is True

    def make_provider():
        return StepProvider(
            [
                [
                    StreamChunk(
                        tool_calls=[
                            ToolCall(id="call-1", name=name, arguments={})
                        ]
                    ),
                    StreamChunk(finish_reason="tool_calls"),
                ],
                [
                    StreamChunk(text_delta="recovered"),
                    StreamChunk(finish_reason="stop"),
                ],
            ]
        )

    recorded, _adapter = run_with_telemetry(tmp_path / "recorded", make_provider())
    store = ConversationStore(tmp_path / "noop")
    store.create_session("session-1")
    noop = asyncio.run(
        run_turn(
            text="find private candidates",
            sid="session-1",
            store=store,
            provider=make_provider(),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            identity=TurnIdentity(
                session_id="session-1",
                run_id="run-1",
                message_id="message-1",
                part_id="part-1",
            ),
        )
    )

    assert recorded == {"status": "partial", "text": "recovered"}
    assert noop == {"status": "partial", "text": "recovered"}


def test_task_cancellation_settles_the_active_tool_and_agent_spans(
    tmp_path, monkeypatch
):
    def cancelled_execute(name, arguments, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("coworker.turn.execute", cancelled_execute)
    provider = StepProvider(
        [
            [
                StreamChunk(
                    tool_calls=[ToolCall(id="call-1", name="now", arguments={})]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ]
        ]
    )
    store = ConversationStore(tmp_path)
    store.create_session("session-1")
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_turn(
                text="cancel",
                sid="session-1",
                store=store,
                provider=provider,
                persona=None,
                skills=None,
                inbox=Inbox(store),
                openai_tools=[],
                execute_kwargs={},
                identity=TurnIdentity(
                    session_id="session-1",
                    run_id="run-1",
                    message_id="message-1",
                    part_id="part-1",
                ),
                telemetry=recorder,
            )
        )

    starts = {
        type(record.span): record.span_id
        for record in adapter.records
        if isinstance(record, SpanStartedRecord)
    }
    terminals = {
        record.span_id: record.terminal.status
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
    }
    assert terminals[starts[ToolSpan]] is SpanStatus.CANCELLED
    assert terminals[starts[AgentTurnSpan]] is SpanStatus.CANCELLED


def test_step_limit_settles_the_agent_span_instead_of_leaving_it_running(tmp_path):
    provider = StepProvider(
        [
            [
                StreamChunk(
                    tool_calls=[
                        ToolCall(id=f"call-{index}", name="now", arguments={})
                    ]
                ),
                StreamChunk(finish_reason="tool_calls"),
            ]
            for index in range(8)
        ]
    )

    result, adapter = run_with_telemetry(tmp_path, provider)

    assert result["status"] == "stopped"
    agent_start = next(
        record
        for record in adapter.records
        if isinstance(record, SpanStartedRecord)
        and isinstance(record.span, AgentTurnSpan)
    )
    agent_terminal = next(
        record.terminal
        for record in adapter.records
        if isinstance(record, SpanSettledRecord)
        and record.span_id == agent_start.span_id
    )
    assert agent_terminal.status is SpanStatus.CANCELLED
