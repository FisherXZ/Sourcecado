from dataclasses import fields
from typing import get_type_hints

import pytest

from coworker.telemetry.schema import (
    SCHEMA_VERSION,
    AgentTurnSpan,
    CompactionEvent,
    CompactionReason,
    CostEstimate,
    CostEvent,
    ErrorKind,
    ProviderSpan,
    RetryEvent,
    RetryReason,
    SpanEventRecord,
    SpanSettledRecord,
    SpanStartedRecord,
    SpanStatus,
    StopReason,
    TerminalEvent,
    ToolSpan,
    TraceContext,
    UsageEvent,
    record_to_dict,
)


def test_telemetry_schemas_are_versioned_closed_and_serializable():
    context = TraceContext(session_id="session-1", run_id="run-1")
    provider = ProviderSpan(
        provider="anthropic",
        model="claude-sonnet-4",
        operation="chat.completion",
        context_window_tokens=200_000,
    )
    started = SpanStartedRecord(
        sequence=1,
        span_id="span-1",
        parent_span_id=None,
        context=context,
        observed_at_ns=10,
        span=provider,
    )
    usage = UsageEvent(
        input_tokens=120,
        output_tokens=30,
        total_tokens=150,
        cache_hit_input_tokens=20,
        cache_miss_input_tokens=100,
        cache_write_input_tokens=5,
        reasoning_tokens=8,
        current_context_tokens=150,
        context_window_tokens=200_000,
    )
    observed = SpanEventRecord(
        sequence=2,
        span_id="span-1",
        context=context,
        observed_at_ns=20,
        event=usage,
    )
    settled = SpanSettledRecord(
        sequence=3,
        span_id="span-1",
        context=context,
        observed_at_ns=30,
        terminal=TerminalEvent(
            status=SpanStatus.SUCCESS,
            latency_ms=0.00002,
            stop_reason=StopReason.COMPLETED,
            usage=usage,
        ),
    )

    assert record_to_dict(started) == {
        "version": SCHEMA_VERSION,
        "record_type": "span_started",
        "sequence": 1,
        "span_id": "span-1",
        "parent_span_id": None,
        "context": {"session_id": "session-1", "run_id": "run-1"},
        "observed_at_ns": 10,
        "span": {
            "span_type": "provider",
            "provider": "anthropic",
            "model": "claude-sonnet-4",
            "operation": "chat.completion",
            "context_window_tokens": 200_000,
        },
    }
    assert record_to_dict(observed)["event"] == {
        "event_type": "usage",
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "cache_hit_input_tokens": 20,
        "cache_miss_input_tokens": 100,
        "cache_write_input_tokens": 5,
        "reasoning_tokens": 8,
        "current_context_tokens": 150,
        "context_window_tokens": 200_000,
    }
    assert record_to_dict(settled)["terminal"]["status"] == "success"

    with pytest.raises(TypeError):
        ProviderSpan(
            provider="anthropic",
            model="claude-sonnet-4",
            operation="chat.completion",
            prompt="do not capture me",
        )


def test_all_supported_span_and_event_schemas_use_bounded_operational_fields():
    schemas = (
        AgentTurnSpan(operation="agent.turn"),
        ToolSpan(tool_name="gmail_search", operation="tool.execute"),
        RetryEvent(
            operation="provider.request",
            retry_count=1,
            reason=RetryReason.RATE_LIMIT,
            delay_ms=250,
        ),
        CompactionEvent(
            operation="agent.context",
            reason=CompactionReason.CONTEXT_LIMIT,
            input_tokens=80_000,
            output_tokens=20_000,
        ),
        TerminalEvent(
            status=SpanStatus.FAILED,
            latency_ms=12.5,
            stop_reason=StopReason.ERROR,
            error_kind=ErrorKind.TIMEOUT,
            retry_count=2,
        ),
    )

    assert [record_to_dict(schema) for schema in schemas] == [
        {"span_type": "agent_turn", "operation": "agent.turn"},
        {
            "span_type": "tool",
            "tool_name": "gmail_search",
            "operation": "tool.execute",
        },
        {
            "event_type": "retry",
            "operation": "provider.request",
            "retry_count": 1,
            "reason": "rate_limit",
            "delay_ms": 250,
        },
        {
            "event_type": "compaction",
            "operation": "agent.context",
            "reason": "context_limit",
            "input_tokens": 80_000,
            "output_tokens": 20_000,
        },
        {
            "event_type": "terminal",
            "status": "failed",
            "latency_ms": 12.5,
            "stop_reason": "error",
            "retry_count": 2,
            "usage": None,
            "cost": None,
            "error_kind": "timeout",
        },
    ]

    forbidden_fields = {
        "prompt",
        "messages",
        "response",
        "text",
        "body",
        "arguments",
        "result",
        "command_output",
        "credential",
        "authorization",
        "error",
        "reasoning",
        "metadata",
    }
    for schema in schemas:
        assert forbidden_fields.isdisjoint(field.name for field in fields(schema))


def test_compaction_event_allows_unknown_token_counts_without_estimation():
    event = CompactionEvent(
        operation="history.compact",
        reason=CompactionReason.POLICY,
        input_tokens=None,
        output_tokens=None,
    )

    hints = get_type_hints(CompactionEvent)
    assert hints["input_tokens"] == int | None
    assert hints["output_tokens"] == int | None
    assert record_to_dict(event) == {
        "event_type": "compaction",
        "operation": "history.compact",
        "reason": "policy",
        "input_tokens": None,
        "output_tokens": None,
    }


@pytest.mark.parametrize(
    ("constructor", "match"),
    [
        (
            lambda: TraceContext(session_id="contains a space", run_id="run-1"),
            "session_id",
        ),
        (
            lambda: RetryEvent(
                operation="provider.request",
                retry_count=-1,
                reason=RetryReason.TIMEOUT,
            ),
            "retry_count",
        ),
        (
            lambda: UsageEvent(input_tokens=1, output_tokens=-1),
            "output_tokens",
        ),
        (
            lambda: TerminalEvent(
                status=SpanStatus.FAILED,
                latency_ms=1,
            ),
            "error_kind",
        ),
    ],
)
def test_telemetry_schemas_reject_unbounded_or_invalid_values(constructor, match):
    with pytest.raises(ValueError, match=match):
        constructor()


def test_usage_accepts_partial_provider_reports_without_inventing_missing_counts():
    usage = UsageEvent(total_tokens=42, reasoning_tokens=7)

    assert record_to_dict(usage) == {
        "event_type": "usage",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": 42,
        "cache_hit_input_tokens": None,
        "cache_miss_input_tokens": None,
        "cache_write_input_tokens": None,
        "reasoning_tokens": 7,
        "current_context_tokens": None,
        "context_window_tokens": None,
    }


def test_estimated_cost_is_a_separate_closed_measurement_from_provider_usage():
    cost = CostEstimate(estimated_cost_usd=0.0042)

    assert record_to_dict(CostEvent(cost=cost)) == {
        "event_type": "cost",
        "cost": {"estimated_cost_usd": 0.0042},
    }
    assert record_to_dict(
        TerminalEvent(
            status=SpanStatus.SUCCESS,
            latency_ms=1,
            cost=cost,
        )
    )["cost"] == {"estimated_cost_usd": 0.0042}
    assert "estimated_cost_usd" not in {
        field.name for field in fields(UsageEvent(total_tokens=10))
    }

    with pytest.raises(TypeError):
        CostEstimate(estimated_cost_usd=0.0042, pricing_metadata={"secret": "no"})


def test_estimated_cost_rejects_missing_or_non_finite_values():
    for value in (None, float("nan"), float("inf"), -0.01):
        with pytest.raises(ValueError, match="estimated_cost_usd"):
            CostEstimate(estimated_cost_usd=value)
