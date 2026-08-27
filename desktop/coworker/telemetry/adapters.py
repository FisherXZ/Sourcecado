"""Passive telemetry recorder and local adapters."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock, RLock
from typing import Protocol

from coworker.telemetry.schema import (
    CostEstimate,
    ErrorKind,
    SpanEventRecord,
    SpanSchema,
    SpanSettledRecord,
    SpanStartedRecord,
    SpanStatus,
    SPAN_SCHEMA_TYPES,
    StopReason,
    TelemetryEvent,
    TELEMETRY_EVENT_TYPES,
    TelemetryRecord,
    TELEMETRY_RECORD_TYPES,
    TerminalEvent,
    TraceContext,
    UsageEvent,
)


class TelemetryAdapter(Protocol):
    enabled: bool

    def record(self, record: TelemetryRecord) -> None: ...


class NoOpTelemetryAdapter:
    """Disabled sink; the recorder takes a zero-work fast path for this adapter."""

    enabled = False

    def record(self, record: TelemetryRecord) -> None:
        return None


class InMemoryTelemetryAdapter:
    """Thread-safe ordered record sink for tests and local diagnostics."""

    enabled = True

    def __init__(self) -> None:
        self._records: list[TelemetryRecord] = []
        self._lock = Lock()

    def record(self, record: TelemetryRecord) -> None:
        if type(record) not in TELEMETRY_RECORD_TYPES:
            return None
        with self._lock:
            self._records.append(record)

    @property
    def records(self) -> tuple[TelemetryRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def current_run_metrics(self, *, run_id: str, now_ns: int):
        from coworker.telemetry.metrics import current_run_metrics

        return current_run_metrics(self.records, run_id=run_id, now_ns=now_ns)


class TelemetryRecorder:
    """Creates deterministic spans while containing all adapter failures."""

    def __init__(
        self,
        adapter: TelemetryAdapter | None = None,
        *,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._adapter = adapter or NoOpTelemetryAdapter()
        self._clock = clock
        self._lock = RLock()
        self._next_span = 1
        self._next_record = 1
        self._last_observed_at_ns = 0

    @property
    def enabled(self) -> bool:
        return bool(self._adapter.enabled)

    def start_span(
        self,
        span: SpanSchema,
        context: TraceContext,
        *,
        parent: SpanHandle | None = None,
    ) -> SpanHandle | InertSpanHandle:
        if not self.enabled:
            return InertSpanHandle()
        if type(span) not in SPAN_SCHEMA_TYPES:
            return InertSpanHandle()
        if parent is not None and parent.context != context:
            return InertSpanHandle()
        with self._lock:
            span_id = f"span-{self._next_span:06d}"
            self._next_span += 1
            observed_at_ns = self._now()
            record = SpanStartedRecord(
                sequence=self._sequence(),
                span_id=span_id,
                parent_span_id=parent.span_id if parent is not None else None,
                context=context,
                observed_at_ns=observed_at_ns,
                span=span,
            )
            self._safe_record(record)
        return SpanHandle(
            recorder=self,
            span_id=span_id,
            context=context,
            started_at_ns=observed_at_ns,
        )

    def _sequence(self) -> int:
        sequence = self._next_record
        self._next_record += 1
        return sequence

    def _now(self) -> int:
        try:
            observed_at_ns = self._clock()
        except Exception:
            observed_at_ns = self._last_observed_at_ns
        self._last_observed_at_ns = max(self._last_observed_at_ns, observed_at_ns)
        return self._last_observed_at_ns

    def _safe_record(self, record: TelemetryRecord) -> None:
        try:
            self._adapter.record(record)
        except Exception:
            return None

    def _record_event(
        self,
        *,
        span_id: str,
        context: TraceContext,
        event: TelemetryEvent,
    ) -> None:
        with self._lock:
            self._safe_record(
                SpanEventRecord(
                    sequence=self._sequence(),
                    span_id=span_id,
                    context=context,
                    observed_at_ns=self._now(),
                    event=event,
                )
            )

    def _settle(
        self,
        *,
        span_id: str,
        context: TraceContext,
        started_at_ns: int,
        status: SpanStatus,
        stop_reason: StopReason | None,
        retry_count: int,
        usage: UsageEvent | None,
        cost: CostEstimate | None,
        error_kind: ErrorKind | None,
    ) -> None:
        with self._lock:
            observed_at_ns = self._now()
            terminal = TerminalEvent(
                status=status,
                latency_ms=max(0, observed_at_ns - started_at_ns) / 1_000_000,
                stop_reason=stop_reason,
                retry_count=retry_count,
                usage=usage,
                cost=cost,
                error_kind=error_kind,
            )
            self._safe_record(
                SpanSettledRecord(
                    sequence=self._sequence(),
                    span_id=span_id,
                    context=context,
                    observed_at_ns=observed_at_ns,
                    terminal=terminal,
                )
            )


class SpanHandle:
    """A single-settlement handle; all calls after settlement are inert."""

    def __init__(
        self,
        *,
        recorder: TelemetryRecorder,
        span_id: str,
        context: TraceContext,
        started_at_ns: int,
    ) -> None:
        self._recorder = recorder
        self.span_id = span_id
        self.context = context
        self._started_at_ns = started_at_ns
        self._settled = False
        self._lock = Lock()

    @property
    def settled(self) -> bool:
        with self._lock:
            return self._settled

    def child(self, span: SpanSchema) -> SpanHandle | InertSpanHandle:
        with self._lock:
            if self._settled:
                return InertSpanHandle()
            return self._recorder.start_span(span, self.context, parent=self)

    def record(self, event: TelemetryEvent) -> bool:
        if type(event) not in TELEMETRY_EVENT_TYPES:
            return False
        with self._lock:
            if self._settled:
                return False
            self._recorder._record_event(
                span_id=self.span_id,
                context=self.context,
                event=event,
            )
            return True

    def finish(
        self,
        *,
        status: SpanStatus = SpanStatus.SUCCESS,
        stop_reason: StopReason | None = StopReason.COMPLETED,
        retry_count: int = 0,
        usage: UsageEvent | None = None,
        cost: CostEstimate | None = None,
        error_kind: ErrorKind | None = None,
    ) -> bool:
        with self._lock:
            if self._settled:
                return False
            self._settled = True
            try:
                self._recorder._settle(
                    span_id=self.span_id,
                    context=self.context,
                    started_at_ns=self._started_at_ns,
                    status=status,
                    stop_reason=stop_reason,
                    retry_count=retry_count,
                    usage=usage,
                    cost=cost,
                    error_kind=error_kind,
                )
            except Exception:
                return False
            return True

    def fail(
        self,
        error_kind: ErrorKind,
        *,
        retry_count: int = 0,
        usage: UsageEvent | None = None,
        cost: CostEstimate | None = None,
    ) -> bool:
        return self.finish(
            status=SpanStatus.FAILED,
            stop_reason=StopReason.ERROR,
            retry_count=retry_count,
            usage=usage,
            cost=cost,
            error_kind=error_kind,
        )

    def cancel(
        self,
        *,
        usage: UsageEvent | None = None,
        cost: CostEstimate | None = None,
    ) -> bool:
        return self.finish(
            status=SpanStatus.CANCELLED,
            stop_reason=StopReason.CANCELLED,
            usage=usage,
            cost=cost,
        )

    def partial(
        self,
        error_kind: ErrorKind,
        *,
        retry_count: int = 0,
        usage: UsageEvent | None = None,
        cost: CostEstimate | None = None,
    ) -> bool:
        return self.finish(
            status=SpanStatus.PARTIAL,
            stop_reason=StopReason.ERROR,
            retry_count=retry_count,
            usage=usage,
            cost=cost,
            error_kind=error_kind,
        )


class InertSpanHandle:
    """Shared-shape disabled span that performs no work."""

    span_id = None
    context = None
    settled = False

    def child(self, span: SpanSchema) -> InertSpanHandle:
        return self

    def record(self, event: TelemetryEvent) -> bool:
        return False

    def finish(self, **kwargs: object) -> bool:
        return False

    def fail(self, error_kind: ErrorKind, **kwargs: object) -> bool:
        return False

    def cancel(self, **kwargs: object) -> bool:
        return False

    def partial(self, error_kind: ErrorKind, **kwargs: object) -> bool:
        return False
