import asyncio
import copy
import json

import httpx
import pytest

from coworker import provider as provider_module
from coworker.workspace_runtime import WORKSPACE_TOOL_SCHEMAS


def _sse(*payloads):
    return (
        "".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads)
        + "data: [DONE]\n\n"
    )


def _install_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def test_stream_chunks_use_one_closed_lifecycle_vocabulary():
    assert {kind.value for kind in provider_module.StreamKind} == {
        "start",
        "text",
        "reasoning",
        "tool_delta",
        "tool_calls",
        "usage",
        "terminal",
        "error",
    }
    assert (
        provider_module.StreamChunk(text_delta="hello").kind
        is provider_module.StreamKind.TEXT
    )


def test_start_and_error_are_typed_content_free_lifecycle_values():
    started = provider_module.StreamChunk.started(
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    failure = provider_module.ProviderStreamError(
        provider="anthropic",
        model="claude-sonnet-4-6",
        kind=provider_module.ProviderErrorKind.RATE_LIMIT,
        message="provider request was rate limited",
        retryable=True,
        http_status=429,
    )

    assert started.kind is provider_module.StreamKind.START
    assert started.start == provider_module.ProviderStart(
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    assert failure.kind is provider_module.StreamKind.ERROR
    assert failure.retryable is True
    assert failure.http_status == 429
    assert str(failure) == "anthropic provider request was rate limited"
    assert not hasattr(failure, "response_body")


def test_stream_chunk_rejects_ambiguous_mixed_payloads():
    usage = provider_module.ModelUsage(1, 1, 2, 0, 1, 0)

    with pytest.raises(ValueError, match="exactly one lifecycle payload"):
        provider_module.StreamChunk(text_delta="answer", usage=usage)


def test_model_usage_rejects_negative_or_inconsistent_counts():
    with pytest.raises(ValueError, match="non-negative"):
        provider_module.ModelUsage(-1, 1, 0, 0, 0, 0)

    with pytest.raises(ValueError, match="cached.*uncached"):
        provider_module.ModelUsage(10, 2, 12, 8, 3, 0)


def test_provider_verification_reports_eligibility_without_credentials():
    reports = provider_module.provider_verifications(
        {
            "DEEPSEEK_API_KEY": "deepseek-private-key",
            "MOONSHOT_BASE_URL": "https://api.moonshot.ai/v1",
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": " ",
        }
    )

    assert [report.provider for report in reports] == [
        "deepseek",
        "kimi",
        "anthropic",
        "openai",
    ]
    assert reports[0].eligible is True
    assert reports[0].model == "deepseek-v4-pro"
    assert reports[0].failures == ()
    assert reports[0].capabilities.tool_calling is True
    assert reports[1].eligible is False
    assert reports[1].failures == ("missing_api_key",)
    assert reports[2].failures == ("missing_api_key",)
    assert reports[3].failures == ("missing_api_key",)
    assert "deepseek-private-key" not in repr(reports)


def test_all_fully_configured_advertised_providers_are_eligible():
    reports = provider_module.provider_verifications(
        {
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "MOONSHOT_API_KEY": "kimi-secret",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )

    assert [report.eligible for report in reports] == [True, True, True, True]
    assert [report.failures for report in reports] == [(), (), (), ()]
    assert all(report.capabilities.tool_calling for report in reports)
    assert all(report.capabilities.terminal_usage for report in reports)
    assert [report.capabilities.reasoning_usage for report in reports] == [
        True,
        False,
        False,
        True,
    ]


def test_unsupported_configured_model_is_rejected_before_provider_creation(
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setenv("CLUB_MODEL", "not-a-deepseek-model")
    for key in (
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    assert provider_module.provider_from_env() is None
    assert provider_module.default_model_id() is None


@pytest.mark.parametrize(
    ("key_name", "bad_model"),
    [
        ("MOONSHOT_API_KEY", "deepseek-v4-pro"),
        ("ANTHROPIC_API_KEY", "kimi-k3"),
    ],
)
def test_vendor_provider_rejects_model_from_another_backend(
    key_name, bad_model, monkeypatch
):
    for key in (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(key_name, "provider-secret")
    monkeypatch.setenv("CLUB_MODEL", bad_model)

    assert provider_module.provider_from_env() is None


def test_partial_generic_provider_config_rejects_unsafe_base_url():
    report = next(
        report
        for report in provider_module.provider_verifications(
            {
                "OPENAI_API_KEY": "openai-secret",
                "OPENAI_BASE_URL": "http://provider.example.test/v1",
            }
        )
        if report.provider == "openai"
    )

    assert report.eligible is False
    assert report.failures == ("invalid_base_url",)


def test_invalid_primary_provider_is_not_selected_over_verified_fallback(
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-secret")
    monkeypatch.setenv("CLUB_MODEL", "not-a-deepseek-model")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider = provider_module.provider_from_env()

    assert isinstance(provider, provider_module.KimiProvider)
    assert provider.model_id == "kimi-k3"


def test_deepseek_stream_starts_with_provider_and_model_identity(monkeypatch):
    async def handler(request):
        terminal = {
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            },
        }
        return httpx.Response(
            200,
            text=f"data: {json.dumps(terminal)}\n\ndata: [DONE]\n\n",
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = provider_module.DeepSeekProvider(
        api_key="secret",
        model="deepseek-v4-pro",
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]

    events = asyncio.run(consume())

    assert events[0] == provider_module.StreamChunk.started(
        provider="deepseek",
        model="deepseek-v4-pro",
    )


def test_generic_openai_rejects_malformed_tool_arguments_as_protocol_error(
    monkeypatch,
):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_bad",
                                        "type": "function",
                                        "function": {
                                            "name": "search",
                                            "arguments": '{"query":',
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
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret",
        model="gpt-4o-mini",
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "search"}],
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]

    with pytest.raises(provider_module.ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value.error_kind is provider_module.ProviderErrorKind.PROTOCOL
    assert captured.value.retryable is False


def test_generic_openai_structural_stream_error_is_typed(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": "not-an-integer",
                                        "id": "call_bad_index",
                                        "type": "function",
                                        "function": {
                                            "name": "search",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret", model="gpt-4o-mini"
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "search"}],
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]

    with pytest.raises(provider_module.ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value.error_kind is provider_module.ProviderErrorKind.PROTOCOL


def test_generic_openai_length_stop_never_releases_salvageable_tool_call(
    monkeypatch,
):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_partial",
                                        "type": "function",
                                        "function": {
                                            "name": "search",
                                            "arguments": '{"query":"complete-json"}',
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
                        {"index": 0, "delta": {}, "finish_reason": "length"}
                    ]
                },
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret",
        model="gpt-4o-mini",
    )
    emitted = []

    async def consume():
        async for event in provider.astream(
            messages=[{"role": "user", "content": "search"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        ):
            emitted.append(event)

    with pytest.raises(provider_module.ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value.error_kind is provider_module.ProviderErrorKind.PROTOCOL
    assert not [event for event in emitted if event.kind is provider_module.StreamKind.TOOL_CALLS]


def test_generic_openai_history_drops_opaque_reasoning_and_preserves_tools(
    monkeypatch,
):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}
                    ]
                }
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret",
        model="gpt-4o-mini",
    )
    messages = [
        {"role": "user", "content": "search"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "deepseek-private-reasoning",
            "tool_calls": [
                {
                    "id": "call_exact",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"query":"Ada"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_exact",
            "content": '{"name":"Ada"}',
        },
    ]
    original = copy.deepcopy(messages)

    async def consume():
        return [event async for event in provider.astream(messages=messages)]

    asyncio.run(consume())

    assert requests[0]["messages"] == [
        messages[0],
        {
            "role": "assistant",
            "content": None,
            "tool_calls": messages[1]["tool_calls"],
        },
        messages[2],
    ]
    assert messages == original


def test_generic_openai_usage_maps_cache_and_reasoning_token_details(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "prompt_tokens_details": {"cached_tokens": 4},
                        "completion_tokens_details": {"reasoning_tokens": 3},
                    },
                }
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret",
        model="gpt-4o-mini",
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]

    usage = next(
        event.usage
        for event in asyncio.run(consume())
        if event.kind is provider_module.StreamKind.USAGE
    )

    assert usage == provider_module.ModelUsage(
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cached_input_tokens=4,
        uncached_input_tokens=6,
        reasoning_tokens=3,
    )


def test_generic_openai_requests_terminal_usage_explicitly(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}
                    ]
                }
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret",
        model="gpt-4o-mini",
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]

    asyncio.run(consume())

    assert requests[0]["stream_options"] == {"include_usage": True}


def test_kimi_environment_selects_provider_with_its_own_contract(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-secret")
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("CLUB_MODEL", raising=False)

    provider = provider_module.provider_from_env()

    assert type(provider).__name__ == "KimiProvider"
    assert provider.provider_id == "kimi"
    assert provider.model_id == "kimi-k3"


def test_kimi_replays_reasoning_for_tool_results_without_mutating_history(
    monkeypatch,
):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                200,
                text=_sse(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"reasoning_content": "kimi-private"},
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
                                            "id": "call_kimi",
                                            "type": "function",
                                            "function": {
                                                "name": "search",
                                                "arguments": '{"query":"Ada"}',
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
            text=_sse(
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}
                    ]
                }
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.KimiProvider(
        api_key="secret",
        model="kimi-k3",
        base_url="https://api.moonshot.ai/v1",
    )
    first_messages = [{"role": "user", "content": "find Ada"}]

    async def scenario():
        first = [
            event
            async for event in provider.astream(
                context_id="kimi-session",
                messages=first_messages,
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]
        call = next(
            call for event in first for call in (event.tool_calls or [])
        )
        continuation = [
            *first_messages,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call.id, "content": "Ada"},
        ]
        original = copy.deepcopy(continuation)
        _ = [
            event
            async for event in provider.astream(
                context_id="kimi-session",
                messages=continuation,
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]
        return continuation, original

    continuation, original = asyncio.run(scenario())

    assert requests[0]["reasoning_effort"] == "max"
    assert requests[1]["messages"][1]["reasoning_content"] == "kimi-private"
    assert requests[1]["messages"][1]["content"] == ""
    assert continuation == original
    assert "reasoning_content" not in continuation[1]


def test_anthropic_transforms_tool_history_with_exact_call_identity(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"id":"msg_1",'
                '"type":"message","role":"assistant","content":[],"model":'
                '"claude-sonnet-4-6","stop_reason":null,"usage":'
                '{"input_tokens":10,"output_tokens":1}}}\n\n'
                'data: {"type":"content_block_start","index":0,"content_block":'
                '{"type":"text","text":""}}\n\n'
                'data: {"type":"content_block_delta","index":0,"delta":'
                '{"type":"text_delta","text":"done"}}\n\n'
                'data: {"type":"content_block_stop","index":0}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                '"usage":{"output_tokens":2}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.AnthropicProvider(
        api_key="secret",
        model="claude-sonnet-4-6",
    )
    messages = [
        {"role": "system", "content": "Be precise."},
        {"role": "user", "content": "Find Ada"},
        {
            "role": "assistant",
            "content": "I will search.",
            "reasoning_content": "opaque-provider-reasoning",
            "tool_calls": [
                {
                    "id": "call_exact",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"query":"Ada"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_exact",
            "content": '{"name":"Ada Lovelace"}',
        },
    ]
    original = copy.deepcopy(messages)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search people",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]

    async def consume():
        return [
            event
            async for event in provider.astream(messages=messages, tools=tools)
        ]

    asyncio.run(consume())

    assert requests[0]["tools"] == [
        {
            "name": "search",
            "description": "Search people",
            "input_schema": tools[0]["function"]["parameters"],
        }
    ]
    assert requests[0]["messages"] == [
        {"role": "user", "content": "Find Ada"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will search."},
                {
                    "type": "tool_use",
                    "id": "call_exact",
                    "name": "search",
                    "input": {"query": "Ada"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_exact",
                    "content": '{"name":"Ada Lovelace"}',
                }
            ],
        },
    ]
    assert messages == original


def test_anthropic_stream_assembles_tool_deltas_terminal_and_usage(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"id":"msg_1",'
                '"type":"message","role":"assistant","content":[],"model":'
                '"claude-sonnet-4-6","stop_reason":null,"usage":'
                '{"input_tokens":6,"output_tokens":1,"cache_read_input_tokens":3,'
                '"cache_creation_input_tokens":1}}}\n\n'
                'data: {"type":"content_block_start","index":1,"content_block":'
                '{"type":"tool_use","id":"toolu_exact","name":"search","input":{}}}\n\n'
                'data: {"type":"content_block_delta","index":1,"delta":'
                '{"type":"input_json_delta","partial_json":"{\\"query\\":"}}\n\n'
                'data: {"type":"content_block_delta","index":1,"delta":'
                '{"type":"input_json_delta","partial_json":"\\"Ada\\"}"}}\n\n'
                'data: {"type":"content_block_stop","index":1}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
                '"usage":{"output_tokens":4}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.AnthropicProvider(
        api_key="secret",
        model="claude-sonnet-4-6",
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "find Ada"}],
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]

    events = asyncio.run(consume())
    deltas = [
        delta for event in events for delta in (event.tool_call_deltas or [])
    ]
    calls = [call for event in events for call in (event.tool_calls or [])]
    usage = next(event.usage for event in events if event.usage is not None)
    terminal = next(
        event.terminal
        for event in events
        if event.kind is provider_module.StreamKind.TERMINAL
    )

    assert events[0] == provider_module.StreamChunk.started(
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    assert [(delta.index, delta.id, delta.name_delta, delta.arguments_delta) for delta in deltas] == [
        (1, "toolu_exact", "search", None),
        (1, None, None, '{"query":'),
        (1, None, None, '"Ada"}'),
    ]
    assert calls == [
        provider_module.ToolCall(
            id="toolu_exact",
            name="search",
            arguments={"query": "Ada"},
        )
    ]
    assert next(event.finish_reason for event in events if event.finish_reason) == "tool_calls"
    assert usage == provider_module.ModelUsage(
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        cached_input_tokens=3,
        uncached_input_tokens=7,
        reasoning_tokens=0,
        cache_write_input_tokens=1,
    )
    assert terminal.stop_reason == "tool_calls"
    assert terminal.usage == usage
    assert terminal.latency_ms >= 0
    assert terminal.estimated_cost_usd == pytest.approx(0.00008265)


def test_anthropic_rejects_arguments_that_violate_tool_schema(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"usage":'
                '{"input_tokens":1,"output_tokens":1}}}\n\n'
                'data: {"type":"content_block_start","index":0,"content_block":'
                '{"type":"tool_use","id":"toolu_schema","name":"search","input":{}}}\n\n'
                'data: {"type":"content_block_delta","index":0,"delta":'
                '{"type":"input_json_delta","partial_json":"{\\"limit\\":1}"}}\n\n'
                'data: {"type":"content_block_stop","index":0}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
                '"usage":{"output_tokens":2}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.AnthropicProvider(
        api_key="secret", model="claude-sonnet-4-6"
    )
    emitted = []

    async def consume():
        async for event in provider.astream(
            messages=[{"role": "user", "content": "search"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["query"],
                        },
                    },
                }
            ],
        ):
            emitted.append(event)

    with pytest.raises(provider_module.ProviderStreamError, match="required.*query"):
        asyncio.run(consume())

    assert not [event for event in emitted if event.tool_calls]


def test_anthropic_malformed_stream_event_is_typed(monkeypatch):
    async def handler(request):
        return httpx.Response(200, text="data: {malformed-json}\n\n")

    _install_http(monkeypatch, handler)
    provider = provider_module.AnthropicProvider(
        api_key="secret", model="claude-sonnet-4-6"
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]

    with pytest.raises(provider_module.ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value.error_kind is provider_module.ProviderErrorKind.PROTOCOL


def test_fake_provider_covers_the_complete_success_lifecycle():
    usage = provider_module.ModelUsage(
        input_tokens=8,
        output_tokens=4,
        total_tokens=12,
        cached_input_tokens=2,
        uncached_input_tokens=6,
        reasoning_tokens=1,
    )
    call = provider_module.ToolCall(
        id="call_fake",
        name="search",
        arguments={"query": "Ada"},
    )
    provider = provider_module.FakeProvider(
        steps=[
            {
                "reasoning_deltas": ("private",),
                "deltas": ("visible",),
                "tool_call_deltas": [
                    provider_module.ToolCallDelta(
                        index=0,
                        id="call_fake",
                        name_delta="search",
                        arguments_delta='{"query":"Ada"}',
                    )
                ],
                "usage": usage,
                "finish_reason": "tool_calls",
                "tool_calls": [call],
            }
        ]
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "find Ada"}]
            )
        ]

    events = asyncio.run(consume())

    assert [event.kind for event in events] == [
        provider_module.StreamKind.START,
        provider_module.StreamKind.REASONING,
        provider_module.StreamKind.TEXT,
        provider_module.StreamKind.TOOL_DELTA,
        provider_module.StreamKind.USAGE,
        provider_module.StreamKind.TERMINAL,
        provider_module.StreamKind.TOOL_CALLS,
    ]
    assert events[0].start == provider_module.ProviderStart(
        provider="fake",
        model="fake",
    )
    assert events[5].terminal.stop_reason == "tool_calls"
    assert events[5].terminal.usage == usage
    assert events[5].terminal.latency_ms >= 0
    assert events[5].terminal.estimated_cost_usd is None
    assert events[-1].tool_calls == [call]


def test_fake_provider_covers_typed_error_lifecycle():
    failure = provider_module.ProviderStreamError(
        provider="fake",
        model="fake",
        kind=provider_module.ProviderErrorKind.CONNECTION,
        message="provider connection failed",
        retryable=True,
    )
    provider = provider_module.FakeProvider(steps=[{"error": failure}])
    emitted = []

    async def consume():
        async for event in provider.astream(
            messages=[{"role": "user", "content": "hello"}]
        ):
            emitted.append(event)

    with pytest.raises(provider_module.ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value is failure
    assert [event.kind for event in emitted] == [provider_module.StreamKind.START]


@pytest.mark.parametrize(
    "provider",
    [
        provider_module.DeepSeekProvider(
            api_key="secret", model="deepseek-v4-pro"
        ),
        provider_module.KimiProvider(api_key="secret", model="kimi-k3"),
        provider_module.AnthropicProvider(
            api_key="secret", model="claude-sonnet-4-6"
        ),
        provider_module.OpenAICompatProvider(
            api_key="secret", model="gpt-4o-mini"
        ),
    ],
    ids=lambda provider: provider.provider_id,
)
def test_every_http_provider_uses_typed_content_free_rate_limit_errors(
    provider, monkeypatch
):
    private_body = "PRIVATE_PROVIDER_BODY " + ("x" * 500)

    async def handler(request):
        return httpx.Response(429, text=private_body)

    _install_http(monkeypatch, handler)

    async def consume():
        return [
            event
            async for event in provider.astream(
                context_id="rate-limit",
                messages=[{"role": "user", "content": "hello"}],
            )
        ]

    with pytest.raises(provider_module.ProviderStreamError) as captured:
        asyncio.run(consume())

    assert captured.value.provider == provider.provider_id
    assert captured.value.model == provider.model_id
    assert captured.value.error_kind is provider_module.ProviderErrorKind.RATE_LIMIT
    assert captured.value.retryable is True
    assert captured.value.http_status == 429
    assert private_body not in str(captured.value)


@pytest.mark.parametrize(
    "provider",
    [
        provider_module.DeepSeekProvider(
            api_key="secret", model="deepseek-v4-pro"
        ),
        provider_module.KimiProvider(api_key="secret", model="kimi-k3"),
        provider_module.AnthropicProvider(
            api_key="secret", model="claude-sonnet-4-6"
        ),
        provider_module.OpenAICompatProvider(
            api_key="secret", model="gpt-4o-mini"
        ),
    ],
    ids=lambda provider: provider.provider_id,
)
def test_cancellation_propagates_and_closes_every_http_provider_stream(
    provider, monkeypatch
):
    first_delta_seen = asyncio.Event()

    class CancelStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            if provider.provider_id == "anthropic":
                payload = {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "partial"},
                }
            else:
                payload = {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "partial"},
                            "finish_reason": None,
                        }
                    ]
                }
            yield f"data: {json.dumps(payload)}\n\n".encode()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = CancelStream()

    async def handler(request):
        return httpx.Response(200, stream=stream)

    _install_http(monkeypatch, handler)
    emitted = []

    async def consume():
        async for event in provider.astream(
            context_id="cancel-session",
            messages=[{"role": "user", "content": "hello"}],
        ):
            emitted.append(event)
            if event.kind is provider_module.StreamKind.TEXT:
                first_delta_seen.set()

    async def scenario():
        task = asyncio.create_task(consume())
        await asyncio.wait_for(first_delta_seen.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert stream.closed is True
    assert not [
        event
        for event in emitted
        if event.kind
        in {provider_module.StreamKind.TERMINAL, provider_module.StreamKind.TOOL_CALLS}
    ]


@pytest.mark.parametrize(
    "provider",
    [
        provider_module.DeepSeekProvider(
            api_key="secret", model="deepseek-v4-pro"
        ),
        provider_module.KimiProvider(api_key="secret", model="kimi-k3"),
        provider_module.AnthropicProvider(
            api_key="secret", model="claude-sonnet-4-6"
        ),
        provider_module.OpenAICompatProvider(
            api_key="secret", model="gpt-4o-mini"
        ),
    ],
    ids=lambda provider: provider.provider_id,
)
def test_truncated_stream_never_releases_tool_calls_for_any_provider(
    provider, monkeypatch
):
    if provider.provider_id == "anthropic":
        partial = (
            'data: {"type":"content_block_start","index":0,"content_block":'
            '{"type":"tool_use","id":"call_cut","name":"search","input":{}}}\n\n'
            'data: {"type":"content_block_delta","index":0,"delta":'
            '{"type":"input_json_delta","partial_json":"{}"}}\n\n'
        )
    else:
        partial = (
            "data: "
            + json.dumps(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_cut",
                                        "type": "function",
                                        "function": {
                                            "name": "search",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            )
            + "\n\n"
        )

    async def handler(request):
        return httpx.Response(200, text=partial)

    _install_http(monkeypatch, handler)
    emitted = []

    async def consume():
        async for event in provider.astream(
            context_id="truncated-session",
            messages=[{"role": "user", "content": "search"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        ):
            emitted.append(event)

    with pytest.raises(provider_module.ProviderStreamError):
        asyncio.run(consume())

    assert not [event for event in emitted if event.tool_calls]


@pytest.mark.parametrize(
    "provider",
    [
        provider_module.DeepSeekProvider(
            api_key="secret", model="deepseek-v4-pro"
        ),
        provider_module.KimiProvider(api_key="secret", model="kimi-k3"),
        provider_module.AnthropicProvider(
            api_key="secret", model="claude-sonnet-4-6"
        ),
        provider_module.OpenAICompatProvider(
            api_key="secret", model="gpt-4o-mini"
        ),
    ],
    ids=lambda provider: provider.provider_id,
)
def test_non_object_tool_arguments_are_rejected_by_every_provider(
    provider, monkeypatch
):
    if provider.provider_id == "anthropic":
        response = (
            'data: {"type":"message_start","message":{"usage":'
            '{"input_tokens":1,"output_tokens":1}}}\n\n'
            'data: {"type":"content_block_start","index":0,"content_block":'
            '{"type":"tool_use","id":"call_array","name":"search","input":{}}}\n\n'
            'data: {"type":"content_block_delta","index":0,"delta":'
            '{"type":"input_json_delta","partial_json":"[]"}}\n\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
            '"usage":{"output_tokens":2}}\n\n'
            'data: {"type":"message_stop"}\n\n'
        )
    else:
        response = _sse(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_array",
                                    "type": "function",
                                    "function": {
                                        "name": "search",
                                        "arguments": "[]",
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
        )

    async def handler(request):
        return httpx.Response(200, text=response)

    _install_http(monkeypatch, handler)
    emitted = []

    async def consume():
        async for event in provider.astream(
            context_id="non-object",
            messages=[{"role": "user", "content": "search"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        ):
            emitted.append(event)

    with pytest.raises(provider_module.ProviderStreamError, match="non-object"):
        asyncio.run(consume())

    assert not [event for event in emitted if event.tool_calls]


def test_terminal_metadata_has_latency_usage_and_known_model_cost(
    monkeypatch,
):
    ticks = iter((10.0, 10.125))
    monkeypatch.setattr(provider_module, "monotonic", lambda: next(ticks))

    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 1_000_000,
                        "completion_tokens": 1_000_000,
                        "total_tokens": 2_000_000,
                        "prompt_tokens_details": {"cached_tokens": 0},
                    },
                }
            ),
        )

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret",
        model="gpt-4o-mini",
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                messages=[{"role": "user", "content": "hello"}]
            )
        ]

    terminal = next(
        event.terminal
        for event in asyncio.run(consume())
        if event.kind is provider_module.StreamKind.TERMINAL
    )

    assert terminal == provider_module.ProviderTerminal(
        stop_reason="stop",
        usage=provider_module.ModelUsage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            total_tokens=2_000_000,
            cached_input_tokens=0,
            uncached_input_tokens=1_000_000,
            reasoning_tokens=0,
        ),
        latency_ms=125.0,
        estimated_cost_usd=0.75,
    )


def test_generic_openai_rejects_arguments_that_violate_tool_schema(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_schema",
                                        "type": "function",
                                        "function": {
                                            "name": "search",
                                            "arguments": '{"limit":1}',
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

    _install_http(monkeypatch, handler)
    provider = provider_module.OpenAICompatProvider(
        api_key="secret", model="gpt-4o-mini"
    )
    emitted = []

    async def consume():
        async for event in provider.astream(
            messages=[{"role": "user", "content": "search"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "search",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                                "limit": {"type": "integer"},
                            },
                            "required": ["query"],
                        },
                    },
                }
            ],
        ):
            emitted.append(event)

    with pytest.raises(provider_module.ProviderStreamError, match="required.*query"):
        asyncio.run(consume())

    assert not [event for event in emitted if event.tool_calls]


@pytest.mark.parametrize(
    "expected_before_hash",
    ["abc123", None],
    ids=["string-hash", "json-null"],
)
def test_deepseek_releases_workspace_write_with_expected_before_hash(
    expected_before_hash, monkeypatch
):
    arguments = {
        "grant_id": "grant-1",
        "path": "notes.md",
        "content": "hello",
        "expected_before_hash": expected_before_hash,
    }

    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_write",
                                        "type": "function",
                                        "function": {
                                            "name": "fs_write",
                                            "arguments": json.dumps(arguments),
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

    _install_http(monkeypatch, handler)
    provider = provider_module.DeepSeekProvider(
        api_key="secret", model="deepseek-v4-pro"
    )

    async def consume():
        return [
            event
            async for event in provider.astream(
                context_id="workspace-write",
                messages=[{"role": "user", "content": "write notes.md"}],
                tools=WORKSPACE_TOOL_SCHEMAS,
            )
        ]

    events = asyncio.run(consume())
    calls = next(event.tool_calls for event in events if event.tool_calls)

    assert calls == [
        provider_module.ToolCall(
            id="call_write",
            name="fs_write",
            arguments={
                "grant_id": "grant-1",
                "path": "notes.md",
                "content": "hello",
                "expected_before_hash": expected_before_hash,
            },
        )
    ]
