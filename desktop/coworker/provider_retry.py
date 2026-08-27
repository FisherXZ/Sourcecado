"""Bounded retry decisions for one Sourcecado model request."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import math
import os
import random
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator, Mapping

import httpx

from coworker.provider import (
    DEEPSEEK_BASE,
    KIMI_BASE,
    AnthropicProvider,
    DeepSeekProvider,
    KimiProvider,
    OpenAICompatProvider,
    ProviderErrorKind,
    ProviderStreamError,
    StreamProvider,
    provider_verifications,
)
from coworker.telemetry import RetryReason


class RetryAction(str, Enum):
    RETRY = "retry"
    FAIL = "fail"
    REVIEW = "review"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    FAILOVER = "failover"
    FAIL = "fail"
    REVIEW = "review"
    CANCELLED = "cancelled"


class ProviderRequestCancelled(Exception):
    """Cooperative cancellation while waiting on the provider stream."""


@dataclass(frozen=True)
class RetryDecision:
    action: RetryAction
    reason: RetryReason | None = None
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts_per_provider: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    max_retry_after_seconds: float = 30.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts_per_provider < 1:
            raise ValueError("max_attempts_per_provider must be positive")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one")
        for value in (
            self.base_delay_seconds,
            self.max_delay_seconds,
            self.max_retry_after_seconds,
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError("retry delays must be finite and non-negative")

    def delay_seconds(
        self,
        retry_number: int,
        *,
        retry_after_seconds: float | None,
        jitter_value: float,
    ) -> float:
        if (
            retry_after_seconds is not None
            and math.isfinite(retry_after_seconds)
            and retry_after_seconds >= 0
        ):
            return min(retry_after_seconds, self.max_retry_after_seconds)
        exponent = max(0, retry_number - 1)
        base = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2**exponent),
        )
        jitter = min(1.0, max(0.0, jitter_value))
        multiplier = 1 + self.jitter_ratio * ((2 * jitter) - 1)
        return min(self.max_delay_seconds, base * multiplier)


@dataclass(frozen=True)
class RecoveryDirective:
    action: RecoveryAction
    provider: Any
    attempt_number: int
    reason: RetryReason | None
    delay_seconds: float = 0
    exhausted: bool = False


class RetryController:
    def __init__(
        self,
        providers: tuple[Any, ...],
        *,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]],
        random_value: Callable[[], float] = random.random,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        if not providers:
            raise ValueError("retry controller requires at least one provider")
        self.providers = providers
        self.policy = policy or RetryPolicy()
        self._sleep = sleep
        self._random_value = random_value
        self._cancel_event = cancel_event
        self.provider_index = 0
        self.attempt_number = 1

    @property
    def provider(self) -> Any:
        return self.providers[self.provider_index]

    async def recover(
        self,
        error: BaseException,
        *,
        partial_stream: bool,
    ) -> RecoveryDirective:
        decision = classify_provider_failure(error, partial_stream=partial_stream)
        if decision.action is RetryAction.REVIEW:
            return RecoveryDirective(
                RecoveryAction.REVIEW,
                self.provider,
                self.attempt_number,
                decision.reason,
            )
        if decision.action is RetryAction.FAIL:
            return RecoveryDirective(
                RecoveryAction.FAIL,
                self.provider,
                self.attempt_number,
                decision.reason,
            )
        if self.attempt_number < self.policy.max_attempts_per_provider:
            delay = self.policy.delay_seconds(
                self.attempt_number,
                retry_after_seconds=decision.retry_after_seconds,
                jitter_value=self._random_value(),
            )
            if not await self._sleep_or_cancel(delay):
                return RecoveryDirective(
                    RecoveryAction.CANCELLED,
                    self.provider,
                    self.attempt_number,
                    decision.reason,
                    delay,
                )
            self.attempt_number += 1
            return RecoveryDirective(
                RecoveryAction.RETRY,
                self.provider,
                self.attempt_number,
                decision.reason,
                delay,
            )
        if self.provider_index + 1 < len(self.providers):
            self.provider_index += 1
            self.attempt_number = 1
            return RecoveryDirective(
                RecoveryAction.FAILOVER,
                self.provider,
                self.attempt_number,
                decision.reason,
            )
        return RecoveryDirective(
            RecoveryAction.FAIL,
            self.provider,
            self.attempt_number,
            decision.reason,
            exhausted=True,
        )

    async def _sleep_or_cancel(self, delay: float) -> bool:
        if self._cancel_event is None:
            await self._sleep(delay)
            return True
        if self._cancel_event.is_set():
            return False
        sleep_task = asyncio.create_task(self._sleep(delay))
        cancel_task = asyncio.create_task(self._cancel_event.wait())
        done, pending = await asyncio.wait(
            {sleep_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancel_task in done and self._cancel_event.is_set():
            return False
        await sleep_task
        return True


_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_PROVIDER_KEYS = (
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("kimi", "MOONSHOT_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
)


def verified_provider_chain(
    environment: Mapping[str, str] | None = None,
) -> tuple[StreamProvider, ...]:
    env = os.environ if environment is None else environment
    reports = {report.provider: report for report in provider_verifications(env)}

    def key_for(provider: str, key_name: str) -> str:
        value = str(env.get(key_name) or "").strip()
        if provider == "kimi" and not value:
            value = str(env.get("KIMI_API_KEY") or "").strip()
        return value

    configured = [
        provider
        for provider, key_name in _PROVIDER_KEYS
        if key_for(provider, key_name)
    ]
    if (
        str(env.get("CLUB_MODEL") or "").strip()
        and configured
        and not reports[configured[0]].eligible
    ):
        return ()

    providers: list[StreamProvider] = []
    for provider_id, key_name in _PROVIDER_KEYS:
        key = key_for(provider_id, key_name)
        report = reports[provider_id]
        if not key or not report.eligible:
            continue
        if provider_id == "deepseek":
            provider = DeepSeekProvider(
                api_key=key,
                model=report.model,
                base_url=str(env.get("DEEPSEEK_BASE_URL") or DEEPSEEK_BASE),
            )
        elif provider_id == "kimi":
            provider = KimiProvider(
                api_key=key,
                model=report.model,
                base_url=str(
                    env.get("KIMI_BASE_URL")
                    or env.get("MOONSHOT_BASE_URL")
                    or KIMI_BASE
                ),
            )
        elif provider_id == "anthropic":
            provider = AnthropicProvider(api_key=key, model=report.model)
        else:
            provider = OpenAICompatProvider(
                api_key=key,
                model=report.model,
                base_url=str(
                    env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
                ),
            )
        providers.append(provider)
    return tuple(providers)


def compatible_failover_chain(
    providers: tuple[Any, ...],
    *,
    selected_provider: Any,
    messages: list[dict[str, Any]],
) -> tuple[Any, ...]:
    missing_reasoning = any(
        message.get("role") == "assistant"
        and "reasoning_content" not in message
        for message in messages
    )
    missing_tool_reasoning = any(
        message.get("role") == "assistant"
        and message.get("tool_calls")
        and "reasoning_content" not in message
        for message in messages
    )
    return tuple(
        provider
        for provider in providers
        if provider is selected_provider
        or not (
            (
                missing_tool_reasoning
                if getattr(provider, "provider_id", "") == "kimi"
                else missing_reasoning
            )
            and getattr(provider, "uses_transient_context", False)
        )
    )


def classify_provider_failure(
    error: BaseException,
    *,
    partial_stream: bool,
) -> RetryDecision:
    reason: RetryReason | None = None
    retryable = False
    retry_after_seconds: float | None = None
    if isinstance(error, ProviderStreamError):
        raw_retry_after = getattr(error, "retry_after_seconds", None)
        if (
            isinstance(raw_retry_after, (int, float))
            and not isinstance(raw_retry_after, bool)
            and math.isfinite(raw_retry_after)
            and raw_retry_after >= 0
        ):
            retry_after_seconds = float(raw_retry_after)
        if error.http_status in _RETRYABLE_STATUS:
            retryable = True
        elif error.http_status is None and error.retryable and error.error_kind in {
            ProviderErrorKind.TIMEOUT,
            ProviderErrorKind.CONNECTION,
            ProviderErrorKind.PROVIDER,
            ProviderErrorKind.RATE_LIMIT,
        }:
            retryable = True
        if error.error_kind is ProviderErrorKind.RATE_LIMIT:
            reason = RetryReason.RATE_LIMIT
        elif error.error_kind is ProviderErrorKind.TIMEOUT:
            reason = RetryReason.TIMEOUT
        elif error.error_kind is ProviderErrorKind.CONNECTION:
            reason = RetryReason.CONNECTION
        elif retryable:
            reason = RetryReason.TRANSIENT_PROVIDER
    elif isinstance(error, (TimeoutError, httpx.TimeoutException)):
        retryable = True
        reason = RetryReason.TIMEOUT
    elif isinstance(error, (ConnectionError, httpx.TransportError)):
        retryable = True
        reason = RetryReason.CONNECTION

    if partial_stream:
        return RetryDecision(RetryAction.REVIEW, reason, retry_after_seconds)
    if not retryable:
        return RetryDecision(RetryAction.FAIL)
    return RetryDecision(RetryAction.RETRY, reason, retry_after_seconds)


def safe_provider_failure_message(
    error: BaseException,
    *,
    review_required: bool = False,
) -> str:
    if review_required:
        return (
            "The model provider stopped after partial output. "
            "Review the partial response before retrying."
        )
    if isinstance(error, ProviderStreamError):
        if error.error_kind is ProviderErrorKind.AUTHENTICATION:
            return "Model provider authentication failed. Review provider setup in Settings."
        if error.error_kind is ProviderErrorKind.RATE_LIMIT:
            return "The model provider remained rate limited after bounded retries."
        if error.error_kind is ProviderErrorKind.CONFIGURATION:
            return "Model provider configuration is unavailable. Review Settings."
        if error.error_kind in {
            ProviderErrorKind.INVALID_REQUEST,
            ProviderErrorKind.PROTOCOL,
        }:
            return "The model provider rejected the request. Review before retrying."
    return "The model provider failed after bounded recovery attempts."


async def cancellable_stream(
    stream: AsyncIterator[Any],
    *,
    cancel_event: asyncio.Event | None,
) -> AsyncIterator[Any]:
    if cancel_event is None:
        async for item in stream:
            yield item
        return
    iterator = stream.__aiter__()
    while True:
        if cancel_event.is_set():
            await iterator.aclose()
            raise ProviderRequestCancelled()
        next_task = asyncio.create_task(anext(iterator))
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait(
            {next_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if cancel_task in done and cancel_event.is_set():
            next_task.cancel()
            await asyncio.gather(next_task, return_exceptions=True)
            await iterator.aclose()
            raise ProviderRequestCancelled()
        try:
            item = next_task.result()
        except StopAsyncIteration:
            return
        yield item
