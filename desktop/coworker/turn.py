"""Extracted turn loop — WS and scheduler share this path."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from coworker.inbox import Inbox
from coworker.ledger import record_tool_on_person
from coworker.permissions import decide
from coworker.provider import ToolCall
from coworker.store import ConversationStore
from coworker.tools import execute

MAX_STEPS = 8
INTERRUPTED_TOOL = '{"error": "tool call interrupted"}'


def close_open_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    pending: list[tuple[str, str]] = []

    def flush() -> None:
        nonlocal pending
        for call_id, name in pending:
            out.append(
                {
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call_id,
                    "content": INTERRUPTED_TOOL,
                }
            )
        pending = []

    for msg in messages:
        role = msg.get("role")
        if pending and role != "tool":
            flush()
        out.append(msg)
        if role == "assistant":
            pending = []
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                if not call_id:
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(fn.get("name") or call.get("name") or "tool")
                pending.append((call_id, name))
        elif role == "tool":
            cid = str(msg.get("tool_call_id") or "")
            pending = [(i, n) for i, n in pending if i != cid]
    flush()
    return out


def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in calls
        ],
    }


def _tool_result_message(call: ToolCall, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "name": call.name,
        "tool_call_id": call.id,
        "content": json.dumps(payload),
    }


def _record_person_file(
    sid: str,
    call: ToolCall,
    ok: bool,
    result: dict[str, Any],
    execute_kwargs: dict,
) -> None:
    people = execute_kwargs.get("people")
    if people is None:
        return
    record_tool_on_person(people, sid, call.name, call.arguments, result, ok=ok)


def _persist_closed(store: ConversationStore, sid: str, history: list[dict[str, Any]]) -> None:
    closed = close_open_tool_calls(history)
    if closed == history:
        return
    history[:] = closed
    store.replace_all(sid, [m for m in closed if m.get("role") != "system"])


async def run_turn(
    *,
    text: str,
    sid: str,
    store: ConversationStore,
    provider,
    persona,
    skills,
    inbox: Inbox,
    openai_tools: list,
    execute_kwargs: dict,
    emit: Callable[[dict], Awaitable[None]] | None = None,
    wait_permission: Callable[[str], Awaitable[str]] | None = None,
    system_prompt_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
    async def _emit(event: dict[str, Any]) -> None:
        if emit is not None:
            await emit(event)

    if provider is None:
        await _emit(
            {
                "type": "error",
                "message": "No model key. Set DEEPSEEK_API_KEY (deepseek-v4-pro) or MOONSHOT_API_KEY (kimi-k3) in ~/.config/club/.env.",
            }
        )
        return {"status": "error", "text": ""}

    raw = store.load(sid)
    loaded = close_open_tool_calls(raw)
    if loaded != raw:
        store.replace_all(sid, loaded)
    sys_content = ""
    if system_prompt_fn is not None:
        sys_content = system_prompt_fn(store, persona, skills)
    history: list[dict[str, Any]] = [{"role": "system", "content": sys_content}] + loaded
    # Caller already appended the user message in WS; scheduler has not.
    if not (loaded and loaded[-1].get("role") == "user" and loaded[-1].get("content") == text):
        user_msg = {"role": "user", "content": text}
        history.append(user_msg)
        store.append(sid, user_msg)

    await _emit({"type": "turn_start"})
    last_text = ""
    try:
        for _ in range(MAX_STEPS):
            chunks: list[str] = []
            calls: list[ToolCall] = []
            _persist_closed(store, sid, history)
            async for chunk in provider.astream(messages=history, tools=openai_tools):
                if chunk.text_delta:
                    chunks.append(chunk.text_delta)
                    await _emit({"type": "assistant_delta", "delta": chunk.text_delta})
                if chunk.tool_calls:
                    calls = chunk.tool_calls
            last_text = "".join(chunks)
            if not calls:
                if last_text:
                    assistant_msg = {"role": "assistant", "content": last_text}
                    history.append(assistant_msg)
                    store.append(sid, assistant_msg)
                break
            tool_msg = _assistant_tool_message(last_text, calls)
            history.append(tool_msg)
            store.append(sid, tool_msg)
            for call in calls:
                gate = decide(call.name)
                if not gate.allowed and not gate.needs_user:
                    result = {"error": gate.reason or "denied"}
                    await _emit(
                        {
                            "type": "tool_finished",
                            "id": call.id,
                            "name": call.name,
                            "ok": False,
                            "result": result,
                        }
                    )
                    denied = _tool_result_message(call, result)
                    history.append(denied)
                    store.append(sid, denied)
                    _record_person_file(sid, call, False, result, execute_kwargs)
                    continue
                if gate.needs_user:
                    inbox.park(call.name, call.arguments, item_id=call.id)
                    await _emit(
                        {
                            "type": "permission_required",
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "reason": gate.reason,
                        }
                    )
                    if wait_permission is None:
                        return {"status": "waiting", "text": last_text}
                    choice = await wait_permission(call.id)
                    cached = execute_kwargs.get("_tool_results")
                    if isinstance(cached, dict) and call.id in cached:
                        ok, result = cached.pop(call.id)
                        await _emit(
                            {
                                "type": "tool_finished",
                                "id": call.id,
                                "name": call.name,
                                "ok": ok,
                                "result": result,
                            }
                        )
                        history.append(_tool_result_message(call, result))
                        store.append(sid, _tool_result_message(call, result))
                        _record_person_file(sid, call, ok, result, execute_kwargs)
                        continue
                    if choice == "deny":
                        result = {"error": "denied by user"}
                        await _emit(
                            {
                                "type": "tool_finished",
                                "id": call.id,
                                "name": call.name,
                                "ok": False,
                                "result": result,
                            }
                        )
                        denied = _tool_result_message(call, result)
                        history.append(denied)
                        store.append(sid, denied)
                        _record_person_file(sid, call, False, result, execute_kwargs)
                        continue
                await _emit(
                    {
                        "type": "tool_started",
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                )
                try:
                    kw = {k: v for k, v in execute_kwargs.items() if not k.startswith("_")}
                    ok, result = execute(call.name, call.arguments, **kw)
                except Exception as exc:
                    ok, result = False, {"error": str(exc)}
                if call.name in {"remember", "memory_update", "memory_forget"} and system_prompt_fn:
                    history[0] = {
                        "role": "system",
                        "content": system_prompt_fn(store, persona, skills),
                    }
                await _emit(
                    {
                        "type": "tool_finished",
                        "id": call.id,
                        "name": call.name,
                        "ok": ok,
                        "result": result,
                    }
                )
                tool_result = _tool_result_message(call, result)
                history.append(tool_result)
                store.append(sid, tool_result)
                _record_person_file(sid, call, ok, result, execute_kwargs)
        else:
            await _emit({"type": "error", "message": f"Stopped after {MAX_STEPS} tool steps."})
            return {"status": "stopped", "text": last_text}
    except Exception as exc:
        _persist_closed(store, sid, history)
        await _emit({"type": "error", "message": str(exc)})
        return {"status": "error", "text": last_text}
    await _emit({"type": "turn_end", "text": last_text})
    return {"status": "ok", "text": last_text}
