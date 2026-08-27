"""Closed telemetry schemas containing operational measurements only."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, TypeAlias

SCHEMA_VERSION = 1
_SYMBOL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SECRET_PREFIXES = ("sk-", "ghp_", "github_pat_", "ya29.", "aiza")


def _require_symbol(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not _SYMBOL.fullmatch(value)
        or value.lower().startswith(_SECRET_PREFIXES)
    ):
        raise ValueError(f"{name} must be a bounded operational identifier")


def _require_count(value: int | None, name: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or null")


def _require_cost(value: float) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("estimated_cost_usd must be finite and non-negative")


class SpanStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class StopReason(str, Enum):
    COMPLETED = "completed"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    CANCELLED = "cancelled"
    ERROR = "error"
    UNKNOWN = "unknown"


class ErrorKind(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    PROVIDER = "provider"
    TOOL = "tool"
    POLICY = "policy"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class RetryReason(str, Enum):
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    TRANSIENT_PROVIDER = "transient_provider"
    TRANSIENT_TOOL = "transient_tool"


class CompactionReason(str, Enum):
    CONTEXT_LIMIT = "context_limit"
    MANUAL = "manual"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class TraceContext:
    session_id: str
    run_id: str

    def __post_init__(self) -> None:
        _require_symbol(self.session_id, "session_id")
        _require_symbol(self.run_id, "run_id")


@dataclass(frozen=True, slots=True)
class ProviderSpan:
    span_type: str = field(init=False, default="provider")
    provider: str
    model: str
    operation: str
    context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_symbol(self.provider, "provider")
        _require_symbol(self.model, "model")
        _require_symbol(self.operation, "operation")
        _require_count(self.context_window_tokens, "context_window_tokens")


@dataclass(frozen=True, slots=True)
class AgentTurnSpan:
    span_type: str = field(init=False, default="agent_turn")
    operation: str

    def __post_init__(self) -> None:
        _require_symbol(self.operation, "operation")


@dataclass(frozen=True, slots=True)
class ToolSpan:
    span_type: str = field(init=False, default="tool")
    tool_name: str
    operation: str

    def __post_init__(self) -> None:
        _require_symbol(self.tool_name, "tool_name")
        _require_symbol(self.operation, "operation")


@dataclass(frozen=True, slots=True)
class RetryEvent:
    event_type: str = field(init=False, default="retry")
    operation: str
    retry_count: int
    reason: RetryReason
    delay_ms: int | None = None

    def __post_init__(self) -> None:
        _require_symbol(self.operation, "operation")
        _require_count(self.retry_count, "retry_count")
        if not isinstance(self.reason, RetryReason):
            raise ValueError("reason must be a RetryReason")
        _require_count(self.delay_ms, "delay_ms")


@dataclass(frozen=True, slots=True)
class CompactionEvent:
    event_type: str = field(init=False, default="compaction")
    operation: str
    reason: CompactionReason
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_symbol(self.operation, "operation")
        if not isinstance(self.reason, CompactionReason):
            raise ValueError("reason must be a CompactionReason")
        _require_count(self.input_tokens, "input_tokens")
        _require_count(self.output_tokens, "output_tokens")


@dataclass(frozen=True, slots=True)
class UsageEvent:
    event_type: str = field(init=False, default="usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_hit_input_tokens: int | None = None
    cache_miss_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    current_context_tokens: int | None = None
    context_window_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_count(self.input_tokens, "input_tokens")
        _require_count(self.output_tokens, "output_tokens")
        _require_count(self.total_tokens, "total_tokens")
        _require_count(self.cache_hit_input_tokens, "cache_hit_input_tokens")
        _require_count(self.cache_miss_input_tokens, "cache_miss_input_tokens")
        _require_count(self.cache_write_input_tokens, "cache_write_input_tokens")
        _require_count(self.reasoning_tokens, "reasoning_tokens")
        _require_count(self.current_context_tokens, "current_context_tokens")
        _require_count(self.context_window_tokens, "context_window_tokens")
        if (
            self.current_context_tokens is not None
            and self.context_window_tokens is not None
            and self.current_context_tokens > self.context_window_tokens
        ):
            raise ValueError("current_context_tokens cannot exceed context_window_tokens")


@dataclass(frozen=True, slots=True)
class CostEstimate:
    estimated_cost_usd: float

    def __post_init__(self) -> None:
        _require_cost(self.estimated_cost_usd)


@dataclass(frozen=True, slots=True)
class CostEvent:
    event_type: str = field(init=False, default="cost")
    cost: CostEstimate

    def __post_init__(self) -> None:
        if not isinstance(self.cost, CostEstimate):
            raise ValueError("cost must be a CostEstimate")


@dataclass(frozen=True, slots=True)
class TerminalEvent:
    event_type: str = field(init=False, default="terminal")
    status: SpanStatus
    latency_ms: float
    stop_reason: StopReason | None = None
    retry_count: int = 0
    usage: UsageEvent | None = None
    cost: CostEstimate | None = None
    error_kind: ErrorKind | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, SpanStatus):
            raise ValueError("status must be a SpanStatus")
        if self.stop_reason is not None and not isinstance(self.stop_reason, StopReason):
            raise ValueError("stop_reason must be a StopReason or null")
        if self.error_kind is not None and not isinstance(self.error_kind, ErrorKind):
            raise ValueError("error_kind must be an ErrorKind or null")
        if self.usage is not None and type(self.usage) is not UsageEvent:
            raise ValueError("usage must be a UsageEvent or null")
        if self.cost is not None and type(self.cost) is not CostEstimate:
            raise ValueError("cost must be a CostEstimate or null")
        if (
            not isinstance(self.latency_ms, (int, float))
            or isinstance(self.latency_ms, bool)
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be finite and non-negative")
        _require_count(self.retry_count, "retry_count")
        if self.status is SpanStatus.FAILED and self.error_kind is None:
            raise ValueError("failed terminal events require error_kind")
        if self.status not in {SpanStatus.FAILED, SpanStatus.PARTIAL} and self.error_kind:
            raise ValueError("error_kind is only valid for failed or partial spans")


SpanSchema: TypeAlias = ProviderSpan | AgentTurnSpan | ToolSpan
TelemetryEvent: TypeAlias = RetryEvent | CompactionEvent | UsageEvent | CostEvent
SPAN_SCHEMA_TYPES = (ProviderSpan, AgentTurnSpan, ToolSpan)
TELEMETRY_EVENT_TYPES = (RetryEvent, CompactionEvent, UsageEvent, CostEvent)


@dataclass(frozen=True, slots=True)
class SpanStartedRecord:
    version: int = field(init=False, default=SCHEMA_VERSION)
    record_type: str = field(init=False, default="span_started")
    sequence: int
    span_id: str
    parent_span_id: str | None
    context: TraceContext
    observed_at_ns: int
    span: SpanSchema

    def __post_init__(self) -> None:
        _require_count(self.sequence, "sequence")
        _require_symbol(self.span_id, "span_id")
        if type(self.context) is not TraceContext:
            raise ValueError("context must be a TraceContext")
        if self.parent_span_id is not None:
            _require_symbol(self.parent_span_id, "parent_span_id")
        _require_count(self.observed_at_ns, "observed_at_ns")
        if type(self.span) not in SPAN_SCHEMA_TYPES:
            raise ValueError("span must use a closed span schema")


@dataclass(frozen=True, slots=True)
class SpanEventRecord:
    version: int = field(init=False, default=SCHEMA_VERSION)
    record_type: str = field(init=False, default="span_event")
    sequence: int
    span_id: str
    context: TraceContext
    observed_at_ns: int
    event: TelemetryEvent

    def __post_init__(self) -> None:
        _require_count(self.sequence, "sequence")
        _require_symbol(self.span_id, "span_id")
        if type(self.context) is not TraceContext:
            raise ValueError("context must be a TraceContext")
        _require_count(self.observed_at_ns, "observed_at_ns")
        if type(self.event) not in TELEMETRY_EVENT_TYPES:
            raise ValueError("event must use a closed event schema")


@dataclass(frozen=True, slots=True)
class SpanSettledRecord:
    version: int = field(init=False, default=SCHEMA_VERSION)
    record_type: str = field(init=False, default="span_settled")
    sequence: int
    span_id: str
    context: TraceContext
    observed_at_ns: int
    terminal: TerminalEvent

    def __post_init__(self) -> None:
        _require_count(self.sequence, "sequence")
        _require_symbol(self.span_id, "span_id")
        if type(self.context) is not TraceContext:
            raise ValueError("context must be a TraceContext")
        _require_count(self.observed_at_ns, "observed_at_ns")
        if type(self.terminal) is not TerminalEvent:
            raise ValueError("terminal must be a TerminalEvent")


TelemetryRecord: TypeAlias = SpanStartedRecord | SpanEventRecord | SpanSettledRecord
TELEMETRY_RECORD_TYPES = (SpanStartedRecord, SpanEventRecord, SpanSettledRecord)
_SERIALIZABLE_TYPES = (
    TraceContext,
    ProviderSpan,
    AgentTurnSpan,
    ToolSpan,
    RetryEvent,
    CompactionEvent,
    UsageEvent,
    CostEstimate,
    CostEvent,
    TerminalEvent,
    *TELEMETRY_RECORD_TYPES,
)
_SERIALIZABLE_ENUM_TYPES = (
    SpanStatus,
    StopReason,
    ErrorKind,
    RetryReason,
    CompactionReason,
)


def record_to_dict(value: Any) -> Any:
    """Convert only closed telemetry values to JSON-compatible primitives."""
    if type(value) not in _SERIALIZABLE_TYPES and type(value) not in _SERIALIZABLE_ENUM_TYPES:
        raise TypeError(f"unsupported telemetry value {type(value).__name__}")
    return _to_primitive(value)


def _to_primitive(value: Any) -> Any:
    if type(value) in _SERIALIZABLE_ENUM_TYPES:
        return value.value
    if type(value) in _SERIALIZABLE_TYPES and is_dataclass(value):
        return {
            item.name: _to_primitive(getattr(value, item.name))
            for item in fields(value)
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported telemetry value {type(value).__name__}")
