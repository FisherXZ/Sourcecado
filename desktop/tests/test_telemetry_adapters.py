from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from coworker.telemetry.adapters import (
    InMemoryTelemetryAdapter,
    NoOpTelemetryAdapter,
    TelemetryRecorder,
)
from coworker.telemetry.schema import (
    AgentTurnSpan,
    ErrorKind,
    RetryEvent,
    RetryReason,
    SpanSettledRecord,
    SpanStartedRecord,
    SpanStatus,
    StopReason,
    ToolSpan,
    TraceContext,
    UsageEvent,
)


class StepClock:
    def __init__(self, start: int = 0, step: int = 1_000_000) -> None:
        self.value = start
        self.step = step

    def __call__(self) -> int:
        current = self.value
        self.value += self.step
        return current


def test_noop_adapter_is_inert_and_does_not_even_read_the_clock():
    recorder = TelemetryRecorder(
        NoOpTelemetryAdapter(),
        clock=lambda: (_ for _ in ()).throw(AssertionError("clock was read")),
    )
    context = TraceContext(session_id="session-1", run_id="run-1")

    span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
    child = span.child(ToolSpan(tool_name="gmail_search", operation="tool.execute"))
    child.record(
        RetryEvent(
            operation="tool.execute",
            retry_count=1,
            reason=RetryReason.TIMEOUT,
        )
    )
    child.fail(ErrorKind.TIMEOUT)
    span.finish()

    assert span.span_id is None
    assert child.span_id is None


def test_in_memory_adapter_assigns_deterministic_ids_parentage_and_order():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock(start=10_000_000))
    context = TraceContext(session_id="session-1", run_id="run-1")

    turn = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
    tool = turn.child(ToolSpan(tool_name="gmail_search", operation="tool.execute"))
    tool.record(UsageEvent(input_tokens=5, output_tokens=2))
    tool.finish(stop_reason=StopReason.COMPLETED)
    turn.finish(usage=UsageEvent(input_tokens=5, output_tokens=2))

    records = adapter.records
    assert [record.sequence for record in records] == [1, 2, 3, 4, 5]
    assert [record.record_type for record in records] == [
        "span_started",
        "span_started",
        "span_event",
        "span_settled",
        "span_settled",
    ]
    assert turn.span_id == "span-000001"
    assert tool.span_id == "span-000002"
    assert isinstance(records[1], SpanStartedRecord)
    assert records[1].parent_span_id == turn.span_id
    assert isinstance(records[3], SpanSettledRecord)
    assert records[3].terminal.latency_ms == 2.0


def test_concurrent_children_keep_unique_ordered_records_and_shared_parentage():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    turn = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
    barrier = Barrier(5)

    def execute_tool(index: int) -> str | None:
        barrier.wait()
        span = turn.child(
            ToolSpan(tool_name=f"tool-{index}", operation="tool.execute")
        )
        span.finish()
        return span.span_id

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(execute_tool, index) for index in range(4)]
        barrier.wait()
        span_ids = [future.result() for future in futures]
    turn.finish()

    records = adapter.records
    starts = [record for record in records if isinstance(record, SpanStartedRecord)]
    assert len(set(span_ids)) == 4
    assert all(record.parent_span_id == turn.span_id for record in starts[1:])
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))
    assert len({record.sequence for record in records}) == len(records)


def test_adapter_failures_never_escape_or_change_the_business_result():
    class ExplodingAdapter:
        enabled = True

        def record(self, record):
            raise RuntimeError("adapter unavailable")

    recorder = TelemetryRecorder(ExplodingAdapter(), clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")

    def business_operation() -> str:
        span = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
        span.record(
            RetryEvent(
                operation="provider.request",
                retry_count=1,
                reason=RetryReason.CONNECTION,
            )
        )
        span.finish(status=SpanStatus.SUCCESS)
        return "business-result"

    assert business_operation() == "business-result"
