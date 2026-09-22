import json
from dataclasses import dataclass

import pytest

from coworker.telemetry.adapters import InMemoryTelemetryAdapter, TelemetryRecorder
from coworker.telemetry.schema import (
    AgentTurnSpan,
    ErrorKind,
    ProviderSpan,
    SpanSettledRecord,
    SpanStatus,
    TerminalEvent,
    TraceContext,
    UsageEvent,
    record_to_dict,
)
from tests.test_telemetry_adapters import StepClock


PLANTED_SECRET = "sk-live-PLANTED-DO-NOT-CAPTURE"
FORBIDDEN_KEYS = (
    "prompt",
    "messages",
    "response_text",
    "source_body",
    "arguments",
    "result",
    "command_output",
    "credentials",
    "authorization",
    "raw_error",
    "reasoning",
)


@pytest.mark.parametrize("field_name", FORBIDDEN_KEYS)
def test_closed_provider_schema_rejects_every_content_or_secret_field(field_name):
    values = {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "operation": "chat.completion",
        field_name: PLANTED_SECRET,
    }

    with pytest.raises(TypeError):
        ProviderSpan(**values)


def test_recorder_ignores_runtime_payloads_outside_the_closed_schema():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
    initial_records = adapter.records

    assert span.record(
        {"arguments": {"authorization": PLANTED_SECRET}}
    ) is False
    invalid_span = recorder.start_span(
        {"prompt": PLANTED_SECRET},
        context,
    )

    assert invalid_span.span_id is None
    assert adapter.records == initial_records
    assert PLANTED_SECRET not in json.dumps(
        [record_to_dict(record) for record in adapter.records]
    )


def test_error_schema_accepts_only_bounded_error_kinds_not_raw_errors():
    with pytest.raises(ValueError, match="error_kind"):
        TerminalEvent(
            status=SpanStatus.FAILED,
            latency_ms=1,
            error_kind=PLANTED_SECRET,
        )

    terminal = TerminalEvent(
        status=SpanStatus.FAILED,
        latency_ms=1,
        error_kind=ErrorKind.PROVIDER,
    )
    assert PLANTED_SECRET not in json.dumps(record_to_dict(terminal))


def test_operational_identifiers_reject_common_secret_shapes():
    with pytest.raises(ValueError, match="tool_name"):
        from coworker.telemetry.schema import ToolSpan

        ToolSpan(tool_name=PLANTED_SECRET, operation="tool.execute")


def test_usage_rejects_content_instead_of_coercing_it_to_a_measurement():
    with pytest.raises(ValueError, match="input_tokens"):
        UsageEvent(input_tokens=PLANTED_SECRET)


def test_serializer_rejects_non_telemetry_dataclasses_even_when_they_look_typed():
    @dataclass(frozen=True)
    class ForbiddenPayload:
        prompt: str

    with pytest.raises(TypeError, match="unsupported telemetry value"):
        record_to_dict(ForbiddenPayload(prompt=PLANTED_SECRET))


def test_in_memory_adapter_rejects_untyped_records_without_storing_content():
    adapter = InMemoryTelemetryAdapter()

    adapter.record({"raw_error": PLANTED_SECRET})

    assert adapter.records == ()


def test_settled_record_rejects_an_untyped_terminal_payload():
    context = TraceContext(session_id="session-1", run_id="run-1")

    with pytest.raises(ValueError, match="terminal"):
        SpanSettledRecord(
            sequence=1,
            span_id="span-1",
            context=context,
            observed_at_ns=1,
            terminal={"raw_error": PLANTED_SECRET},
        )


def test_record_envelopes_reject_untyped_context_payloads():
    terminal = TerminalEvent(status=SpanStatus.SUCCESS, latency_ms=1)

    with pytest.raises(ValueError, match="context"):
        SpanSettledRecord(
            sequence=1,
            span_id="span-1",
            context={"session_id": PLANTED_SECRET, "run_id": "run-1"},
            observed_at_ns=1,
            terminal=terminal,
        )


def test_invalid_settlement_values_are_inert_and_non_throwing():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)

    assert span.finish(status="failed", error_kind=PLANTED_SECRET) is False
    assert span.settled is True
    assert len(adapter.records) == 1
