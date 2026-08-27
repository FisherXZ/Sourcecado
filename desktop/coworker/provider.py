"""Sourcecado model access: streamed text and typed tool calls."""

from __future__ import annotations

import json
import os
import re
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from time import monotonic
from typing import Any, AsyncIterator, Mapping, Optional, Protocol
from urllib.parse import urlparse


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name_delta: str | None = None
    arguments_delta: str | None = None


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    reasoning_tokens: int
    cache_write_input_tokens: int = 0

    def __post_init__(self) -> None:
        counts = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cached_input_tokens,
            self.uncached_input_tokens,
            self.reasoning_tokens,
            self.cache_write_input_tokens,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        ):
            raise ValueError("usage counts must be non-negative integers")
        if self.cached_input_tokens + self.uncached_input_tokens != self.input_tokens:
            raise ValueError("cached and uncached input counts must equal input tokens")
        if self.cache_write_input_tokens > self.uncached_input_tokens:
            raise ValueError("cache write input cannot exceed uncached input tokens")
        if self.input_tokens + self.output_tokens != self.total_tokens:
            raise ValueError("input and output counts must equal total tokens")


class StreamKind(str, Enum):
    START = "start"
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_DELTA = "tool_delta"
    TOOL_CALLS = "tool_calls"
    USAGE = "usage"
    TERMINAL = "terminal"
    ERROR = "error"


class ProviderErrorKind(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    PROTOCOL = "protocol"
    PROVIDER = "provider"
    CONFIGURATION = "configuration"


@dataclass(frozen=True)
class ProviderStart:
    provider: str
    model: str


@dataclass(frozen=True)
class ProviderCapabilities:
    text: bool
    transient_reasoning: bool
    tool_calling: bool
    terminal_usage: bool
    cache_usage: bool
    reasoning_usage: bool


@dataclass(frozen=True)
class ProviderVerification:
    provider: str
    model: str
    eligible: bool
    failures: tuple[str, ...]
    capabilities: ProviderCapabilities


@dataclass(frozen=True)
class ProviderTerminal:
    stop_reason: str
    usage: ModelUsage | None
    latency_ms: float
    estimated_cost_usd: float | None


@dataclass(frozen=True)
class ModelPricing:
    uncached_input_per_million: float
    cached_input_per_million: float
    output_per_million: float
    cache_write_input_per_million: float | None = None


@dataclass(frozen=True)
class ProviderModelMetadata:
    provider: str
    model: str
    context_window_tokens: int | None
    pricing: ModelPricing | None


class ProviderStreamError(RuntimeError):
    kind = StreamKind.ERROR

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        kind: ProviderErrorKind,
        message: str,
        retryable: bool,
        http_status: int | None = None,
    ) -> None:
        super().__init__(f"{provider} {message}")
        self.provider = provider
        self.model = model
        self.error_kind = kind
        self.retryable = retryable
        self.http_status = http_status


def _http_error_kind(status_code: int) -> ProviderErrorKind:
    if status_code in {401, 403}:
        return ProviderErrorKind.AUTHENTICATION
    if status_code == 429:
        return ProviderErrorKind.RATE_LIMIT
    if status_code == 408:
        return ProviderErrorKind.TIMEOUT
    if status_code in {400, 404, 409, 422}:
        return ProviderErrorKind.INVALID_REQUEST
    return ProviderErrorKind.PROVIDER


def _provider_http_error(
    *, provider: str, model: str, status_code: int
) -> ProviderStreamError:
    return ProviderStreamError(
        provider=provider,
        model=model,
        kind=_http_error_kind(status_code),
        message=f"provider request failed with HTTP {status_code}",
        retryable=status_code in {408, 409, 429} or status_code >= 500,
        http_status=status_code,
    )


@dataclass
class StreamChunk:
    text_delta: str | None = None
    transient_reasoning_delta: str | None = None
    tool_call_deltas: list[ToolCallDelta] | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    usage: ModelUsage | None = None
    kind: StreamKind | None = None
    start: ProviderStart | None = None
    terminal: ProviderTerminal | None = None

    def __post_init__(self) -> None:
        payload_count = sum(
            (
                self.text_delta is not None,
                self.transient_reasoning_delta is not None,
                self.tool_call_deltas is not None,
                self.tool_calls is not None,
                self.usage is not None,
                self.finish_reason is not None or self.terminal is not None,
                self.start is not None,
            )
        )
        if payload_count > 1:
            raise ValueError("stream chunks require exactly one lifecycle payload")
        if self.kind is not None:
            return
        if self.text_delta is not None:
            self.kind = StreamKind.TEXT
        elif self.transient_reasoning_delta is not None:
            self.kind = StreamKind.REASONING
        elif self.tool_call_deltas is not None:
            self.kind = StreamKind.TOOL_DELTA
        elif self.tool_calls is not None:
            self.kind = StreamKind.TOOL_CALLS
        elif self.usage is not None:
            self.kind = StreamKind.USAGE
        elif self.finish_reason is not None or self.terminal is not None:
            self.kind = StreamKind.TERMINAL
        elif self.start is not None:
            self.kind = StreamKind.START

    @classmethod
    def started(cls, *, provider: str, model: str) -> StreamChunk:
        return cls(
            kind=StreamKind.START,
            start=ProviderStart(provider=provider, model=model),
        )


class StreamProvider(Protocol):
    provider_id: str
    model_id: str

    def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]: ...


class FakeProvider:
    """Deterministic stream for tests. Never hits a network."""

    provider_id = "fake"
    model_id = "fake"

    def __init__(
        self,
        deltas: tuple[str, ...] = ("Hello ", "world"),
        steps: list[dict[str, Any]] | None = None,
    ):
        self.steps = list(steps) if steps is not None else [{"deltas": deltas}]
        self.i = 0
        self.calls: list[list[dict[str, Any]]] = []

    async def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        del context_id
        self.calls.append(list(messages))
        step = self.steps[min(self.i, len(self.steps) - 1)]
        self.i += 1
        started_at = monotonic()
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        failure = step.get("error")
        if isinstance(failure, ProviderStreamError):
            raise failure
        for delta in step.get("reasoning_deltas") or ():
            yield StreamChunk(transient_reasoning_delta=str(delta))
        for delta in step.get("deltas") or ():
            yield StreamChunk(text_delta=delta)
        for delta in step.get("tool_call_deltas") or ():
            yield StreamChunk(tool_call_deltas=[delta])
        usage = step.get("usage")
        if isinstance(usage, ModelUsage):
            yield StreamChunk(usage=usage)
        finish_reason = step.get("finish_reason")
        if finish_reason:
            reason = str(finish_reason)
            yield StreamChunk(
                finish_reason=reason,
                terminal=ProviderTerminal(
                    stop_reason=reason,
                    usage=usage if isinstance(usage, ModelUsage) else None,
                    latency_ms=max(0.0, monotonic() - started_at) * 1000,
                    estimated_cost_usd=estimate_model_cost(
                        self.provider_id,
                        self.model_id,
                        usage if isinstance(usage, ModelUsage) else None,
                    ),
                ),
            )
        calls = step.get("tool_calls")
        if calls:
            yield StreamChunk(tool_calls=list(calls))


DEEPSEEK_MODEL = "deepseek-v4-pro"
KIMI_MODEL = "kimi-k3"
DEEPSEEK_BASE = "https://api.deepseek.com"
KIMI_BASE = "https://api.moonshot.ai/v1"

_PROVIDER_DEFAULTS: tuple[tuple[str, str, str], ...] = (
    ("deepseek", DEEPSEEK_MODEL, "DEEPSEEK_API_KEY"),
    ("kimi", KIMI_MODEL, "MOONSHOT_API_KEY"),
    ("anthropic", "claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
    ("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
)

_PROVIDER_CAPABILITIES = {
    "deepseek": ProviderCapabilities(True, True, True, True, True, True),
    "kimi": ProviderCapabilities(True, True, True, True, True, False),
    "anthropic": ProviderCapabilities(True, True, True, True, True, False),
    "openai": ProviderCapabilities(True, True, True, True, True, True),
}

# Verified against provider catalogs on 2026-08-27. Unknown or newly priced
# models stay unavailable until this Sourcecado-owned table is revalidated.
_MODEL_METADATA: dict[tuple[str, str], tuple[int, ModelPricing]] = {
    ("deepseek", "deepseek-v4-flash"): (
        1_000_000,
        ModelPricing(0.14, 0.0028, 0.28),
    ),
    ("deepseek", "deepseek-v4-pro"): (
        1_000_000,
        ModelPricing(0.435, 0.003625, 0.87),
    ),
    ("kimi", "kimi-k3"): (
        1_000_000,
        ModelPricing(3.0, 0.30, 15.0),
    ),
    ("anthropic", "claude-sonnet-4-6"): (
        1_000_000,
        ModelPricing(3.0, 0.30, 15.0, 3.75),
    ),
    ("openai", "gpt-4o-mini"): (
        128_000,
        ModelPricing(0.15, 0.075, 0.60),
    ),
}
_MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_WINDOWS_ABSOLUTE_MODEL = re.compile(r"^[A-Za-z]:/")
_SECRET_MODEL_PREFIXES = (
    "sk-",
    "ghp_",
    "github_pat_",
    "ya29.",
    "aiza",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "xoxr-",
    "xapp-",
)


def provider_model_metadata(provider: str, model: str) -> ProviderModelMetadata:
    known = _MODEL_METADATA.get((provider, model))
    return ProviderModelMetadata(
        provider=provider,
        model=model,
        context_window_tokens=known[0] if known is not None else None,
        pricing=known[1] if known is not None else None,
    )


def estimate_model_cost(
    provider: str,
    model: str,
    usage: ModelUsage | None,
) -> float | None:
    pricing = provider_model_metadata(provider, model).pricing
    if pricing is None or usage is None:
        return None
    cache_write_rate = (
        pricing.cache_write_input_per_million
        if pricing.cache_write_input_per_million is not None
        else pricing.uncached_input_per_million
    )
    ordinary_input = usage.uncached_input_tokens - usage.cache_write_input_tokens
    return (
        ordinary_input * pricing.uncached_input_per_million
        + usage.cache_write_input_tokens * cache_write_rate
        + usage.cached_input_tokens * pricing.cached_input_per_million
        + usage.output_tokens * pricing.output_per_million
    ) / 1_000_000


def safe_model_identifier(value: str) -> bool:
    if not _MODEL_IDENTIFIER.fullmatch(value):
        return False
    lowered = value.lower()
    if lowered.startswith(_SECRET_MODEL_PREFIXES):
        return False
    if lowered.startswith("file:/") or "://" in lowered:
        return False
    if _WINDOWS_ABSOLUTE_MODEL.match(value):
        return False
    return all(segment not in {"", ".", ".."} for segment in value.split("/"))


def _first_configured_provider(env: Mapping[str, str]) -> str | None:
    return next(
        (
            provider
            for provider, _model, key_name in _PROVIDER_DEFAULTS
            if str(env.get(key_name) or "").strip()
            or (
                provider == "kimi"
                and str(env.get("KIMI_API_KEY") or "").strip()
            )
        ),
        None,
    )


def provider_verifications(
    environment: Mapping[str, str] | None = None,
) -> tuple[ProviderVerification, ...]:
    env = os.environ if environment is None else environment
    override = str(env.get("CLUB_MODEL") or "").strip()
    first_configured = _first_configured_provider(env)
    reports: list[ProviderVerification] = []
    for provider, default_model, key_name in _PROVIDER_DEFAULTS:
        requested_model = (
            override if override and provider == first_configured else default_model
        )
        model = (
            requested_model
            if safe_model_identifier(requested_model)
            else default_model
        )
        key = str(env.get(key_name) or "").strip()
        if provider == "kimi" and not key:
            key = str(env.get("KIMI_API_KEY") or "").strip()
        failures: list[str] = []
        if requested_model != model:
            failures.append("invalid_model_identifier")
        if not key:
            failures.append("missing_api_key")
        base_url = ""
        if provider == "deepseek":
            base_url = str(env.get("DEEPSEEK_BASE_URL") or "").strip()
        elif provider == "kimi":
            base_url = str(
                env.get("KIMI_BASE_URL") or env.get("MOONSHOT_BASE_URL") or ""
            ).strip()
        elif provider == "openai":
            base_url = str(env.get("OPENAI_BASE_URL") or "").strip()
        if base_url and not _safe_provider_base_url(base_url):
            failures.append("invalid_base_url")
        if provider == "deepseek" and model not in {
            "deepseek-v4-pro",
            "deepseek-v4-flash",
        }:
            failures.append("unsupported_model")
        elif provider == "kimi" and model != "kimi-k3":
            failures.append("unsupported_model")
        elif provider == "anthropic" and not model.startswith("claude-"):
            failures.append("unsupported_model")
        capabilities = _PROVIDER_CAPABILITIES[provider]
        if key and not capabilities.tool_calling:
            failures.append("provider_contract_unverified")
        reports.append(
            ProviderVerification(
                provider=provider,
                model=model,
                eligible=not failures,
                failures=tuple(failures),
                capabilities=capabilities,
            )
        )
    return tuple(reports)


def _safe_provider_base_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _deepseek_key() -> str:
    return _env("DEEPSEEK_API_KEY")


def _kimi_key() -> str:
    return _env("MOONSHOT_API_KEY") or _env("KIMI_API_KEY")


def _anthropic_key() -> str:
    return _env("ANTHROPIC_API_KEY")


def _openai_key() -> str:
    return _env("OPENAI_API_KEY")


def default_model_id() -> Optional[str]:
    provider = provider_from_env()
    return provider.model_id if provider is not None else None


class AnthropicProvider:
    provider_id = "anthropic"
    model_id: str

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 1024):
        self.api_key = api_key
        self.model_id = model
        self.max_tokens = max_tokens

    def _protocol_error(self, message: str) -> ProviderStreamError:
        return ProviderStreamError(
            provider=self.provider_id,
            model=self.model_id,
            kind=ProviderErrorKind.PROTOCOL,
            message=message,
            retryable=False,
        )

    def _wire_tools(
        self, tools: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for raw in tools or []:
            function = raw.get("function")
            if not isinstance(function, dict) or not function.get("name"):
                raise self._protocol_error("tool definition is missing a function name")
            schema = function.get("parameters")
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            item: dict[str, Any] = {
                "name": str(function["name"]),
                "input_schema": deepcopy(schema),
            }
            if function.get("description"):
                item["description"] = str(function["description"])
            wire.append(item)
        return wire

    def _wire_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            if pending_results:
                wire.append(
                    {"role": "user", "content": list(pending_results)}
                )
                pending_results.clear()

        for original in messages:
            role = original.get("role")
            if role == "system":
                continue
            if role == "tool":
                call_id = str(original.get("tool_call_id") or "")
                if not call_id:
                    raise self._protocol_error("tool result is missing tool_call_id")
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": str(original.get("content") or ""),
                    }
                )
                continue
            flush_results()
            if role == "user":
                wire.append(
                    {"role": "user", "content": deepcopy(original.get("content") or "")}
                )
                continue
            if role != "assistant":
                continue
            calls = original.get("tool_calls") or []
            if not calls:
                wire.append(
                    {
                        "role": "assistant",
                        "content": deepcopy(original.get("content") or ""),
                    }
                )
                continue
            blocks: list[dict[str, Any]] = []
            content = original.get("content")
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for raw_call in calls:
                if not isinstance(raw_call, dict):
                    raise self._protocol_error("assistant tool call is malformed")
                function = raw_call.get("function")
                if not isinstance(function, dict):
                    raise self._protocol_error("assistant tool call is missing function")
                call_id = str(raw_call.get("id") or "")
                name = str(function.get("name") or "")
                if not call_id or not name:
                    raise self._protocol_error("assistant tool call is missing identity")
                try:
                    arguments = json.loads(str(function.get("arguments") or "{}"))
                except json.JSONDecodeError as exc:
                    raise self._protocol_error(
                        "assistant tool call has invalid JSON arguments"
                    ) from exc
                if not isinstance(arguments, dict):
                    raise self._protocol_error(
                        "assistant tool call arguments are not an object"
                    )
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": arguments,
                    }
                )
            wire.append({"role": "assistant", "content": blocks})
        flush_results()
        return wire

    async def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        del context_id
        import httpx

        system = " ".join(
            str(m.get("content") or "") for m in messages if m.get("role") == "system"
        )
        wire = self._wire_messages(messages)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": wire,
        }
        if system:
            body["system"] = system
        wire_tools = self._wire_tools(tools)
        if wire_tools:
            body["tools"] = wire_tools
        started_at = monotonic()
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        slots: dict[int, dict[str, str]] = {}
        initial_usage: dict[str, Any] = {}
        final_usage: dict[str, Any] = {}
        stop_reason: str | None = None
        saw_message_stop = False
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise _provider_http_error(
                        provider=self.provider_id,
                        model=self.model_id,
                        status_code=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise self._protocol_error(
                            "stream event is malformed JSON"
                        ) from exc
                    event_type = data.get("type")
                    if event_type == "message_start":
                        message = data.get("message")
                        if isinstance(message, dict) and isinstance(
                            message.get("usage"), dict
                        ):
                            initial_usage = dict(message["usage"])
                        continue
                    if event_type == "content_block_start":
                        block = data.get("content_block")
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        index = data.get("index")
                        if isinstance(index, bool) or not isinstance(index, int):
                            raise self._protocol_error("tool block has invalid index")
                        call_id = str(block.get("id") or "")
                        name = str(block.get("name") or "")
                        if not call_id or not name:
                            raise self._protocol_error("tool block is missing identity")
                        initial_input = block.get("input")
                        arguments = (
                            json.dumps(initial_input)
                            if isinstance(initial_input, dict) and initial_input
                            else ""
                        )
                        slots[index] = {
                            "id": call_id,
                            "name": name,
                            "arguments": arguments,
                        }
                        yield StreamChunk(
                            tool_call_deltas=[
                                ToolCallDelta(index=index, id=call_id, name_delta=name)
                            ]
                        )
                        continue
                    if event_type == "content_block_delta":
                        delta = data.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        delta_type = delta.get("type")
                        if delta_type == "text_delta" and delta.get("text"):
                            yield StreamChunk(text_delta=str(delta["text"]))
                            continue
                        if delta_type == "thinking_delta" and delta.get("thinking"):
                            yield StreamChunk(
                                transient_reasoning_delta=str(delta["thinking"])
                            )
                            continue
                        if delta_type != "input_json_delta":
                            continue
                        index = data.get("index")
                        if not isinstance(index, int) or isinstance(index, bool):
                            raise self._protocol_error("tool delta has invalid index")
                        if index not in slots:
                            raise self._protocol_error("tool delta has no matching block")
                        arguments_delta = str(delta.get("partial_json") or "")
                        slots[index]["arguments"] += arguments_delta
                        yield StreamChunk(
                            tool_call_deltas=[
                                ToolCallDelta(
                                    index=index,
                                    arguments_delta=arguments_delta,
                                )
                            ]
                        )
                        continue
                    if event_type == "message_delta":
                        delta = data.get("delta")
                        if isinstance(delta, dict) and delta.get("stop_reason"):
                            stop_reason = str(delta["stop_reason"])
                        if isinstance(data.get("usage"), dict):
                            final_usage = dict(data["usage"])
                        continue
                    if event_type == "error":
                        error = data.get("error")
                        error_type = (
                            str(error.get("type") or "provider_error")
                            if isinstance(error, dict)
                            else "provider_error"
                        )
                        raise ProviderStreamError(
                            provider=self.provider_id,
                            model=self.model_id,
                            kind=ProviderErrorKind.PROVIDER,
                            message=f"stream failed: {error_type}",
                            retryable=error_type in {"overloaded_error", "rate_limit_error"},
                        )
                    if event_type == "message_stop":
                        saw_message_stop = True
                        continue
        if not saw_message_stop:
            raise self._protocol_error("stream ended before message_stop")
        finish_reason = _anthropic_finish_reason(stop_reason)
        if finish_reason is None:
            raise self._protocol_error("stream ended without a terminal reason")
        if slots and finish_reason != "tool_calls":
            raise self._protocol_error(
                f"stream ended tool assembly with {finish_reason} finish reason"
            )
        calls: list[ToolCall] = []
        for index in sorted(slots):
            try:
                call = _parse_tool_slot(slots[index], strict=True)
                _validate_tool_call(call, tools)
                calls.append(call)
            except RuntimeError as exc:
                raise self._protocol_error(str(exc)) from exc
        usage = _parse_anthropic_usage(initial_usage, final_usage)
        yield StreamChunk(usage=usage)
        yield StreamChunk(
            finish_reason=finish_reason,
            terminal=ProviderTerminal(
                stop_reason=finish_reason,
                usage=usage,
                latency_ms=max(0.0, monotonic() - started_at) * 1000,
                estimated_cost_usd=estimate_model_cost(
                    self.provider_id, self.model_id, usage
                ),
            ),
        )
        if calls:
            yield StreamChunk(tool_calls=calls)


def _anthropic_finish_reason(reason: str | None) -> str | None:
    return {
        "end_turn": "stop",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
        "max_tokens": "length",
        "refusal": "content_filter",
    }.get(reason) if reason is not None else None


def _parse_anthropic_usage(
    initial: dict[str, Any], final: dict[str, Any]
) -> ModelUsage:
    def count(source: dict[str, Any], name: str) -> int:
        value = source.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProviderStreamError(
                provider="anthropic",
                model="unknown",
                kind=ProviderErrorKind.PROTOCOL,
                message=f"usage field is invalid: {name}",
                retryable=False,
            )
        return value

    cache_write = count(initial, "cache_creation_input_tokens")
    uncached = count(initial, "input_tokens") + cache_write
    cached = count(initial, "cache_read_input_tokens")
    output = count(final or initial, "output_tokens")
    input_tokens = cached + uncached
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output,
        total_tokens=input_tokens + output,
        cached_input_tokens=cached,
        uncached_input_tokens=uncached,
        reasoning_tokens=0,
        cache_write_input_tokens=cache_write,
    )


class OpenAICompatProvider:
    provider_id = "openai"
    model_id: str
    strict_tool_arguments = True
    require_complete_stream = True
    valid_finish_reasons: frozenset[str] | None = frozenset(
        {"stop", "tool_calls", "length", "content_filter", "function_call"}
    )

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
    ):
        self.api_key = api_key
        self.model_id = model
        self.base_url = base_url.rstrip("/")

    def _request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "stream": True,
            "stream_options": {"include_usage": True},
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    def _http_error(self, status_code: int, body: str) -> RuntimeError:
        del body
        return _provider_http_error(
            provider=self.provider_id,
            model=self.model_id,
            status_code=status_code,
        )

    def _protocol_error(self, message: str) -> ProviderStreamError:
        return ProviderStreamError(
            provider=self.provider_id,
            model=self.model_id,
            kind=ProviderErrorKind.PROTOCOL,
            message=message,
            retryable=False,
        )

    def _prepare_messages(
        self,
        *,
        context_id: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        del context_id, tools
        prepared = deepcopy(messages)
        for message in prepared:
            if message.get("role") == "assistant":
                message.pop("reasoning_content", None)
                message.pop("thinking", None)
                message.pop("reasoning", None)
        return prepared

    def _record_completed_response(
        self,
        *,
        context_id: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        reasoning: str,
        content: str,
        calls: list[ToolCall],
        finish_reason: str | None,
    ) -> None:
        del context_id, messages, tools, reasoning, content, calls, finish_reason

    async def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        context_id: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        import httpx

        started_at = monotonic()
        yield StreamChunk.started(provider=self.provider_id, model=self.model_id)
        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        wire_messages = self._prepare_messages(
            context_id=context_id,
            messages=messages,
            tools=tools,
        )
        body = self._request_body(messages=wire_messages, tools=tools)
        acc: dict[int, dict[str, str]] = {}
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []
        saw_done = False
        terminal_reason: str | None = None
        latest_usage: ModelUsage | None = None
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", errors="replace")
                    raise self._http_error(resp.status_code, err)
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        saw_done = True
                        break
                    try:
                        data = json.loads(payload)
                        usage = _parse_usage(data.get("usage"))
                    except (json.JSONDecodeError, RuntimeError, ValueError) as exc:
                        raise self._protocol_error(
                            "stream payload or usage is malformed"
                        ) from exc
                    if usage is not None:
                        latest_usage = usage
                        yield StreamChunk(usage=usage)
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        reasoning_parts.append(str(reasoning))
                        yield StreamChunk(transient_reasoning_delta=reasoning)
                    text = delta.get("content")
                    if text:
                        answer_parts.append(str(text))
                        yield StreamChunk(text_delta=text)
                    for raw in delta.get("tool_calls") or []:
                        raw_index = raw.get("index", 0)
                        if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                            raise self._protocol_error(
                                "stream returned invalid tool index"
                            )
                        idx = raw_index
                        slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        raw_id = str(raw["id"]) if raw.get("id") else None
                        if raw_id:
                            if slot["id"] and slot["id"] != raw_id:
                                raise self._protocol_error(
                                    f"stream changed tool call identity at index {idx}"
                                )
                            slot["id"] = raw_id
                        fn = raw.get("function") or {}
                        name_delta = str(fn["name"]) if fn.get("name") else None
                        arguments_delta = (
                            str(fn["arguments"])
                            if fn.get("arguments") is not None
                            else None
                        )
                        if name_delta:
                            slot["name"] += name_delta
                        if arguments_delta:
                            slot["arguments"] += arguments_delta
                        yield StreamChunk(
                            tool_call_deltas=[
                                ToolCallDelta(
                                    index=idx,
                                    id=raw_id,
                                    name_delta=name_delta,
                                    arguments_delta=arguments_delta,
                                )
                            ]
                        )
                    finish_reason = choice.get("finish_reason")
                    if finish_reason:
                        reason = str(finish_reason)
                        if (
                            self.valid_finish_reasons is not None
                            and reason not in self.valid_finish_reasons
                        ):
                            raise self._protocol_error(
                                f"stream returned unknown finish reason: {reason}"
                            )
                        if terminal_reason is not None and terminal_reason != reason:
                            raise self._protocol_error(
                                "stream returned conflicting finish reasons"
                            )
                        terminal_reason = reason
        if self.require_complete_stream and not saw_done:
            raise self._protocol_error("stream ended before data: [DONE]")
        if self.require_complete_stream and terminal_reason is None:
            raise self._protocol_error("stream ended without a terminal reason")
        if (
            self.require_complete_stream
            and acc
            and terminal_reason != "tool_calls"
        ):
            raise self._protocol_error(
                f"stream ended tool assembly with {terminal_reason or 'no'} finish reason"
            )
        calls = []
        seen_call_ids: set[str] = set()
        for index in sorted(acc):
            slot = acc[index]
            if not slot.get("name") and not self.strict_tool_arguments:
                continue
            try:
                call = _parse_tool_slot(slot, strict=self.strict_tool_arguments)
                _validate_tool_call(call, tools)
            except RuntimeError as exc:
                raise self._protocol_error(str(exc)) from exc
            if self.strict_tool_arguments and call.id in seen_call_ids:
                raise self._protocol_error(f"stream repeated tool call id: {call.id}")
            seen_call_ids.add(call.id)
            calls.append(call)
        self._record_completed_response(
            context_id=context_id,
            messages=messages,
            tools=tools,
            reasoning="".join(reasoning_parts),
            content="".join(answer_parts),
            calls=calls,
            finish_reason=terminal_reason,
        )
        assert terminal_reason is not None
        yield StreamChunk(
            finish_reason=terminal_reason,
            terminal=ProviderTerminal(
                stop_reason=terminal_reason,
                usage=latest_usage,
                latency_ms=max(0.0, monotonic() - started_at) * 1000,
                estimated_cost_usd=estimate_model_cost(
                    self.provider_id, self.model_id, latest_usage
                ),
            ),
        )
        if calls:
            yield StreamChunk(tool_calls=calls)


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek's OpenAI-compatible endpoint with provider-specific semantics."""

    provider_id = "deepseek"
    strict_tool_arguments = True
    require_complete_stream = True
    valid_finish_reasons = frozenset(
        {
            "stop",
            "tool_calls",
            "length",
            "content_filter",
            "insufficient_system_resource",
        }
    )
    uses_transient_context = True
    _MAX_CONTEXTS = 128
    _MAX_RESPONSES_PER_CONTEXT = 256

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.deepseek.com",
    ):
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self._transient_reasoning: OrderedDict[
            str, OrderedDict[str, str]
        ] = OrderedDict()

    def _http_error(self, status_code: int, body: str) -> RuntimeError:
        del body
        return _provider_http_error(
            provider=self.provider_id,
            model=self.model_id,
            status_code=status_code,
        )

    def _prepare_messages(
        self,
        *,
        context_id: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if tools and not context_id:
            raise RuntimeError("deepseek tool requests require a transient context id")
        prepared: list[dict[str, Any]] = []
        cache = self._context_cache(context_id, create=False) if context_id else None
        for original in messages:
            message = deepcopy(original)
            if message.get("role") == "assistant":
                if message.get("tool_calls") and message.get("content") is None:
                    message["content"] = ""
                if tools and "reasoning_content" not in message:
                    key = _continuity_key(prepared, message)
                    reasoning = cache.get(key) if cache is not None else None
                    if reasoning is None:
                        raise RuntimeError(
                            "deepseek transient reasoning is unavailable for continuation"
                        )
                    message["reasoning_content"] = reasoning
            prepared.append(message)
        return prepared

    def _record_completed_response(
        self,
        *,
        context_id: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        reasoning: str,
        content: str,
        calls: list[ToolCall],
        finish_reason: str | None,
    ) -> None:
        if not tools or not context_id or finish_reason not in {"stop", "tool_calls"}:
            return
        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in calls
            ]
        cache = self._context_cache(context_id, create=True)
        assert cache is not None
        key = _continuity_key(messages, assistant)
        cache[key] = reasoning
        cache.move_to_end(key)
        while len(cache) > self._MAX_RESPONSES_PER_CONTEXT:
            cache.popitem(last=False)

    def _context_cache(
        self, context_id: str | None, *, create: bool
    ) -> OrderedDict[str, str] | None:
        if context_id is None:
            return None
        cache = self._transient_reasoning.get(context_id)
        if cache is None and create:
            cache = OrderedDict()
            self._transient_reasoning[context_id] = cache
        if cache is not None:
            self._transient_reasoning.move_to_end(context_id)
        while len(self._transient_reasoning) > self._MAX_CONTEXTS:
            self._transient_reasoning.popitem(last=False)
        return cache

    def _request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model_id,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        return body


class KimiProvider(DeepSeekProvider):
    """Moonshot's Kimi API with provider-owned compatibility behavior."""

    provider_id = "kimi"
    valid_finish_reasons = frozenset(
        {"stop", "tool_calls", "length", "content_filter"}
    )

    def _http_error(self, status_code: int, body: str) -> RuntimeError:
        del body
        return _provider_http_error(
            provider=self.provider_id,
            model=self.model_id,
            status_code=status_code,
        )

    def _prepare_messages(
        self,
        *,
        context_id: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        if context_id or not tools:
            return super()._prepare_messages(
                context_id=context_id,
                messages=messages,
                tools=tools,
            )
        prepared = deepcopy(messages)
        for message in prepared:
            if message.get("role") != "assistant" or not message.get("tool_calls"):
                continue
            if message.get("content") is None:
                message["content"] = ""
            if "reasoning_content" not in message:
                raise self._protocol_error(
                    "kimi tool continuation requires retained reasoning"
                )
        return prepared

    def _request_body(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        body = OpenAICompatProvider._request_body(
            self,
            messages=messages,
            tools=tools,
        )
        body["reasoning_effort"] = "max"
        return body


def _continuity_key(
    messages: list[dict[str, Any]], assistant: dict[str, Any]
) -> str:
    def durable_shape(message: dict[str, Any]) -> dict[str, Any]:
        shaped = deepcopy(message)
        shaped.pop("reasoning_content", None)
        shaped.pop("message_id", None)
        if shaped.get("role") == "assistant":
            if shaped.get("tool_calls") and shaped.get("content") is None:
                shaped["content"] = ""
            for call in shaped.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if isinstance(function, dict):
                    function.pop("arguments", None)
        if shaped.get("role") == "tool":
            shaped.pop("content", None)
        return shaped

    canonical = [
        durable_shape(message)
        for message in messages
        if message.get("role") != "system"
    ]
    canonical.append(durable_shape(assistant))
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _parse_usage(raw: Any) -> ModelUsage | None:
    if not isinstance(raw, dict):
        return None

    def token_count(name: str, source: dict[str, Any] = raw) -> int:
        value = source.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(f"openai stream returned invalid usage field: {name}")
        return value

    details = raw.get("completion_tokens_details")
    if not isinstance(details, dict):
        details = {}
    prompt_details = raw.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    input_tokens = token_count("prompt_tokens")
    if "prompt_cache_hit_tokens" in raw:
        cached_input_tokens = token_count("prompt_cache_hit_tokens")
    elif "cached_tokens" in raw:
        cached_input_tokens = token_count("cached_tokens")
    else:
        cached_input_tokens = token_count("cached_tokens", prompt_details)
    if "prompt_cache_miss_tokens" in raw:
        uncached_input_tokens = token_count("prompt_cache_miss_tokens")
    else:
        uncached_input_tokens = max(0, input_tokens - cached_input_tokens)
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=token_count("completion_tokens"),
        total_tokens=token_count("total_tokens"),
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=uncached_input_tokens,
        reasoning_tokens=token_count("reasoning_tokens", details),
    )


def _parse_tool_slot(slot: dict[str, str], *, strict: bool = False) -> ToolCall:
    call_id = slot.get("id") or ""
    name = slot.get("name") or ""
    if strict and not call_id:
        raise RuntimeError("openai stream returned tool call without an id")
    if strict and not name:
        raise RuntimeError("openai stream returned tool call without a name")
    raw_args = slot.get("arguments") or "{}"
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        if strict:
            raise RuntimeError("openai stream returned invalid JSON tool arguments") from exc
        parsed = {}
    if not isinstance(parsed, dict):
        if strict:
            raise RuntimeError("openai stream returned non-object tool arguments")
        parsed = {}
    return ToolCall(id=call_id or "call_0", name=name, arguments=parsed)


def _validate_tool_call(
    call: ToolCall, tools: list[dict[str, Any]] | None
) -> None:
    schemas: dict[str, dict[str, Any]] = {}
    for raw in tools or []:
        function = raw.get("function")
        if not isinstance(function, dict) or not function.get("name"):
            continue
        parameters = function.get("parameters")
        schemas[str(function["name"])] = (
            parameters if isinstance(parameters, dict) else {}
        )
    if call.name not in schemas:
        raise RuntimeError(f"tool call referenced unavailable tool: {call.name}")
    schema = schemas[call.name]
    for required in schema.get("required") or []:
        if str(required) not in call.arguments:
            raise RuntimeError(
                f"tool arguments required property missing: {required}"
            )
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for name, value in call.arguments.items():
        definition = properties.get(name)
        if not isinstance(definition, dict):
            continue
        expected = definition.get("type")
        if expected and not _matches_json_type(value, expected):
            raise RuntimeError(
                f"tool argument has wrong type for {name}: expected {expected}"
            )
        allowed = definition.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            raise RuntimeError(f"tool argument is outside enum for {name}")


def _matches_json_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_json_type(value, option) for option in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return False


def provider_from_env() -> Optional[StreamProvider]:
    """DeepSeek V4 Pro first, Kimi K3 second. Other keys are last-resort."""
    reports = {report.provider: report for report in provider_verifications()}
    override = _env("CLUB_MODEL")
    first_configured = _first_configured_provider(os.environ)
    if (
        override
        and first_configured is not None
        and not reports[first_configured].eligible
    ):
        return None
    if _deepseek_key() and reports["deepseek"].eligible:
        return DeepSeekProvider(
            api_key=_deepseek_key(),
            model=reports["deepseek"].model,
            base_url=_env("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE,
        )
    if _kimi_key() and reports["kimi"].eligible:
        return KimiProvider(
            api_key=_kimi_key(),
            model=reports["kimi"].model,
            base_url=_env("KIMI_BASE_URL") or _env("MOONSHOT_BASE_URL") or KIMI_BASE,
        )
    if _anthropic_key() and reports["anthropic"].eligible:
        return AnthropicProvider(
            api_key=_anthropic_key(),
            model=reports["anthropic"].model,
        )
    if _openai_key() and reports["openai"].eligible:
        return OpenAICompatProvider(
            api_key=_openai_key(),
            model=reports["openai"].model,
            base_url=_env("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        )
    return None
