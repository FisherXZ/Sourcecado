from coworker.telemetry.adapters import InMemoryTelemetryAdapter, TelemetryRecorder
from coworker.telemetry.metrics import CurrentRunMetrics
from coworker.telemetry.schema import (
    AgentTurnSpan,
    CostEstimate,
    CostEvent,
    ProviderSpan,
    TraceContext,
    UsageEvent,
)
from tests.test_telemetry_adapters import StepClock


def test_current_run_metrics_aggregate_provider_usage_cost_context_and_elapsed_time():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    turn = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)

    first = turn.child(
        ProviderSpan(
            provider="deepseek",
            model="deepseek-chat",
            operation="chat.completion",
            context_window_tokens=100,
        )
    )
    first_usage = UsageEvent(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cache_hit_input_tokens=4,
        cache_miss_input_tokens=6,
        reasoning_tokens=2,
        current_context_tokens=15,
        context_window_tokens=100,
    )
    first_cost = CostEstimate(estimated_cost_usd=0.0042)
    first.record(first_usage)
    first.record(CostEvent(cost=first_cost))
    first.finish(usage=first_usage, cost=first_cost)

    second = turn.child(
        ProviderSpan(
            provider="deepseek",
            model="deepseek-reasoner",
            operation="chat.completion",
            context_window_tokens=100,
        )
    )
    second_usage = UsageEvent(
        input_tokens=3,
        output_tokens=2,
        total_tokens=5,
        reasoning_tokens=1,
        current_context_tokens=8,
        context_window_tokens=100,
    )
    second_cost = CostEstimate(estimated_cost_usd=0.002)
    second.finish(usage=second_usage, cost=second_cost)

    metrics = adapter.current_run_metrics(run_id="run-1", now_ns=8_000_000)

    assert metrics == CurrentRunMetrics(
        run_id="run-1",
        input_tokens=13,
        output_tokens=7,
        total_tokens=20,
        cache_hit_input_tokens=4,
        cache_miss_input_tokens=6,
        reasoning_tokens=3,
        current_context_tokens=8,
        context_window_tokens=100,
        context_use_ratio=0.08,
        elapsed_ms=8.0,
        estimated_cost_usd=0.0062,
    )


def test_current_run_metrics_preserve_missing_usage_as_unknown():
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter, clock=StepClock())
    context = TraceContext(session_id="session-1", run_id="run-1")
    turn = recorder.start_span(AgentTurnSpan(operation="agent.turn"), context)
    provider = turn.child(
        ProviderSpan(
            provider="deepseek",
            model="deepseek-chat",
            operation="chat.completion",
        )
    )
    provider.finish(usage=None)
    turn.finish(usage=None)

    metrics = adapter.current_run_metrics(run_id="run-1", now_ns=10_000_000)

    assert metrics.input_tokens is None
    assert metrics.output_tokens is None
    assert metrics.total_tokens is None
    assert metrics.current_context_tokens is None
    assert metrics.context_use_ratio is None
    assert metrics.estimated_cost_usd is None
    assert metrics.elapsed_ms == 3.0
