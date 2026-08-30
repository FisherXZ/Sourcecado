import asyncio
from datetime import UTC, datetime
import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from coworker.inbox import Inbox
from coworker.permissions import Decision
from coworker.provider import (
    KimiProvider,
    OpenAICompatProvider,
    ProviderErrorKind,
    ProviderStreamError,
)
from coworker.provider import StreamChunk, ToolCall
from coworker import provider as provider_module
from coworker.store import ConversationStore
from coworker.server import TOKEN_HEADER, create_app
from coworker.telemetry import InMemoryTelemetryAdapter, TelemetryRecorder
from coworker.telemetry.schema import RetryEvent, SpanEventRecord
from coworker.turn import RunControl, new_turn_identity, run_turn


def _error(
    kind: ProviderErrorKind,
    *,
    status: int | None = None,
    retryable: bool = False,
) -> ProviderStreamError:
    return ProviderStreamError(
        provider="deepseek",
        model="deepseek-v4-pro",
        kind=kind,
        message="safe provider failure",
        retryable=retryable,
        http_status=status,
    )


@pytest.mark.parametrize(
    ("error", "expected_action", "expected_reason"),
    [
        (_error(ProviderErrorKind.TIMEOUT, status=408, retryable=True), "retry", "timeout"),
        (_error(ProviderErrorKind.INVALID_REQUEST, status=409, retryable=True), "retry", "transient_provider"),
        (_error(ProviderErrorKind.RATE_LIMIT, status=429, retryable=True), "retry", "rate_limit"),
        (_error(ProviderErrorKind.PROVIDER, status=500, retryable=True), "retry", "transient_provider"),
        (_error(ProviderErrorKind.PROVIDER, status=502, retryable=True), "retry", "transient_provider"),
        (_error(ProviderErrorKind.PROVIDER, status=503, retryable=True), "retry", "transient_provider"),
        (_error(ProviderErrorKind.PROVIDER, status=504, retryable=True), "retry", "transient_provider"),
        (_error(ProviderErrorKind.AUTHENTICATION, status=401), "fail", None),
        (_error(ProviderErrorKind.INVALID_REQUEST, status=400), "fail", None),
        (_error(ProviderErrorKind.PROTOCOL), "fail", None),
        (_error(ProviderErrorKind.CONFIGURATION), "fail", None),
        (_error(ProviderErrorKind.PROVIDER, status=501, retryable=True), "fail", None),
    ],
)
def test_provider_failure_classification_is_bounded(
    error, expected_action, expected_reason
):
    retry = importlib.import_module("coworker.provider_retry")

    decision = retry.classify_provider_failure(error, partial_stream=False)

    assert decision.action.value == expected_action
    assert (
        decision.reason.value if decision.reason is not None else None
    ) == expected_reason


def test_any_failure_after_meaningful_stream_requires_review():
    retry = importlib.import_module("coworker.provider_retry")
    error = _error(ProviderErrorKind.AUTHENTICATION, status=401)

    decision = retry.classify_provider_failure(error, partial_stream=True)

    assert decision.action.value == "review"
    assert decision.reason is None


def test_retry_policy_bounds_retry_after_and_exponential_jitter():
    retry = importlib.import_module("coworker.provider_retry")
    policy = retry.RetryPolicy(
        max_attempts_per_provider=3,
        base_delay_seconds=0.5,
        max_delay_seconds=4.0,
        max_retry_after_seconds=30.0,
        jitter_ratio=0.2,
    )

    assert policy.delay_seconds(1, retry_after_seconds=10, jitter_value=0.0) == 10
    assert policy.delay_seconds(1, retry_after_seconds=99, jitter_value=1.0) == 30
    assert policy.delay_seconds(1, retry_after_seconds=None, jitter_value=0.5) == 0.5
    assert policy.delay_seconds(2, retry_after_seconds=None, jitter_value=0.5) == 1.0
    assert policy.delay_seconds(1, retry_after_seconds=None, jitter_value=0.0) == 0.4
    assert policy.delay_seconds(1, retry_after_seconds=None, jitter_value=1.0) == 0.6
    assert policy.delay_seconds(9, retry_after_seconds=None, jitter_value=1.0) == 4.0


def test_retry_decision_carries_provider_retry_after_hint():
    retry = importlib.import_module("coworker.provider_retry")
    error = _error(ProviderErrorKind.RATE_LIMIT, status=429, retryable=True)
    error.retry_after_seconds = 12.0

    decision = retry.classify_provider_failure(error, partial_stream=False)

    assert decision.retry_after_seconds == 12.0


def test_openai_provider_captures_numeric_retry_after_without_body(monkeypatch):
    async def handler(request):
        return httpx.Response(
            429,
            headers={"Retry-After": "7"},
            text="PRIVATE_PROVIDER_BODY",
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = OpenAICompatProvider(api_key="secret", model="gpt-4o-mini")

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]

    with pytest.raises(ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value.retry_after_seconds == 7.0
    assert "PRIVATE_PROVIDER_BODY" not in str(captured.value)


def test_retry_after_accepts_http_date_with_deterministic_utc_clock():
    now = datetime(2037, 10, 21, 7, 27, tzinfo=UTC)

    assert provider_module._retry_after_seconds(
        {"retry-after": "Wed, 21 Oct 2037 07:28:00 GMT"},
        now=now,
    ) == 60.0
    assert provider_module._retry_after_seconds(
        {"retry-after": "Wed, 21 Oct 2037 07:26:00 GMT"},
        now=now,
    ) == 0.0
    assert provider_module._retry_after_seconds(
        {"retry-after": "not-a-date"},
        now=now,
    ) is None


def test_failover_chain_contains_only_verified_configured_providers():
    retry = importlib.import_module("coworker.provider_retry")

    providers = retry.verified_provider_chain(
        {
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "MOONSHOT_API_KEY": "kimi-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )

    assert [(provider.provider_id, provider.model_id) for provider in providers] == [
        ("deepseek", "deepseek-v4-pro"),
        ("kimi", "kimi-k3"),
        ("anthropic", "claude-sonnet-4-6"),
        ("openai", "gpt-4o-mini"),
    ]


def test_app_composition_uses_verified_provider_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CLUB_MODEL", raising=False)

    app = create_app(token="token", state=tmp_path)

    assert app.state.provider.provider_id == "deepseek"
    assert [provider.provider_id for provider in app.state.provider_failovers] == [
        "kimi"
    ]


def test_invalid_explicit_primary_model_produces_no_failover_chain():
    retry = importlib.import_module("coworker.provider_retry")

    providers = retry.verified_provider_chain(
        {
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "MOONSHOT_API_KEY": "kimi-secret",
            "CLUB_MODEL": "not-a-deepseek-model",
        }
    )

    assert providers == ()


def test_failover_candidates_skip_reasoning_provider_without_durable_continuity():
    retry = importlib.import_module("coworker.provider_retry")

    class Provider:
        def __init__(self, provider_id, transient=False):
            self.provider_id = provider_id
            self.model_id = f"{provider_id}-model"
            self.uses_transient_context = transient

    selected = Provider("deepseek", transient=True)
    kimi = Provider("kimi", transient=True)
    anthropic = Provider("anthropic")
    history = [
        {"role": "user", "content": "perform a tool"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "now", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]

    candidates = retry.compatible_failover_chain(
        (selected, kimi, anthropic),
        selected_provider=selected,
        messages=history,
    )

    assert [provider.provider_id for provider in candidates] == [
        "deepseek",
        "anthropic",
    ]


def test_kimi_failover_remains_eligible_after_plain_assistant_history():
    retry = importlib.import_module("coworker.provider_retry")

    class Provider:
        def __init__(self, provider_id, transient=False):
            self.provider_id = provider_id
            self.model_id = f"{provider_id}-model"
            self.uses_transient_context = transient

    selected = Provider("deepseek", transient=True)
    kimi = Provider("kimi", transient=True)
    anthropic = Provider("anthropic")
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hello back"},
        {"role": "user", "content": "continue"},
    ]

    candidates = retry.compatible_failover_chain(
        (selected, kimi, anthropic),
        selected_provider=selected,
        messages=history,
    )

    assert [provider.provider_id for provider in candidates] == [
        "deepseek",
        "kimi",
        "anthropic",
    ]


def test_kimi_failover_accepts_tool_history_with_retained_reasoning():
    retry = importlib.import_module("coworker.provider_retry")
    selected = OpenAICompatProvider(api_key="active", model="gpt-4o-mini")
    kimi = KimiProvider(api_key="fallback", model="kimi-k3")
    history = [
        {"role": "user", "content": "perform a tool"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "retained reasoning",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "now", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
    ]

    candidates = retry.compatible_failover_chain(
        (selected, kimi),
        selected_provider=selected,
        messages=history,
    )
    prepared = kimi._prepare_messages(
        context_id=None,
        messages=history,
        tools=[{"type": "function", "function": {"name": "now"}}],
    )

    assert candidates == (selected, kimi)
    assert prepared[1]["reasoning_content"] == "retained reasoning"


def test_kimi_fallback_context_tolerates_plain_history_and_caches_own_tool_reasoning():
    kimi = KimiProvider(api_key="fallback", model="kimi-k3")
    sid = "kimi-fallback-context"
    prior = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "plain answer from another provider"},
        {"role": "user", "content": "use a tool"},
    ]
    tools = [{"type": "function", "function": {"name": "now"}}]

    first_prepared = kimi._prepare_messages(
        context_id=sid,
        messages=prior,
        tools=tools,
    )
    call = ToolCall(id="call_kimi", name="now", arguments={})
    kimi._record_completed_response(
        context_id=sid,
        messages=prior,
        tools=tools,
        reasoning="kimi private reasoning",
        content="",
        calls=[call],
        finish_reason="tool_calls",
    )
    continuation = [
        *prior,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": call.id, "content": "{}"},
    ]
    second_prepared = kimi._prepare_messages(
        context_id=sid,
        messages=continuation,
        tools=tools,
    )

    assert "reasoning_content" not in first_prepared[1]
    assert second_prepared[-2]["reasoning_content"] == "kimi private reasoning"
    assert "reasoning_content" not in continuation[-2]


def test_kimi_cross_provider_failover_uses_retained_history_path(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")

    class ActiveProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        async def astream(self, *, messages, tools, context_id=None):
            raise _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
            yield

    class KimiFallback:
        provider_id = "kimi"
        model_id = "kimi-k3"
        uses_transient_context = True

        def __init__(self):
            self.context_ids = []

        async def astream(self, *, messages, tools, context_id=None):
            self.context_ids.append(context_id)
            if context_id is None:
                raise RuntimeError("fresh Kimi fallback did not receive a local cache id")
            yield StreamChunk(text_delta="kimi continued")
            yield StreamChunk(finish_reason="stop")

    store = ConversationStore(tmp_path)
    sid = "plain-history-failover"
    store.create_session(sid)
    store.append(sid, {"role": "user", "content": "hello"})
    store.append(sid, {"role": "assistant", "content": "hello back"})
    fallback = KimiFallback()

    result = asyncio.run(
        run_turn(
            text="continue",
            sid=sid,
            store=store,
            provider=ActiveProvider(),
            failover_providers=(fallback,),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
        )
    )

    assert result == {"status": "ok", "text": "kimi continued"}
    assert fallback.context_ids == [sid]


def test_actual_kimi_failover_can_call_tool_and_continue_with_cached_reasoning(
    tmp_path, monkeypatch
):
    retry = importlib.import_module("coworker.provider_retry")
    requests = []

    def sse(*payloads):
        return "".join(
            f"data: {json.dumps(payload)}\n\n" for payload in payloads
        ) + "data: [DONE]\n\n"

    async def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                text=sse(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning_content": "kimi own reasoning"},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_kimi_now",
                                            "type": "function",
                                            "function": {
                                                "name": "now",
                                                "arguments": "{}",
                                            },
                                        }
                                    ]
                                },
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                        ]
                    },
                ),
            )
        return httpx.Response(
            200,
            text=sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "kimi finished"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    executions = []

    def execute_now(name, arguments, **kwargs):
        executions.append(name)
        return True, {"time": "now"}

    monkeypatch.setattr("coworker.turn.execute", execute_now)

    class FailingPrimary:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        async def astream(self, *, messages, tools, context_id=None):
            raise _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
            yield

    store = ConversationStore(tmp_path)
    sid = "kimi-tool-failover"
    store.create_session(sid)
    store.append(sid, {"role": "user", "content": "hello"})
    store.append(sid, {"role": "assistant", "content": "plain prior answer"})
    result = asyncio.run(
        run_turn(
            text="use now",
            sid=sid,
            store=store,
            provider=FailingPrimary(),
            failover_providers=(
                KimiProvider(api_key="kimi-secret", model="kimi-k3"),
            ),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[{"type": "function", "function": {"name": "now"}}],
            execute_kwargs={},
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
        )
    )

    tool_assistant = next(
        message
        for message in requests[1]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert result == {"status": "ok", "text": "kimi finished"}
    assert executions == ["now"]
    assert len(requests) == 2
    assert tool_assistant["reasoning_content"] == "kimi own reasoning"


def test_retry_controller_exhausts_active_attempts_before_failover():
    retry = importlib.import_module("coworker.provider_retry")

    class Provider:
        def __init__(self, provider_id):
            self.provider_id = provider_id
            self.model_id = f"{provider_id}-model"

    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)

    controller = retry.RetryController(
        (Provider("primary"), Provider("fallback")),
        policy=retry.RetryPolicy(
            max_attempts_per_provider=2,
            base_delay_seconds=0.5,
            max_delay_seconds=4,
            max_retry_after_seconds=30,
            jitter_ratio=0,
        ),
        sleep=sleep,
        random_value=lambda: 0.5,
    )
    error = _error(ProviderErrorKind.RATE_LIMIT, status=429, retryable=True)

    first = asyncio.run(controller.recover(error, partial_stream=False))
    second = asyncio.run(controller.recover(error, partial_stream=False))

    assert first.action.value == "retry"
    assert first.provider.provider_id == "primary"
    assert first.attempt_number == 2
    assert first.delay_seconds == 0.5
    assert second.action.value == "failover"
    assert second.provider.provider_id == "fallback"
    assert second.attempt_number == 1
    assert second.delay_seconds == 0
    assert sleeps == [0.5]


def test_retry_controller_fails_over_on_nonretryable_provider_incompatibility():
    retry = importlib.import_module("coworker.provider_retry")

    class Provider:
        def __init__(self, provider_id):
            self.provider_id = provider_id
            self.model_id = f"{provider_id}-model"

    async def no_sleep(_delay):
        return None

    controller = retry.RetryController(
        (Provider("deepseek"), Provider("openai")),
        policy=retry.RetryPolicy(max_attempts_per_provider=1),
        sleep=no_sleep,
    )

    failover = asyncio.run(
        controller.recover(
            RuntimeError("provider adapter rejected retained history"),
            partial_stream=False,
        )
    )
    exhausted = asyncio.run(
        controller.recover(
            RuntimeError("fallback adapter also rejected retained history"),
            partial_stream=False,
        )
    )

    assert failover.action is retry.RecoveryAction.FAILOVER
    assert failover.provider.provider_id == "openai"
    assert failover.reason.value == "provider_incompatible"
    assert exhausted.action is retry.RecoveryAction.FAIL
    assert exhausted.exhausted is True


def test_retry_controller_cancels_during_backoff_without_next_attempt():
    retry = importlib.import_module("coworker.provider_retry")

    class Provider:
        provider_id = "primary"
        model_id = "primary-model"

    async def scenario():
        sleep_started = asyncio.Event()
        cancel = asyncio.Event()

        async def blocked_sleep(delay):
            sleep_started.set()
            await asyncio.Event().wait()

        controller = retry.RetryController(
            (Provider(),),
            policy=retry.RetryPolicy(max_attempts_per_provider=2),
            sleep=blocked_sleep,
            cancel_event=cancel,
        )
        error = _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
        task = asyncio.create_task(controller.recover(error, partial_stream=False))
        await asyncio.wait_for(sleep_started.wait(), timeout=1)
        cancel.set()
        directive = await asyncio.wait_for(task, timeout=1)
        return controller, directive

    controller, directive = asyncio.run(scenario())

    assert directive.action.value == "cancelled"
    assert directive.attempt_number == 1
    assert controller.attempt_number == 1


def test_run_turn_retries_one_model_request_before_success(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")

    class RetryThenSuccess:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools, context_id=None):
            self.attempts += 1
            if self.attempts == 1:
                raise _error(
                    ProviderErrorKind.TIMEOUT,
                    status=408,
                    retryable=True,
                )
            yield StreamChunk(text_delta="recovered")
            yield StreamChunk(finish_reason="stop")

    async def no_sleep(delay):
        return None

    provider = RetryThenSuccess()
    store = ConversationStore(tmp_path)
    sid = "retry-session"
    store.create_session(sid)
    adapter = InMemoryTelemetryAdapter()
    recorder = TelemetryRecorder(adapter)

    result = asyncio.run(
        run_turn(
            text="hello",
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            telemetry=recorder,
            retry_policy=retry.RetryPolicy(
                max_attempts_per_provider=2,
                base_delay_seconds=0,
                jitter_ratio=0,
            ),
            retry_sleep=no_sleep,
        )
    )

    retries = [
        record.event
        for record in adapter.records
        if isinstance(record, SpanEventRecord)
        and isinstance(record.event, RetryEvent)
    ]
    assert result == {"status": "ok", "text": "recovered"}
    assert provider.attempts == 2
    assert len(retries) == 1
    assert retries[0].reason.value == "timeout"
    assert retries[0].delay_ms == 0


def test_run_turn_fails_over_after_active_provider_attempts_exhaust(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")

    class FailingProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools, context_id=None):
            self.attempts += 1
            raise _error(
                ProviderErrorKind.CONNECTION,
                retryable=True,
            )
            yield

    class FallbackProvider:
        provider_id = "openai"
        model_id = "gpt-4o-mini"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools):
            self.attempts += 1
            yield StreamChunk(text_delta="fallback answer")
            yield StreamChunk(finish_reason="stop")

    async def no_sleep(delay):
        return None

    active = FailingProvider()
    fallback = FallbackProvider()
    store = ConversationStore(tmp_path)
    sid = "failover-session"
    store.create_session(sid)

    result = asyncio.run(
        run_turn(
            text="hello",
            sid=sid,
            store=store,
            provider=active,
            failover_providers=(fallback,),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
            retry_sleep=no_sleep,
        )
    )

    assert result == {"status": "ok", "text": "fallback answer"}
    assert active.attempts == 1
    assert fallback.attempts == 1


@pytest.mark.parametrize(
    ("tool_name", "arguments", "tool_result"),
    [
        ("now", {}, {"time": "now"}),
        (
            "fs_write",
            {"grant_id": "grant-1", "path": "notes.txt", "content": "written"},
            {"status": "written", "path": "notes.txt"},
        ),
    ],
)
def test_failover_continues_from_completed_tool_result_without_reexecution(
    tmp_path, monkeypatch, tool_name, arguments, tool_result
):
    retry = importlib.import_module("coworker.provider_retry")

    class ActiveProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools, context_id=None):
            self.attempts += 1
            if self.attempts == 1:
                yield StreamChunk(
                    tool_calls=[
                        ToolCall(
                            id="call_effect",
                            name=tool_name,
                            arguments=arguments,
                        )
                    ]
                )
                yield StreamChunk(finish_reason="tool_calls")
                return
            raise _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
            yield

    class FallbackProvider:
        provider_id = "openai"
        model_id = "gpt-4o-mini"

        def __init__(self):
            self.calls = []

        async def astream(self, *, messages, tools):
            self.calls.append(messages)
            yield StreamChunk(text_delta="continued safely")
            yield StreamChunk(finish_reason="stop")

    executions = []

    def execute_once(name, arguments, **kwargs):
        executions.append((name, arguments))
        return True, tool_result

    monkeypatch.setattr("coworker.turn.execute", execute_once)
    monkeypatch.setattr(
        "coworker.turn.decide",
        lambda *args, **kwargs: Decision(True, False, "test-authorized"),
    )
    active = ActiveProvider()
    fallback = FallbackProvider()
    store = ConversationStore(tmp_path)
    sid = "tool-failover-session"
    store.create_session(sid)

    result = asyncio.run(
        run_turn(
            text="perform one effect",
            sid=sid,
            store=store,
            provider=active,
            failover_providers=(fallback,),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[
                {"type": "function", "function": {"name": tool_name}}
            ],
            execute_kwargs={},
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
        )
    )

    assert result == {"status": "ok", "text": "continued safely"}
    assert executions == [(tool_name, arguments)]
    assert active.attempts == 2
    assert len(fallback.calls) == 1
    continued = fallback.calls[0]
    assert [message["role"] for message in continued[-2:]] == ["assistant", "tool"]
    assert continued[-1]["tool_call_id"] == "call_effect"
    assert json.loads(continued[-1]["content"]) == tool_result


def test_failover_does_not_repeat_completed_approval_or_send(tmp_path, monkeypatch):
    retry = importlib.import_module("coworker.provider_retry")

    class ActiveProvider:
        provider_id = "openai"
        model_id = "gpt-4o-mini"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools):
            self.attempts += 1
            if self.attempts == 1:
                yield StreamChunk(
                    tool_calls=[
                        ToolCall(
                            id="call_send",
                            name="gmail_send",
                            arguments={"draft_id": "draft-1"},
                        )
                    ]
                )
                yield StreamChunk(finish_reason="tool_calls")
                return
            raise _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
            yield

    class FallbackProvider:
        provider_id = "anthropic"
        model_id = "claude-sonnet-4-6"

        async def astream(self, *, messages, tools):
            yield StreamChunk(text_delta="send already completed")
            yield StreamChunk(finish_reason="stop")

    approvals = []
    sends = []

    async def approve(call_id):
        approvals.append(call_id)
        return "allow"

    def execute_send(name, arguments, **kwargs):
        sends.append((name, arguments, kwargs["approval_granted"]))
        return True, {"message_id": "sent-1", "status": "sent"}

    monkeypatch.setattr("coworker.turn.execute", execute_send)
    store = ConversationStore(tmp_path)
    sid = "approval-failover"
    store.create_session(sid)
    result = asyncio.run(
        run_turn(
            text="send it",
            sid=sid,
            store=store,
            provider=ActiveProvider(),
            failover_providers=(FallbackProvider(),),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[
                {"type": "function", "function": {"name": "gmail_send"}}
            ],
            execute_kwargs={},
            wait_permission=approve,
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
        )
    )

    assert result == {"status": "ok", "text": "send already completed"}
    assert approvals == ["call_send"]
    assert sends == [("gmail_send", {"draft_id": "draft-1"}, True)]


def test_final_provider_failure_emits_safe_message_without_raw_body(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")
    planted = "PRIVATE_PROVIDER_BODY sk-PLANTED"

    class FailingProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        async def astream(self, *, messages, tools, context_id=None):
            raise ProviderStreamError(
                provider=self.provider_id,
                model=self.model_id,
                kind=ProviderErrorKind.AUTHENTICATION,
                message=planted,
                retryable=False,
                http_status=401,
            )
            yield

    emitted = []

    async def emit(event):
        emitted.append(event)

    store = ConversationStore(tmp_path)
    sid = "safe-final-failure"
    store.create_session(sid)
    result = asyncio.run(
        run_turn(
            text="hello",
            sid=sid,
            store=store,
            provider=FailingProvider(),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            emit=emit,
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
        )
    )

    terminal = next(event for event in emitted if event["type"] == "error")
    assert result["status"] == "error"
    assert planted not in terminal["message"]
    assert "authentication" in terminal["message"].lower()


def test_exhausted_provider_chain_is_bounded_and_telemetried(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")

    class AlwaysTimeout:
        def __init__(self, provider_id):
            self.provider_id = provider_id
            self.model_id = f"{provider_id}-model"
            self.attempts = 0

        async def astream(self, *, messages, tools):
            self.attempts += 1
            raise _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
            yield

    async def no_sleep(delay):
        return None

    active = AlwaysTimeout("openai")
    fallback = AlwaysTimeout("anthropic")
    store = ConversationStore(tmp_path)
    sid = "exhausted-chain"
    store.create_session(sid)
    adapter = InMemoryTelemetryAdapter()
    result = asyncio.run(
        run_turn(
            text="hello",
            sid=sid,
            store=store,
            provider=active,
            failover_providers=(fallback,),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            telemetry=TelemetryRecorder(adapter),
            retry_policy=retry.RetryPolicy(
                max_attempts_per_provider=2,
                base_delay_seconds=0,
            ),
            retry_sleep=no_sleep,
        )
    )

    retry_events = [
        record.event
        for record in adapter.records
        if isinstance(record, SpanEventRecord)
        and isinstance(record.event, RetryEvent)
    ]
    assert result["status"] == "error"
    assert active.attempts == 2
    assert fallback.attempts == 2
    assert [event.retry_count for event in retry_events] == [1, 2, 3]
    assert all(event.reason.value == "timeout" for event in retry_events)


def test_recovered_websocket_turn_reports_retry_without_provider_body(tmp_path):
    planted = "PRIVATE_RATE_LIMIT_BODY sk-PLANTED"

    class RecoveringProvider:
        provider_id = "openai"
        model_id = "gpt-4o-mini"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools):
            self.attempts += 1
            if self.attempts == 1:
                raise ProviderStreamError(
                    provider=self.provider_id,
                    model=self.model_id,
                    kind=ProviderErrorKind.RATE_LIMIT,
                    message=planted,
                    retryable=True,
                    http_status=429,
                )
            yield StreamChunk(text_delta="recovered answer")
            yield StreamChunk(finish_reason="stop")

    provider = RecoveringProvider()
    app = create_app(token="token", provider=provider, state=tmp_path)
    client = TestClient(app)
    sid = app.state.store.open_session_id()
    with client.websocket_connect("/ws/chat", subprotocols=["club", "token"]) as ws:
        ws.send_json({"type": "chat", "text": "hello", "session_id": sid})
        events = []
        while not events or events[-1]["type"] not in {"turn_end", "error"}:
            events.append(ws.receive_json())

    metrics = client.get(
        f"/v1/sessions/{sid}/telemetry/current",
        headers={TOKEN_HEADER: "token"},
    ).json()["current_run"]
    encoded = json.dumps({"events": events, "metrics": metrics})
    recovery = next(event for event in events if event["type"] == "provider_recovery")
    assert events[-1]["type"] == "turn_end"
    assert events[-1]["text"] == "recovered answer"
    assert recovery == {
        **{
            key: recovery[key]
            for key in (
                "version",
                "type",
                "session_id",
                "run_id",
                "event_id",
                "message_id",
                "part_id",
            )
        },
        "action": "retry",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "attempt": 2,
        "reason": "rate_limit",
        "delay_ms": recovery["delay_ms"],
        "message": "Retrying the model provider after a temporary failure.",
    }
    assert 0 <= recovery["delay_ms"] <= 4_000
    assert metrics["retry_count"] == 1
    assert provider.attempts == 2
    assert planted not in encoded


def test_partial_stream_requires_review_without_retry_or_failover(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")

    class PartialProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools, context_id=None):
            self.attempts += 1
            yield StreamChunk(text_delta="partial answer")
            raise _error(ProviderErrorKind.RATE_LIMIT, status=429, retryable=True)

    class FallbackProvider:
        provider_id = "openai"
        model_id = "gpt-4o-mini"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools):
            self.attempts += 1
            yield StreamChunk(text_delta="must not concatenate")

    emitted = []

    async def emit(event):
        emitted.append(event)

    active = PartialProvider()
    fallback = FallbackProvider()
    store = ConversationStore(tmp_path)
    sid = "partial-review"
    store.create_session(sid)
    result = asyncio.run(
        run_turn(
            text="hello",
            sid=sid,
            store=store,
            provider=active,
            failover_providers=(fallback,),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            emit=emit,
        )
    )

    terminal = next(event for event in emitted if event["type"] == "error")
    assert result["status"] == "error"
    assert active.attempts == 1
    assert fallback.attempts == 0
    assert [
        event.get("delta")
        for event in emitted
        if event["type"] == "assistant_delta"
    ] == ["partial answer"]
    assert "review" in terminal["message"].lower()
    assert "must not concatenate" not in str(emitted)


@pytest.mark.parametrize(
    "failure",
    [
        _error(ProviderErrorKind.RATE_LIMIT, status=429, retryable=True),
        TimeoutError(),
    ],
    ids=["rate_limit", "timeout"],
)
def test_hidden_reasoning_still_retries_and_fails_over(tmp_path, failure):
    retry = importlib.import_module("coworker.provider_retry")

    class HiddenReasoningThenFailure:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools, context_id=None):
            self.attempts += 1
            yield StreamChunk.started(
                provider=self.provider_id,
                model=self.model_id,
            )
            yield StreamChunk(transient_reasoning_delta="hidden plan")
            raise failure

    class FallbackProvider:
        provider_id = "openai"
        model_id = "gpt-4o-mini"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools):
            self.attempts += 1
            yield StreamChunk(text_delta="fallback answer")
            yield StreamChunk(finish_reason="stop")

    async def no_sleep(delay):
        return None

    emitted = []

    async def emit(event):
        emitted.append(event)

    active = HiddenReasoningThenFailure()
    fallback = FallbackProvider()
    store = ConversationStore(tmp_path)
    sid = "hidden-reasoning-failover"
    store.create_session(sid)
    result = asyncio.run(
        run_turn(
            text="hello",
            sid=sid,
            store=store,
            provider=active,
            failover_providers=(fallback,),
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[],
            execute_kwargs={},
            emit=emit,
            retry_policy=retry.RetryPolicy(max_attempts_per_provider=1),
            retry_sleep=no_sleep,
        )
    )

    assert result == {"status": "ok", "text": "fallback answer"}
    assert active.attempts == 1
    assert fallback.attempts == 1
    assert [
        event.get("delta")
        for event in emitted
        if event["type"] == "assistant_delta"
    ] == ["fallback answer"]
    assert [event["type"] for event in emitted if event["type"] == "error"] == []
    assert "hidden plan" not in str(emitted)
    assert "Review the partial response" not in str(emitted)


def test_cancellable_stream_interrupts_blocked_provider_request():
    retry = importlib.import_module("coworker.provider_retry")
    assert hasattr(retry, "cancellable_stream")

    async def scenario():
        started = asyncio.Event()
        cancel = asyncio.Event()
        closed = asyncio.Event()

        async def blocked_stream():
            try:
                started.set()
                await asyncio.Event().wait()
                yield StreamChunk(text_delta="late")
            finally:
                closed.set()

        async def consume():
            return [
                event
                async for event in retry.cancellable_stream(
                    blocked_stream(),
                    cancel_event=cancel,
                )
            ]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=1)
        cancel.set()
        with pytest.raises(retry.ProviderRequestCancelled):
            await asyncio.wait_for(task, timeout=1)
        await asyncio.wait_for(closed.wait(), timeout=1)

    asyncio.run(scenario())


def test_run_turn_cancels_while_provider_request_is_blocked(tmp_path):
    class BlockedProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.started = asyncio.Event()
            self.closed = asyncio.Event()

        async def astream(self, *, messages, tools, context_id=None):
            try:
                self.started.set()
                await asyncio.Event().wait()
                yield StreamChunk(text_delta="late")
            finally:
                self.closed.set()

    async def scenario():
        provider = BlockedProvider()
        store = ConversationStore(tmp_path)
        sid = "blocked-request"
        store.create_session(sid)
        control = RunControl(new_turn_identity(sid))
        task = asyncio.create_task(
            run_turn(
                text="hello",
                sid=sid,
                store=store,
                provider=provider,
                persona=None,
                skills=None,
                inbox=Inbox(store),
                openai_tools=[],
                execute_kwargs={},
                identity=control.identity,
                control=control,
            )
        )
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        control.cancel_requested.set()
        result = await asyncio.wait_for(task, timeout=1)
        await asyncio.wait_for(provider.closed.wait(), timeout=1)
        return result

    assert asyncio.run(scenario()) == {"status": "stopped", "text": ""}


def test_run_turn_cancels_during_retry_backoff(tmp_path):
    retry = importlib.import_module("coworker.provider_retry")

    class FailingProvider:
        provider_id = "deepseek"
        model_id = "deepseek-v4-pro"

        def __init__(self):
            self.attempts = 0

        async def astream(self, *, messages, tools, context_id=None):
            self.attempts += 1
            raise _error(ProviderErrorKind.TIMEOUT, status=408, retryable=True)
            yield

    async def scenario():
        sleep_started = asyncio.Event()

        async def blocked_sleep(delay):
            sleep_started.set()
            await asyncio.Event().wait()

        provider = FailingProvider()
        store = ConversationStore(tmp_path)
        sid = "backoff-cancel"
        store.create_session(sid)
        control = RunControl(new_turn_identity(sid))
        task = asyncio.create_task(
            run_turn(
                text="hello",
                sid=sid,
                store=store,
                provider=provider,
                persona=None,
                skills=None,
                inbox=Inbox(store),
                openai_tools=[],
                execute_kwargs={},
                identity=control.identity,
                control=control,
                retry_policy=retry.RetryPolicy(max_attempts_per_provider=2),
                retry_sleep=blocked_sleep,
            )
        )
        await asyncio.wait_for(sleep_started.wait(), timeout=1)
        control.cancel_requested.set()
        result = await asyncio.wait_for(task, timeout=1)
        return provider, result

    provider, result = asyncio.run(scenario())

    assert result == {"status": "stopped", "text": ""}
    assert provider.attempts == 1
