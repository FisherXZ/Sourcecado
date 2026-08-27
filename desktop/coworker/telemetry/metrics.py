"""Read-only current-run measurements derived from telemetry records."""

from __future__ import annotations

import math
from dataclasses import dataclass

from coworker.telemetry.schema import (
    AgentTurnSpan,
    CostEvent,
    ProviderSpan,
    SpanEventRecord,
    SpanSettledRecord,
    SpanStartedRecord,
    TelemetryRecord,
    UsageEvent,
)


@dataclass(frozen=True, slots=True)
class CurrentRunMetrics:
    run_id: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cache_hit_input_tokens: int | None
    cache_miss_input_tokens: int | None
    reasoning_tokens: int | None
    current_context_tokens: int | None
    context_window_tokens: int | None
    context_use_ratio: float | None
    elapsed_ms: float | None
    estimated_cost_usd: float | None


def _sum_known(usages: list[UsageEvent], field_name: str) -> int | None:
    values = [getattr(usage, field_name) for usage in usages]
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def current_run_metrics(
    records: tuple[TelemetryRecord, ...],
    *,
    run_id: str,
    now_ns: int,
) -> CurrentRunMetrics:
    run_records = [record for record in records if record.context.run_id == run_id]
    starts = {
        record.span_id: record
        for record in run_records
        if isinstance(record, SpanStartedRecord)
    }
    provider_ids = {
        span_id
        for span_id, record in starts.items()
        if isinstance(record.span, ProviderSpan)
    }

    usages: dict[str, tuple[int, UsageEvent]] = {}
    costs: dict[str, tuple[int, float]] = {}
    settlements: dict[str, SpanSettledRecord] = {}
    for record in run_records:
        if isinstance(record, SpanEventRecord) and record.span_id in provider_ids:
            if isinstance(record.event, UsageEvent):
                usages[record.span_id] = (record.sequence, record.event)
            elif isinstance(record.event, CostEvent):
                costs[record.span_id] = (
                    record.sequence,
                    record.event.cost.estimated_cost_usd,
                )
        elif isinstance(record, SpanSettledRecord):
            settlements[record.span_id] = record
            if record.span_id in provider_ids:
                if record.terminal.usage is not None:
                    usages[record.span_id] = (
                        record.sequence,
                        record.terminal.usage,
                    )
                if record.terminal.cost is not None:
                    costs[record.span_id] = (
                        record.sequence,
                        record.terminal.cost.estimated_cost_usd,
                    )

    selected_usages = [entry[1] for entry in usages.values()]
    latest_usage = max(usages.values(), key=lambda entry: entry[0])[1] if usages else None

    agent_starts = [
        record
        for record in starts.values()
        if isinstance(record.span, AgentTurnSpan)
    ]
    elapsed_ms: float | None = None
    if agent_starts:
        root = min(agent_starts, key=lambda record: record.sequence)
        settlement = settlements.get(root.span_id)
        elapsed_ms = (
            settlement.terminal.latency_ms
            if settlement is not None
            else max(0, now_ns - root.observed_at_ns) / 1_000_000
        )

    current_context_tokens = (
        latest_usage.current_context_tokens if latest_usage is not None else None
    )
    context_window_tokens = (
        latest_usage.context_window_tokens if latest_usage is not None else None
    )
    context_use_ratio = (
        current_context_tokens / context_window_tokens
        if current_context_tokens is not None and context_window_tokens
        else None
    )
    estimated_cost_usd = (
        math.fsum(entry[1] for entry in costs.values()) if costs else None
    )
    return CurrentRunMetrics(
        run_id=run_id,
        input_tokens=_sum_known(selected_usages, "input_tokens"),
        output_tokens=_sum_known(selected_usages, "output_tokens"),
        total_tokens=_sum_known(selected_usages, "total_tokens"),
        cache_hit_input_tokens=_sum_known(selected_usages, "cache_hit_input_tokens"),
        cache_miss_input_tokens=_sum_known(selected_usages, "cache_miss_input_tokens"),
        reasoning_tokens=_sum_known(selected_usages, "reasoning_tokens"),
        current_context_tokens=current_context_tokens,
        context_window_tokens=context_window_tokens,
        context_use_ratio=context_use_ratio,
        elapsed_ms=elapsed_ms,
        estimated_cost_usd=estimated_cost_usd,
    )
