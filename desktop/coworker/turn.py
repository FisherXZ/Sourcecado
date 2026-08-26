"""Extracted turn loop — WS and scheduler share this path."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from coworker.events import TurnEventStream, TurnIdentity, new_turn_identity
from coworker.inbox import Inbox
from coworker.ledger import record_tool_on_person
from coworker.permissions import RETRY_SAFE, decide
from coworker.provider import ToolCall
from coworker.store import ConversationStore
from coworker.tools import execute

MAX_STEPS = 8
INTERRUPTED_TOOL = '{"error": "tool call interrupted"}'


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_TOOL_SOURCES: dict[str, tuple[str | None, str]] = {
    "gmail_search": ("gmail", "Gmail"),
    "gmail_read": ("gmail", "Gmail"),
    "gmail_draft": ("gmail", "Gmail"),
    "drive_search": ("drive", "Google Drive"),
    "drive_read": ("drive", "Google Drive"),
    "calendar_list": ("calendar", "Google Calendar"),
    "calendar_create": ("calendar", "Google Calendar"),
    "calendar_update": ("calendar", "Google Calendar"),
    "apollo_search_people": ("apollo", "Apollo"),
    "apollo_enrich_contact": ("apollo", "Apollo"),
}

_SAFE_RETRY_TOOLS = RETRY_SAFE


def approval_resource(
    name: str, arguments: dict[str, Any], gmail: Any
) -> dict[str, Any] | None:
    """The few fields an operator needs to judge a gmail_send: recipient,
    subject, account. Never the body, tokens, or raw headers (DU-12)."""
    if name != "gmail_send":
        return None
    draft_id = str(arguments.get("draft_id") or "")
    resource: dict[str, Any] = {
        "kind": "gmail_draft",
        "draft_id": draft_id,
        "to": None,
        "subject": None,
        "account": None,
    }
    if gmail is None:
        return resource
    try:
        draft = gmail.get_draft(draft_id=draft_id)
        resource["to"] = draft.get("to") or None
        resource["subject"] = draft.get("subject") or None
    except Exception:
        pass
    account = getattr(gmail, "account", None)
    if callable(account):
        try:
            resource["account"] = account() or None
        except Exception:
            pass
    return resource


def _tool_source(name: str) -> tuple[str | None, str]:
    if name.startswith("mcp__"):
        return "granola", "Granola"
    return _TOOL_SOURCES.get(name, (None, "Sourcecado action"))


def _failure_class(detail: str) -> str:
    lower = detail.lower()
    if any(
        marker in lower
        for marker in (
            "not connected",
            "not configured",
            "unauthorized",
            "authentication",
            "credential",
            "missing scope",
            "http 401",
            "http 403",
            "api_key",
            "api key",
        )
    ):
        return "connector_auth"
    if any(
        marker in lower
        for marker in (
            "timeout",
            "timed out",
            "network",
            "connection reset",
            "connection refused",
            "dns",
            "http 502",
            "http 503",
            "http 504",
        )
    ):
        return "timeout_network"
    if any(marker in lower for marker in ("denied", "permission", "not allowed")):
        return "permission"
    if any(
        marker in lower
        for marker in ("required", "invalid", "must be", "must provide", "malformed")
    ):
        return "validation"
    return "unknown"


def _failure_summary(failure_class: str, source: str, retry_safe: bool) -> str:
    if failure_class == "connector_auth":
        return f"{source} needs to be repaired before this source can be checked."
    if failure_class == "timeout_network":
        return (
            f"{source} could not be reached. Retry is safe."
            if retry_safe
            else f"{source} could not be reached. Review before retrying."
        )
    if failure_class == "permission":
        return f"{source} was not allowed to complete this action."
    if failure_class == "validation":
        return f"{source} needs corrected request details before retrying."
    return f"{source} failed. Review the details before choosing what to do next."


def _tool_failure(
    call: ToolCall,
    result: dict[str, Any],
    identity: TurnIdentity,
) -> dict[str, Any]:
    detail = str(result.get("error") or "Unknown tool failure")
    connector_id, source = _tool_source(call.name)
    failure_class = _failure_class(detail)
    retry_safe = call.name in _SAFE_RETRY_TOOLS
    return {
        "class": failure_class,
        "connector_id": connector_id,
        "source": source,
        "retry_safe": retry_safe,
        "idempotent": retry_safe,
        "summary": _failure_summary(failure_class, source, retry_safe),
        "repair_route": (
            f"#/connections/{connector_id}"
            if failure_class == "connector_auth" and connector_id
            else None
        ),
        "detail": detail,
        "call_id": call.id,
        "run_id": identity.run_id,
        "session_id": identity.session_id,
        "state": "failed",
    }


def _tool_provenance(
    call: ToolCall, result: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _connector_id, default_provider = _tool_source(call.name)
    sources: list[dict[str, Any]] = []
    for index, raw in enumerate(result.get("sources") or []):
        if not isinstance(raw, dict):
            continue
        sources.append(
            {
                "id": str(raw.get("id") or f"{call.id}:source:{index}"),
                "title": str(raw.get("title") or raw.get("name") or "Source"),
                "url": str(raw["url"]) if raw.get("url") else None,
                "provider": str(raw.get("provider") or default_provider),
                "stale": bool(raw.get("stale", False)),
                "truncated": bool(raw.get("truncated", False)),
            }
        )
    artifacts: list[dict[str, Any]] = []
    for index, raw in enumerate(result.get("artifacts") or []):
        if not isinstance(raw, dict):
            continue
        artifacts.append(
            {
                "id": str(raw.get("id") or f"{call.id}:artifact:{index}"),
                "artifact_type": str(
                    raw.get("artifact_type") or raw.get("type") or "artifact"
                ),
                "title": str(raw.get("title") or "Generated artifact"),
                "preview": str(raw["preview"]) if raw.get("preview") else None,
                "external_url": (
                    str(raw["external_url"])
                    if raw.get("external_url")
                    else None
                ),
                "stale": bool(raw.get("stale", False)),
                "truncated": bool(raw.get("truncated", False)),
            }
        )
    return sources, artifacts


def _tool_finished_event(
    call: ToolCall,
    *,
    ok: bool,
    result: dict[str, Any],
    identity: TurnIdentity,
) -> dict[str, Any]:
    sources, artifacts = _tool_provenance(call, result)
    return {
        "type": "tool_finished",
        "id": call.id,
        "name": call.name,
        "ok": ok,
        "result": result,
        "finished_at": _now_iso(),
        **({"failure": _tool_failure(call, result, identity)} if not ok else {}),
        **({"sources": sources} if sources else {}),
        **({"artifacts": artifacts} if artifacts else {}),
    }


class RunControl:
    """Addressed cooperative control for one sidecar-authoritative turn."""

    def __init__(self, identity: TurnIdentity) -> None:
        self.identity = identity
        self.cancel_requested = asyncio.Event()
        self._events: TurnEventStream | None = None
        self._lock = asyncio.Lock()
        self._stopping_sent = False
        self._terminal_sent = False
        self.current_action: str | None = None

    @property
    def terminal(self) -> bool:
        return self._terminal_sent

    def abandon(self) -> None:
        """Mark terminal without emitting — the task died before its terminal."""
        self._terminal_sent = True

    async def attach(self, events: TurnEventStream) -> None:
        async with self._lock:
            self._events = events

    async def request_cancel(self) -> bool:
        async with self._lock:
            if self._terminal_sent:
                return False
            self.cancel_requested.set()
            if self._stopping_sent or self._events is None:
                return True
            message = (
                "Stopping after the current action finishes."
                if self.current_action
                else "Stopping the current run."
            )
            await self._events.send(
                {"type": "turn_stopping", "state": "stopping", "message": message}
            )
            self._stopping_sent = True
            return True

    async def send_terminal(self, event: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            if self._terminal_sent or self._events is None:
                return None
            self._terminal_sent = True
            return await self._events.send(event)

    async def finish_stopped(self, text: str) -> dict[str, Any] | None:
        return await self.send_terminal(
            {
                "type": "turn_stopped",
                "state": "stopped",
                "text": text,
                "message": "Run cancelled.",
            }
        )


class RunCoordinator:
    """Routes commands to turns without allowing another WebSocket reader."""

    def __init__(self) -> None:
        self._controls: dict[tuple[str, str], RunControl] = {}
        self._lock = threading.Lock()

    def register(self, control: RunControl) -> bool:
        """Admit at most one live run per session; evict superseded terminals."""
        session_id = control.identity.session_id
        key = (session_id, control.identity.run_id)
        with self._lock:
            stale: list[tuple[str, str]] = []
            for (sid, rid), existing in self._controls.items():
                if sid != session_id:
                    continue
                if not existing.terminal:
                    return False
                stale.append((sid, rid))
            for old in stale:
                del self._controls[old]
            self._controls[key] = control
            return True

    def get(self, session_id: str, run_id: str) -> RunControl | None:
        with self._lock:
            return self._controls.get((session_id, run_id))

    def latest_per_session(self) -> list[RunControl]:
        """The most recently registered control for each session."""
        with self._lock:
            latest: dict[str, RunControl] = {}
            for (sid, _), control in self._controls.items():
                latest[sid] = control
            return list(latest.values())

    async def cancel(self, session_id: str, run_id: str) -> bool:
        control = self.get(session_id, run_id)
        if control is None:
            return False
        return await control.request_cancel()

    def active_for(self, session_id: str) -> RunControl | None:
        with self._lock:
            for (sid, _), control in reversed(tuple(self._controls.items())):
                if sid == session_id and not control.terminal:
                    return control
            return None


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
    identity: TurnIdentity | None = None,
    control: RunControl | None = None,
) -> dict[str, Any]:
    events = TurnEventStream(
        identity=identity or new_turn_identity(sid),
        store=store,
        emit=emit,
    )
    if control is not None:
        await control.attach(events)

    async def _emit(event: dict[str, Any]) -> None:
        await events.send(event)

    def _stamp(message: dict[str, Any]) -> dict[str, Any]:
        """Identity for restore merges; stripped before the model sees it."""
        message["message_id"] = events.identity.message_id
        return message

    async def _terminal(event: dict[str, Any]) -> None:
        if control is None:
            await _emit(event)
        else:
            await control.send_terminal(event)

    async def _approval_receipt(
        item: dict[str, Any], *, resolution: str
    ) -> None:
        existing = next(
            (
                event
                for event in store.load_events(sid)
                if event.get("type") == "approval_resolved"
                and event.get("id") == item.get("id")
                and event.get("resolved_at") == item.get("resolved_at")
            ),
            None,
        )
        if existing is not None:
            if emit is not None:
                await emit(existing)
            return
        await _emit(
            {
                "type": "approval_resolved",
                "id": str(item["id"]),
                "name": str(item["name"]),
                "resolution": resolution,
                "decision": item.get("decision"),
                "actor": item.get("actor"),
                "requested_at": str(
                    item.get("requested_at") or item.get("created_at") or _now_iso()
                ),
                "resolved_at": str(item.get("resolved_at") or _now_iso()),
                "scope": str(item.get("scope") or "once"),
                "execution_status": str(
                    item.get("execution_status") or "pending"
                ),
                "execution_error": item.get("execution_error"),
            }
        )

    async def _stopped(
        history: list[dict[str, Any]], text_so_far: str
    ) -> dict[str, Any]:
        _persist_closed(store, sid, history)
        if control is not None:
            await control.finish_stopped(text_so_far)
        else:
            await _emit(
                {
                    "type": "turn_end",
                    "state": "stopped",
                    "text": text_so_far,
                    "message": "Run stopped.",
                }
            )
        return {"status": "stopped", "text": text_so_far}

    await _emit({"type": "turn_start", "state": "running"})
    if provider is None:
        await _terminal(
            {
                "type": "error",
                "state": "failed",
                "message": "No model key. Set DEEPSEEK_API_KEY (deepseek-v4-pro) or MOONSHOT_API_KEY (kimi-k3) in ~/.config/club/.env.",
            }
        )
        return {"status": "error", "text": ""}

    last_text = ""
    had_tool_failure = False
    history: list[dict[str, Any]] = []
    try:
        raw = store.load(sid)
        loaded = close_open_tool_calls(raw)
        if loaded != raw:
            store.replace_all(sid, loaded)
        sys_content = ""
        if system_prompt_fn is not None:
            sys_content = system_prompt_fn(
                store,
                persona,
                skills,
                people=execute_kwargs.get("people"),
                session_id=sid,
            )
        history = [{"role": "system", "content": sys_content}] + loaded
        # run_turn appends the user message for every caller. Skip only when
        # the transcript already ends with this exact text — a rerun of a turn
        # that died right after persisting its user message.
        if not (loaded and loaded[-1].get("role") == "user" and loaded[-1].get("content") == text):
            user_msg = {"role": "user", "content": text}
            history.append(user_msg)
            store.append(sid, user_msg)
        for _ in range(MAX_STEPS):
            chunks: list[str] = []
            calls: list[ToolCall] = []
            _persist_closed(store, sid, history)
            model_messages = [
                {k: v for k, v in message.items() if k != "message_id"}
                for message in history
            ]
            async for chunk in provider.astream(
                messages=model_messages, tools=openai_tools
            ):
                if control is not None and control.cancel_requested.is_set():
                    return await _stopped(history, "".join(chunks) or last_text)
                if chunk.text_delta:
                    chunks.append(chunk.text_delta)
                    await _emit({"type": "assistant_delta", "delta": chunk.text_delta})
                if chunk.tool_calls:
                    calls = chunk.tool_calls
            if control is not None and control.cancel_requested.is_set():
                return await _stopped(history, "".join(chunks) or last_text)
            last_text = "".join(chunks)
            if not calls:
                if last_text:
                    assistant_msg = _stamp(
                        {"role": "assistant", "content": last_text}
                    )
                    history.append(assistant_msg)
                    store.append(sid, assistant_msg)
                break
            tool_msg = _stamp(_assistant_tool_message(last_text, calls))
            history.append(tool_msg)
            store.append(sid, tool_msg)
            for call in calls:
                approval_claimant: str | None = None
                gate = decide(call.name)
                if not gate.allowed and not gate.needs_user:
                    result = {"error": gate.reason or "denied"}
                    had_tool_failure = True
                    await _emit(
                        _tool_finished_event(
                            call,
                            ok=False,
                            result=result,
                            identity=events.identity,
                        )
                    )
                    denied = _stamp(_tool_result_message(call, result))
                    history.append(denied)
                    store.append(sid, denied)
                    _record_person_file(sid, call, False, result, execute_kwargs)
                    continue
                if gate.needs_user:
                    resource = approval_resource(
                        call.name, call.arguments, execute_kwargs.get("gmail")
                    )
                    parked = inbox.park(
                        call.name,
                        call.arguments,
                        item_id=call.id,
                        reason=gate.reason,
                        session_id=sid,
                        run_id=events.identity.run_id,
                        message_id=events.identity.message_id,
                        part_id=events.identity.part_id,
                        resource=resource,
                    )
                    await _emit(
                        {
                            "type": "permission_required",
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "reason": gate.reason,
                            "requested_at": parked["requested_at"],
                            "scope": parked["scope"],
                            **({"resource": resource} if resource else {}),
                        }
                    )
                    if wait_permission is None:
                        return {"status": "waiting", "text": last_text}
                    choice = await wait_permission(call.id)
                    if choice == "cancel":
                        cancelled = inbox.cancel(call.id)
                        if cancelled is not None:
                            await _approval_receipt(
                                cancelled, resolution="cancelled"
                            )
                        else:
                            expired = inbox.get(call.id)
                            if (
                                expired is not None
                                and expired.get("state") == "expired"
                            ):
                                await _approval_receipt(
                                    expired, resolution="expired"
                                )
                        return await _stopped(history, last_text)
                    approval_claimant = f"turn:{events.identity.run_id}"
                    claim = inbox.decide_and_claim(
                        call.id,
                        choice,
                        actor=None,
                        scope=str(parked.get("scope") or "once"),
                        claimant=approval_claimant,
                    )
                    if claim is None:
                        result = {"error": "approval resolved elsewhere"}
                        had_tool_failure = True
                        await _emit(
                            _tool_finished_event(
                                call,
                                ok=False,
                                result=result,
                                identity=events.identity,
                            )
                        )
                        failed = _stamp(_tool_result_message(call, result))
                        history.append(failed)
                        store.append(sid, failed)
                        _record_person_file(
                            sid, call, False, result, execute_kwargs
                        )
                        continue
                    if choice == "deny":
                        receipt = claim.item
                        _ok, result = inbox.execution_outcome(receipt)
                        had_tool_failure = True
                        await _emit(
                            _tool_finished_event(
                                call,
                                ok=False,
                                result=result,
                                identity=events.identity,
                            )
                        )
                        denied = _stamp(_tool_result_message(call, result))
                        history.append(denied)
                        store.append(sid, denied)
                        _record_person_file(sid, call, False, result, execute_kwargs)
                        if receipt is not None:
                            await _approval_receipt(
                                receipt, resolution="denied"
                            )
                        continue
                    if not claim.owned:
                        receipt = await inbox.wait_for_execution(call.id)
                        if receipt is None:
                            ok, result = False, {
                                "error": "approval execution unavailable"
                            }
                        else:
                            ok, result = inbox.execution_outcome(receipt)
                        had_tool_failure = had_tool_failure or not ok
                        await _emit(
                            _tool_finished_event(
                                call,
                                ok=ok,
                                result=result,
                                identity=events.identity,
                            )
                        )
                        tool_result = _stamp(_tool_result_message(call, result))
                        history.append(tool_result)
                        store.append(sid, tool_result)
                        _record_person_file(sid, call, ok, result, execute_kwargs)
                        if receipt is not None and receipt.get(
                            "execution_status"
                        ) not in ("executing", "pending"):
                            await _approval_receipt(
                                receipt, resolution="allowed"
                            )
                        continue
                if control is not None:
                    control.current_action = call.name
                await _emit(
                    {
                        "type": "tool_started",
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "started_at": _now_iso(),
                    }
                )
                try:
                    kw = {k: v for k, v in execute_kwargs.items() if not k.startswith("_")}
                    kw["session_id"] = sid
                    ok, result = await asyncio.to_thread(
                        execute, call.name, call.arguments, **kw
                    )
                except Exception as exc:
                    ok, result = False, {"error": str(exc)}
                if call.name in {"remember", "memory_update", "memory_forget"} and system_prompt_fn:
                    history[0] = {
                        "role": "system",
                        "content": system_prompt_fn(
                            store,
                            persona,
                            skills,
                            people=execute_kwargs.get("people"),
                            session_id=sid,
                        ),
                    }
                had_tool_failure = had_tool_failure or not ok
                await _emit(
                    _tool_finished_event(
                        call,
                        ok=ok,
                        result=result,
                        identity=events.identity,
                    )
                )
                tool_result = _stamp(_tool_result_message(call, result))
                history.append(tool_result)
                store.append(sid, tool_result)
                _record_person_file(sid, call, ok, result, execute_kwargs)
                if approval_claimant is not None:
                    receipt = inbox.complete_execution(
                        call.id,
                        claimant=approval_claimant,
                        ok=ok,
                        result=result,
                    )
                    if receipt is not None:
                        await _approval_receipt(
                            receipt, resolution="allowed"
                        )
                if control is not None:
                    await asyncio.sleep(0)
                    if control.cancel_requested.is_set():
                        return await _stopped(history, last_text)
                    control.current_action = None
        else:
            await _terminal(
                {
                    "type": "turn_end",
                    "state": "stopped",
                    "text": last_text,
                    "message": f"Stopped after {MAX_STEPS} tool steps.",
                }
            )
            return {"status": "stopped", "text": last_text}
    except Exception as exc:
        _persist_closed(store, sid, history)
        await _terminal({"type": "error", "state": "failed", "message": str(exc)})
        return {"status": "error", "text": last_text}
    final_state = "partial" if had_tool_failure else "complete"
    await _terminal({"type": "turn_end", "text": last_text, "state": final_state})
    return {
        "status": "partial" if had_tool_failure else "ok",
        "text": last_text,
    }
