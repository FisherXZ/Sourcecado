"""Sourcecado model access: streamed text and typed tool calls."""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, AsyncIterator, Optional, Protocol


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


@dataclass
class StreamChunk:
    text_delta: str | None = None
    transient_reasoning_delta: str | None = None
    tool_call_deltas: list[ToolCallDelta] | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    usage: ModelUsage | None = None


class StreamProvider(Protocol):
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
        for delta in step.get("deltas") or ():
            yield StreamChunk(text_delta=delta)
        calls = step.get("tool_calls")
        if calls:
            yield StreamChunk(tool_calls=list(calls))


DEEPSEEK_MODEL = "deepseek-v4-pro"
KIMI_MODEL = "kimi-k3"
DEEPSEEK_BASE = "https://api.deepseek.com"
KIMI_BASE = "https://api.moonshot.ai/v1"


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
    override = _env("CLUB_MODEL")
    if override:
        return override
    if _deepseek_key():
        return DEEPSEEK_MODEL
    if _kimi_key():
        return KIMI_MODEL
    if _anthropic_key():
        return "claude-sonnet-4-6"
    if _openai_key():
        return "gpt-4o-mini"
    return None


class AnthropicProvider:
    model_id: str

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 1024):
        self.api_key = api_key
        self.model_id = model
        self.max_tokens = max_tokens

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
        wire = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "stream": True,
            "messages": [m for m in wire if m.get("role") in ("user", "assistant")],
        }
        if system:
            body["system"] = system
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(f"anthropic {resp.status_code}: {err[:400]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    data = json.loads(payload)
                    if data.get("type") != "content_block_delta":
                        continue
                    delta = data.get("delta") or {}
                    text = delta.get("text")
                    if text:
                        yield StreamChunk(text_delta=text)


class OpenAICompatProvider:
    model_id: str
    strict_tool_arguments = False
    require_complete_stream = False
    valid_finish_reasons: frozenset[str] | None = None

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
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    def _http_error(self, status_code: int, body: str) -> RuntimeError:
        return RuntimeError(f"openai {status_code}: {body[:400]}")

    def _prepare_messages(
        self,
        *,
        context_id: str | None,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        del context_id, tools
        return messages

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
                    data = json.loads(payload)
                    usage = _parse_usage(data.get("usage"))
                    if usage is not None:
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
                            raise RuntimeError("openai stream returned invalid tool index")
                        idx = raw_index
                        slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        raw_id = str(raw["id"]) if raw.get("id") else None
                        if raw_id:
                            if slot["id"] and slot["id"] != raw_id:
                                raise RuntimeError(
                                    f"openai stream changed tool call identity at index {idx}"
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
                            raise RuntimeError(
                                f"openai stream returned unknown finish reason: {reason}"
                            )
                        if terminal_reason is not None and terminal_reason != reason:
                            raise RuntimeError("openai stream returned conflicting finish reasons")
                        terminal_reason = reason
                        yield StreamChunk(finish_reason=reason)
        if self.require_complete_stream and not saw_done:
            raise RuntimeError("deepseek stream ended before data: [DONE]")
        if self.require_complete_stream and terminal_reason is None:
            raise RuntimeError("deepseek stream ended without a terminal reason")
        if (
            self.require_complete_stream
            and acc
            and terminal_reason != "tool_calls"
        ):
            raise RuntimeError(
                f"deepseek stream ended tool assembly with {terminal_reason or 'no'} finish reason"
            )
        calls = []
        seen_call_ids: set[str] = set()
        for index in sorted(acc):
            slot = acc[index]
            if not slot.get("name") and not self.strict_tool_arguments:
                continue
            call = _parse_tool_slot(slot, strict=self.strict_tool_arguments)
            if self.strict_tool_arguments and call.id in seen_call_ids:
                raise RuntimeError(f"openai stream repeated tool call id: {call.id}")
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
        if calls:
            yield StreamChunk(tool_calls=calls)


class DeepSeekProvider(OpenAICompatProvider):
    """DeepSeek's OpenAI-compatible endpoint with provider-specific semantics."""

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
        return RuntimeError(
            f"deepseek provider request failed with HTTP {status_code}"
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


def _continuity_key(
    messages: list[dict[str, Any]], assistant: dict[str, Any]
) -> str:
    def durable_shape(message: dict[str, Any]) -> dict[str, Any]:
        shaped = deepcopy(message)
        shaped.pop("reasoning_content", None)
        shaped.pop("message_id", None)
        if (
            shaped.get("role") == "assistant"
            and shaped.get("tool_calls")
            and shaped.get("content") is None
        ):
            shaped["content"] = ""
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
    return ModelUsage(
        input_tokens=token_count("prompt_tokens"),
        output_tokens=token_count("completion_tokens"),
        total_tokens=token_count("total_tokens"),
        cached_input_tokens=token_count("prompt_cache_hit_tokens"),
        uncached_input_tokens=token_count("prompt_cache_miss_tokens"),
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


def provider_from_env() -> Optional[StreamProvider]:
    """DeepSeek V4 Pro first, Kimi K3 second. Other keys are last-resort."""
    override = _env("CLUB_MODEL")
    if _deepseek_key():
        return DeepSeekProvider(
            api_key=_deepseek_key(),
            model=override or DEEPSEEK_MODEL,
            base_url=_env("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE,
        )
    if _kimi_key():
        return OpenAICompatProvider(
            api_key=_kimi_key(),
            model=override or KIMI_MODEL,
            base_url=_env("KIMI_BASE_URL") or _env("MOONSHOT_BASE_URL") or KIMI_BASE,
        )
    if _anthropic_key():
        return AnthropicProvider(
            api_key=_anthropic_key(),
            model=override or "claude-sonnet-4-6",
        )
    if _openai_key():
        return OpenAICompatProvider(
            api_key=_openai_key(),
            model=override or "gpt-4o-mini",
            base_url=_env("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        )
    return None
