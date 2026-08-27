import asyncio
import copy
import json

import httpx
import pytest

from coworker.provider import (
    DEEPSEEK_MODEL,
    KIMI_MODEL,
    DeepSeekProvider,
    OpenAICompatProvider,
    default_model_id,
    provider_from_env,
)
from coworker.inbox import Inbox
from coworker.permissions import Decision
from coworker.store import ConversationStore
from coworker.turn import run_turn
from coworker.workspace_runtime import WorkspaceRuntime


def _sse(*payloads):
    return "".join(f"data: {json.dumps(payload)}\n\n" for payload in payloads) + "data: [DONE]\n\n"


def test_deepseek_wins_over_kimi(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "kimi-key")
    monkeypatch.delenv("CLUB_MODEL", raising=False)
    p = provider_from_env()
    assert isinstance(p, OpenAICompatProvider)
    assert type(p).__name__ == "DeepSeekProvider"
    assert p.model_id == DEEPSEEK_MODEL
    assert p.base_url == "https://api.deepseek.com"
    assert p.api_key == "ds-key"


def test_kimi_when_no_deepseek(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("KIMI_API_KEY", "kimi-key")
    monkeypatch.delenv("CLUB_MODEL", raising=False)
    p = provider_from_env()
    assert isinstance(p, OpenAICompatProvider)
    assert p.model_id == KIMI_MODEL
    assert p.base_url == "https://api.moonshot.ai/v1"
    assert p.api_key == "kimi-key"


def test_club_model_override(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("CLUB_MODEL", "deepseek-v4-flash")
    p = provider_from_env()
    assert p is not None
    assert p.model_id == "deepseek-v4-flash"


def test_deepseek_explicitly_enables_thinking_and_usage_without_tool_choice(monkeypatch):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=_sse(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)

    async def consume():
        return [
            chunk
            async for chunk in provider.astream(
                context_id="request-shape",
                messages=[{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "now"}}],
            )
        ]

    asyncio.run(consume())

    assert requests == [
        {
            "model": DEEPSEEK_MODEL,
            "stream": True,
            "stream_options": {"include_usage": True},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "now"}}],
        }
    ]


def test_deepseek_stream_distinguishes_transient_reasoning_from_answer(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": "private analysis"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"content": "Visible answer"},
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)

    async def consume():
        return [
            chunk
            async for chunk in provider.astream(
                messages=[{"role": "user", "content": "hi"}]
            )
        ]

    chunks = asyncio.run(consume())

    assert [
        (chunk.transient_reasoning_delta, chunk.text_delta)
        for chunk in chunks
        if chunk.finish_reason is None
    ] == [
        ("private analysis", None),
        (None, "Visible answer"),
    ]


def test_deepseek_stream_emits_terminal_reason_and_content_free_usage(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {"content": "done"},
                            "finish_reason": "stop",
                        }
                    ]
                },
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                        "prompt_cache_hit_tokens": 4,
                        "prompt_cache_miss_tokens": 7,
                        "completion_tokens_details": {"reasoning_tokens": 3},
                        "unexpected_response_content": "must not escape",
                    },
                },
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)

    async def consume():
        return [
            chunk
            async for chunk in provider.astream(
                messages=[{"role": "user", "content": "hi"}]
            )
        ]

    chunks = asyncio.run(consume())

    assert chunks[-2].finish_reason == "stop"
    assert vars(chunks[-1].usage) == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
        "cached_input_tokens": 4,
        "uncached_input_tokens": 7,
        "reasoning_tokens": 3,
    }
    assert "unexpected_response_content" not in repr(chunks[-1].usage)


def test_deepseek_stream_assembles_interleaved_tool_deltas_by_call_identity(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 1,
                                        "id": "call_search",
                                        "function": {
                                            "name": "sea",
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
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_now",
                                        "function": {
                                            "name": "now",
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "function": {
                                            "name": "rch",
                                            "arguments": '"founder"}',
                                        },
                                    },
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {},
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)

    async def consume():
        return [
            chunk
            async for chunk in provider.astream(
                context_id="multi-tool",
                messages=[{"role": "user", "content": "use tools"}],
                tools=[{"type": "function", "function": {"name": "now"}}],
            )
        ]

    chunks = asyncio.run(consume())
    deltas = [
        delta
        for chunk in chunks
        for delta in (chunk.tool_call_deltas or [])
    ]
    completed = [call for chunk in chunks for call in (chunk.tool_calls or [])]

    assert [(delta.index, delta.id) for delta in deltas] == [
        (1, "call_search"),
        (0, "call_now"),
        (1, None),
    ]
    assert [(call.id, call.name, call.arguments) for call in completed] == [
        ("call_now", "now", {}),
        ("call_search", "search", {"query": "founder"}),
    ]


def test_deepseek_rejects_malformed_streamed_tool_arguments(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_bad",
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
                        {"delta": {}, "finish_reason": "tool_calls"}
                    ]
                },
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    emitted = []

    async def consume():
        async for chunk in provider.astream(
            context_id="malformed",
            messages=[{"role": "user", "content": "use a tool"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        ):
            emitted.append(chunk)

    with pytest.raises(RuntimeError, match="invalid JSON tool arguments"):
        asyncio.run(consume())

    assert not [chunk for chunk in emitted if chunk.tool_calls]


def test_deepseek_rejects_truncated_stream_before_releasing_tool_calls(monkeypatch):
    partial = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_partial",
                            "function": {"name": "now", "arguments": "{}"},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }

    async def handler(request):
        return httpx.Response(
            200,
            text=f"data: {json.dumps(partial)}\n\n",
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    emitted = []

    async def consume():
        async for chunk in provider.astream(
            context_id="truncated",
            messages=[{"role": "user", "content": "use a tool"}],
            tools=[{"type": "function", "function": {"name": "now"}}],
        ):
            emitted.append(chunk)

    with pytest.raises(RuntimeError, match="ended before.*DONE"):
        asyncio.run(consume())

    assert not [chunk for chunk in emitted if chunk.tool_calls]


def test_deepseek_length_terminal_never_releases_partial_tool_calls(monkeypatch):
    async def handler(request):
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_cut_off",
                                        "function": {
                                            "name": "search",
                                            "arguments": '{"query":"founder"}',
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
                        {"delta": {}, "finish_reason": "length"}
                    ]
                },
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    emitted = []

    async def consume():
        async for chunk in provider.astream(
            context_id="length",
            messages=[{"role": "user", "content": "use a tool"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
        ):
            emitted.append(chunk)

    with pytest.raises(RuntimeError, match="tool assembly.*length"):
        asyncio.run(consume())

    assert not [chunk for chunk in emitted if chunk.tool_calls]


def test_deepseek_provider_error_is_truthful_bounded_and_content_free(monkeypatch):
    private_body = "PRIVATE_REASONING " + ("x" * 1000)

    async def handler(request):
        return httpx.Response(429, text=private_body)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)

    async def consume():
        return [
            chunk
            async for chunk in provider.astream(
                messages=[{"role": "user", "content": "hi"}]
            )
        ]

    with pytest.raises(RuntimeError) as captured:
        asyncio.run(consume())

    assert str(captured.value) == "deepseek provider request failed with HTTP 429"
    assert private_body not in str(captured.value)


def test_deepseek_replays_transient_reasoning_for_next_tool_round_without_mutation(
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
                                "delta": {"reasoning_content": "private round one"},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_search",
                                            "function": {
                                                "name": "search",
                                                "arguments": '{"query":"founder"}',
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
                            {"delta": {}, "finish_reason": "tool_calls"}
                        ]
                    },
                ),
            )
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": "private round two"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {"delta": {"content": "done"}, "finish_reason": None}
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    first_messages = [{"role": "user", "content": "find a founder"}]

    async def scenario():
        first_chunks = [
            chunk
            async for chunk in provider.astream(
                context_id="session-a",
                messages=first_messages,
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]
        call = next(
            call
            for chunk in first_chunks
            for call in (chunk.tool_calls or [])
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
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": '{"result":"Ada"}',
            },
        ]
        durable_before = copy.deepcopy(continuation)
        _ = [
            chunk
            async for chunk in provider.astream(
                context_id="session-a",
                messages=continuation,
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]
        return continuation, durable_before

    continuation, durable_before = asyncio.run(scenario())
    replayed_assistant = requests[1]["messages"][1]

    assert replayed_assistant["reasoning_content"] == "private round one"
    assert replayed_assistant["content"] == ""
    assert continuation == durable_before
    assert "reasoning_content" not in continuation[1]


def test_deepseek_continuity_survives_a_system_prompt_refresh(monkeypatch):
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
                                "delta": {"reasoning_content": "private"},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_memory",
                                            "function": {
                                                "name": "remember",
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
                            {"delta": {}, "finish_reason": "tool_calls"}
                        ]
                    },
                ),
            )
        return httpx.Response(
            200,
            text=_sse(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    tools = [{"type": "function", "function": {"name": "remember"}}]
    initial = [
        {"role": "system", "content": "memory before tool"},
        {"role": "user", "content": "remember this"},
    ]

    async def scenario():
        chunks = [
            chunk
            async for chunk in provider.astream(
                context_id="system-refresh",
                messages=initial,
                tools=tools,
            )
        ]
        call = next(
            call for chunk in chunks for call in (chunk.tool_calls or [])
        )
        continuation = [
            {"role": "system", "content": "memory after tool"},
            initial[1],
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
            {"role": "tool", "tool_call_id": call.id, "content": "{}"},
        ]
        return [
            chunk
            async for chunk in provider.astream(
                context_id="system-refresh",
                messages=continuation,
                tools=tools,
            )
        ]

    asyncio.run(scenario())

    assert requests[1]["messages"][2]["reasoning_content"] == "private"


def test_turn_binds_deepseek_transient_context_without_persisting_reasoning(
    tmp_path, monkeypatch
):
    private_reasoning = "PRIVATE_TRANSIENT_REASONING"
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
                                "delta": {"reasoning_content": private_reasoning},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_now",
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
                            {"delta": {}, "finish_reason": "tool_calls"}
                        ]
                    },
                ),
            )
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": "second private step"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {"delta": {"content": "It is noon."}, "finish_reason": None}
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda name, arguments, **kwargs: (True, {"time": "noon"}),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    store = ConversationStore(tmp_path)
    sid = "deepseek-session"
    store.create_session(sid)
    emitted = []

    async def emit(event):
        emitted.append(event)

    result = asyncio.run(
        run_turn(
            text="what time is it?",
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[
                {"type": "function", "function": {"name": "now"}}
            ],
            execute_kwargs={},
            emit=emit,
        )
    )

    assert result == {"status": "ok", "text": "It is noon."}
    assert len(requests) == 2
    assert requests[1]["messages"][2]["reasoning_content"] == private_reasoning
    durable = json.dumps(
        {"messages": store.load(sid), "events": store.load_events(sid)}
    )
    assert private_reasoning not in durable
    assert "second private step" not in durable
    assert "reasoning_content" not in durable


def test_turn_continues_after_workspace_read_without_persisting_reasoning(
    tmp_path, monkeypatch
):
    private_first = "PRIVATE_FS_READ_REASONING"
    private_final = "PRIVATE_FS_FINAL_REASONING"
    private_follow = "PRIVATE_FS_FOLLOW_REASONING"
    file_body = "SECRET_FILE_BODY"
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
                                "delta": {"reasoning_content": private_first},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_read",
                                            "function": {
                                                "name": "fs_read",
                                                "arguments": (
                                                    '{"grant_id":"g1","path":"notes.md"}'
                                                ),
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
                            {"delta": {}, "finish_reason": "tool_calls"}
                        ]
                    },
                ),
            )
        if len(requests) == 2:
            return httpx.Response(
                200,
                text=_sse(
                    {
                        "choices": [
                            {
                                "delta": {"reasoning_content": private_final},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {"content": "I read the notes."},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ),
            )
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": private_follow},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"content": "The notes are ready."},
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        "coworker.turn.decide",
        lambda *args, **kwargs: Decision(True, False, "auto"),
    )
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda name, arguments, **kwargs: (
            True,
            {"content": file_body, "path": "notes.md", "truncated": False},
        ),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    store = ConversationStore(tmp_path)
    sid = "workspace-read-session"
    store.create_session(sid)

    first = asyncio.run(
        run_turn(
            text="read notes.md",
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[
                {"type": "function", "function": {"name": "fs_read"}}
            ],
            execute_kwargs={"workspace_runtime": WorkspaceRuntime(tmp_path)},
        )
    )
    follow = asyncio.run(
        run_turn(
            text="summarize them",
            sid=sid,
            store=store,
            provider=provider,
            persona=None,
            skills=None,
            inbox=Inbox(store),
            openai_tools=[
                {"type": "function", "function": {"name": "fs_read"}}
            ],
            execute_kwargs={"workspace_runtime": WorkspaceRuntime(tmp_path)},
        )
    )

    assert first == {"status": "ok", "text": "I read the notes."}
    assert follow == {"status": "ok", "text": "The notes are ready."}
    assert len(requests) == 3
    follow_messages = requests[2]["messages"]
    assert follow_messages[2]["reasoning_content"] == private_first
    assert follow_messages[4]["reasoning_content"] == private_final
    durable = json.dumps(
        {"messages": store.load(sid), "events": store.load_events(sid)}
    )
    assert file_body not in durable
    assert private_first not in durable
    assert private_final not in durable
    assert private_follow not in durable
    assert "reasoning_content" not in durable


def test_turn_continues_after_workspace_shell_without_persisting_reasoning(
    tmp_path, monkeypatch
):
    private_first = "PRIVATE_SHELL_REASONING"
    private_final = "PRIVATE_SHELL_FINAL_REASONING"
    private_follow = "PRIVATE_SHELL_FOLLOW_REASONING"
    command_output = "SECRET_SHELL_OUTPUT"
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
                                "delta": {"reasoning_content": private_first},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_shell",
                                            "function": {
                                                "name": "shell_exec",
                                                "arguments": (
                                                    '{"grant_id":"g1","command":"echo hi","cwd":"."}'
                                                ),
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
                            {"delta": {}, "finish_reason": "tool_calls"}
                        ]
                    },
                ),
            )
        if len(requests) == 2:
            return httpx.Response(
                200,
                text=_sse(
                    {
                        "choices": [
                            {
                                "delta": {"reasoning_content": private_final},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {"content": "The command finished."},
                                "finish_reason": None,
                            }
                        ]
                    },
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ),
            )
        return httpx.Response(
            200,
            text=_sse(
                {
                    "choices": [
                        {
                            "delta": {"reasoning_content": private_follow},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"content": "Ready for the next step."},
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    monkeypatch.setattr(
        "coworker.turn.decide",
        lambda *args, **kwargs: Decision(True, False, "auto"),
    )
    monkeypatch.setattr(
        "coworker.turn.execute",
        lambda name, arguments, **kwargs: (
            True,
            {"output": command_output, "exit_code": 0},
        ),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    store = ConversationStore(tmp_path)
    sid = "workspace-shell-session"
    store.create_session(sid)
    tools = [{"type": "function", "function": {"name": "shell_exec"}}]
    kwargs = {
        "sid": sid,
        "store": store,
        "provider": provider,
        "persona": None,
        "skills": None,
        "inbox": Inbox(store),
        "openai_tools": tools,
        "execute_kwargs": {"workspace_runtime": WorkspaceRuntime(tmp_path)},
    }

    first = asyncio.run(run_turn(text="echo hi", **kwargs))
    follow = asyncio.run(run_turn(text="what next?", **kwargs))

    assert first == {"status": "ok", "text": "The command finished."}
    assert follow == {"status": "ok", "text": "Ready for the next step."}
    assert len(requests) == 3
    follow_messages = requests[2]["messages"]
    assert follow_messages[2]["reasoning_content"] == private_first
    assert follow_messages[4]["reasoning_content"] == private_final
    durable = json.dumps(
        {"messages": store.load(sid), "events": store.load_events(sid)}
    )
    assert command_output not in durable
    assert "[COMMAND REDACTED]" in durable
    assert private_first not in durable
    assert private_final not in durable
    assert private_follow not in durable
    assert "reasoning_content" not in durable


def test_fresh_deepseek_provider_reports_unavailable_transient_continuation(
    monkeypatch,
):
    requests = []

    async def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text=_sse(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    restarted = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    durable_messages = [
        {"role": "user", "content": "find a founder"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_before_restart",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_before_restart",
            "content": '{"result":"Ada"}',
        },
    ]
    original = copy.deepcopy(durable_messages)

    async def consume():
        return [
            chunk
            async for chunk in restarted.astream(
                context_id="restored-session",
                messages=durable_messages,
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]

    with pytest.raises(
        RuntimeError,
        match="transient reasoning is unavailable for continuation",
    ):
        asyncio.run(consume())

    assert requests == []
    assert durable_messages == original


def test_deepseek_cancellation_propagates_closes_stream_and_discards_partial_call(
    monkeypatch,
):
    tool_delta_seen = asyncio.Event()

    class CancelStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            payload = {
                "choices": [
                    {
                        "delta": {
                            "reasoning_content": "partial private reasoning",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_cancelled",
                                    "function": {
                                        "name": "search",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ]
            }
            yield f"data: {json.dumps(payload)}\n\n".encode()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = CancelStream()
    request_count = 0

    async def handler(request):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(200, stream=stream)
        return httpx.Response(
            200,
            text=_sse(
                {"choices": [{"delta": {}, "finish_reason": "stop"}]}
            ),
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = DeepSeekProvider(api_key="secret", model=DEEPSEEK_MODEL)
    emitted = []
    first_messages = [{"role": "user", "content": "find"}]

    async def consume_cancelled():
        async for chunk in provider.astream(
            context_id="cancelled-session",
            messages=first_messages,
            tools=[{"type": "function", "function": {"name": "search"}}],
        ):
            emitted.append(chunk)
            if chunk.tool_call_deltas:
                tool_delta_seen.set()

    async def consume_continuation():
        continuation = [
            *first_messages,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_cancelled",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_cancelled",
                "content": "{}",
            },
        ]
        return [
            chunk
            async for chunk in provider.astream(
                context_id="cancelled-session",
                messages=continuation,
                tools=[{"type": "function", "function": {"name": "search"}}],
            )
        ]

    async def scenario():
        task = asyncio.create_task(consume_cancelled())
        await asyncio.wait_for(tool_delta_seen.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(
            RuntimeError,
            match="transient reasoning is unavailable for continuation",
        ):
            await consume_continuation()

    asyncio.run(scenario())

    assert stream.closed is True
    assert not [chunk for chunk in emitted if chunk.tool_calls]
    assert request_count == 1


def test_default_model_id_none(monkeypatch):
    for key in (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CLUB_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    assert default_model_id() is None
    assert provider_from_env() is None
