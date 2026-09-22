import pytest

from coworker.telemetry.adapters import InMemoryTelemetryAdapter, TelemetryRecorder
from coworker.telemetry.schema import (
    AgentTurnSpan,
    CompactionEvent,
    CompactionReason,
    ErrorKind,
    RetryEvent,
    RetryReason,
    SpanEventRecord,
    SpanSettledRecord,
    SpanStatus,
    StopReason,
    ToolSpan,
    TraceContext,
    UsageEvent,
)
from tests.test_telemetry_adapters import StepClock


@pytest.mark.parametrize(
    ("settle", "status", "stop_reason", "error_kind"),
    [
        (lambda span: span.finish(), SpanStatus.SUCCESS, StopReason.COMPLETED, None),
        (
            lambda span: span.fail(ErrorKind.PROVIDER, retry_count=2),
            SpanStatus.FAILED,
            StopReason.ERROR,
            ErrorKind.PROVIDER,
        ),
        (
            lambda span: span.cancel(),
            SpanStatus.CANCELLED,
            StopReason.CANCELLED,
            None,
        ),
        (
            lambda span: span.partial(ErrorKind.TOOL),
            SpanStatus.PARTIAL,
            StopReason.ERROR,
            ErrorKind.TOOL,
        ),
    ],
)
def test_adapter_conformance_records_each_bounded_terminal_outcome(
    settle, status, stop_reason, error_kind
):
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)

    assert settle(span) is True

    terminal = adapter.records[-1]
    assert isinstance(terminal, SpanSettledRecord)
    assert terminal.terminal.status is status
    assert terminal.terminal.stop_reason is stop_reason
    assert terminal.terminal.error_kind is error_kind


def test_adapter_conformance_makes_every_post_settlement_call_inert():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
    assert span.finish() is True
    settled_records = adapter.records

    assert span.settled is True
    assert span.record(
        RetryEvent(
            operation="provider.request",
            retry_count=1,
            reason=RetryReason.TIMEOUT,
        )
    ) is False
    assert span.finish() is False
    assert span.fail(ErrorKind.UNKNOWN) is False
    assert span.cancel() is False
    assert span.partial(ErrorKind.TOOL) is False
    child = span.child(ToolSpan(tool_name="gmail_search", operation="tool.execute"))
    assert child.span_id is None
    assert adapter.records == settled_records


def test_adapter_conformance_records_retry_compaction_and_missing_usage():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)

    span.record(
        RetryEvent(
            operation="provider.request",
            retry_count=1,
            reason=RetryReason.RATE_LIMIT,
            delay_ms=500,
        )
    )
    span.record(
        CompactionEvent(
            operation="agent.context",
            reason=CompactionReason.CONTEXT_LIMIT,
            input_tokens=100_000,
            output_tokens=30_000,
        )
    )
    span.finish(usage=None)

    events = [record.event for record in adapter.records if isinstance(record, SpanEventRecord)]
    assert [event.event_type for event in events] == ["retry", "compaction"]
    terminal = adapter.records[-1]
    assert isinstance(terminal, SpanSettledRecord)
    assert terminal.terminal.usage is None


def test_mismatched_parent_context_is_ignored_without_throwing_or_recording():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    parent = recorder.start_span(
        AgentTurnSpan(operation="agent.turn"),
        TraceContext(session_id="session-1", run_id="run-1"),
    )

    child = recorder.start_span(
        ToolSpan(tool_name="gmail_search", operation="tool.execute"),
        TraceContext(session_id="session-2", run_id="run-2"),
        parent=parent,
    )

    assert child.span_id is None
    assert len(adapter.records) == 1


def test_terminal_schema_rejects_untyped_status_values():
    with pytest.raises(ValueError, match="status"):
        from coworker.telemetry.schema import TerminalEvent

        TerminalEvent(status="failed", latency_ms=1, error_kind=ErrorKind.TIMEOUT)


def test_partial_tool_span_preserves_usage_without_accepting_error_content():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    span = recorder.start_span(
        ToolSpan(tool_name="gmail_search", operation="tool.execute"), context
    )
    usage = UsageEvent(input_tokens=10, output_tokens=0)

    span.partial(ErrorKind.TOOL, usage=usage)

    terminal = adapter.records[-1]
    assert isinstance(terminal, SpanSettledRecord)
    assert terminal.terminal.status is SpanStatus.PARTIAL
    assert terminal.terminal.usage == usage


def test_adapter_containment_does_not_swallow_base_exception_cancellation():
    class CancellationSignal(BaseException):
        pass

    class CancellingAdapter:
        enabled = True

        def record(self, record):
            raise CancellationSignal()

    recorder = TelemetryRecorder(CancellingAdapter(), clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")

    with pytest.raises(CancellationSignal):
        recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)


def test_clock_containment_does_not_swallow_base_exception_cancellation():
    class CancellationSignal(BaseException):
        pass

    recorder = TelemetryRecorder(
        InMemoryTelemetryAdapter(),
        clock=lambda: (_ for _ in ()).throw(CancellationSignal()),
    )
    context = TraceContext(session_id="session-1", run_id="run-1")

    with pytest.raises(CancellationSignal):
        recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
