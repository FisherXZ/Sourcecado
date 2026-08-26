"""Sourcecado model access: streamed text and typed tool calls."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    text_delta: str | None = None
    tool_calls: list[ToolCall] | None = None


class StreamProvider(Protocol):
    model_id: str

    def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
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
    ) -> AsyncIterator[StreamChunk]:
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
    ) -> AsyncIterator[StreamChunk]:
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

    async def astream(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        import httpx

        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
        }
        body: dict[str, Any] = {
            "model": self.model_id,
            "stream": True,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        acc: dict[int, dict[str, str]] = {}
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                if resp.status_code >= 400:
                    err = (await resp.aread()).decode("utf-8", errors="replace")
                    raise RuntimeError(f"openai {resp.status_code}: {err[:400]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    data = json.loads(payload)
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content")
                    if text:
                        yield StreamChunk(text_delta=text)
                    for raw in delta.get("tool_calls") or []:
                        idx = int(raw.get("index") or 0)
                        slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if raw.get("id"):
                            slot["id"] = str(raw["id"])
                        fn = raw.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += str(fn["name"])
                        if fn.get("arguments"):
                            slot["arguments"] += str(fn["arguments"])
        calls = [_parse_tool_slot(slot) for slot in acc.values() if slot.get("name")]
        if calls:
            yield StreamChunk(tool_calls=calls)


def _parse_tool_slot(slot: dict[str, str]) -> ToolCall:
    raw_args = slot.get("arguments") or "{}"
    try:
        parsed = json.loads(raw_args)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return ToolCall(id=slot.get("id") or "call_0", name=slot["name"], arguments=parsed)


def provider_from_env() -> Optional[StreamProvider]:
    """DeepSeek V4 Pro first, Kimi K3 second. Other keys are last-resort."""
    override = _env("CLUB_MODEL")
    if _deepseek_key():
        return OpenAICompatProvider(
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
