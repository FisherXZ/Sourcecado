"""Extracted turn loop — WS and scheduler share this path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from coworker.agent_run_execution import (
    EXECUTION_LEASE_SECONDS,
    AgentRunExecution,
    AgentRunExecutionOwnershipError,
)
from coworker.agent_run_heartbeat import AgentRunHeartbeat, AgentRunHeartbeatError
from coworker.agent_runs import safe_error_summary
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
    "drive_list_folder": ("drive", "Google Drive"),
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
        for marker in (
            "required",
            "requires",
            "invalid",
            "must be",
            "must provide",
            "malformed",
        )
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
    trigger: str = "chat",
    parent_run_id: str | None = None,
    lease_seconds: float | None = None,
) -> dict[str, Any]:
    execution_lease_seconds = (
        EXECUTION_LEASE_SECONDS if lease_seconds is None else lease_seconds
    )
    turn_identity = identity or new_turn_identity(sid)
    events = TurnEventStream(
        identity=turn_identity,
        store=store,
        emit=emit,
    )
    # The Agent Run is the durable authority and intentionally exists before
    # the presentation stream's turn_start event.
    try:
        execution = AgentRunExecution.start(
            store,
            turn_identity,
            text,
            trigger,
            (
                str(provider.model_id)
                if provider is not None and getattr(provider, "model_id", None)
                else None
            ),
            MAX_STEPS,
            parent_run_id=parent_run_id,
            lease_seconds=execution_lease_seconds,
        )
    except AgentRunExecutionOwnershipError:
        return {"status": "conflict", "text": "", "run_id": turn_identity.run_id}
    if control is not None:
        await control.attach(events)

    async def _emit(event: dict[str, Any]) -> dict[str, Any]:
        return await events.send(event)

    def _stamp(message: dict[str, Any]) -> dict[str, Any]:
        """Identity for restore merges; stripped before the model sees it."""
        message["message_id"] = events.identity.message_id
        return message

    def _durable_history() -> list[dict[str, Any]]:
        return store.load(sid)

    def _durable_events() -> list[dict[str, Any]]:
        return store.load_events(sid)

    def _close_durable_transcript() -> list[dict[str, Any]]:
        persisted = store.load(sid)
        closed = close_open_tool_calls(persisted)
        if closed != persisted:
            store.replace_all(sid, closed)
        return closed

    async def _terminal(event: dict[str, Any]) -> bool:
        state = str(event.get("state") or "failed")
        result_status = {
            "complete": "ok",
            "partial": "partial",
            "stopped": "stopped",
            "failed": "error",
        }.get(state, "error")
        final_text = str(event.get("text") or "")
        terminal_result = {
            "status": result_status,
            "message_id": events.identity.message_id,
            "text_length": len(final_text),
        }
        if state == "failed":
            terminal_result["error"] = safe_error_summary(
                str(event.get("message") or "Run failed.")
            )
            terminal_result["class"] = "run_error"
        try:
            terminal_history = _durable_history()
        except Exception:
            terminal_history = [
                message for message in history if message.get("role") != "system"
            ]
        execution.terminal(
            terminal_history,
            _durable_events(),
            state,
            result_status,
            events.identity.message_id,
            len(final_text),
            error=terminal_result.get("error"),
            error_class=terminal_result.get("class"),
        )
        try:
            if control is None:
                await _emit(event)
            else:
                await control.send_terminal(event)
        except Exception:
            # Durable terminal authority already won. Slice B2b can repair the
            # missing presentation projection; this caller must report failure.
            return False
        return True

    def _checkpoint_tool(
        call: ToolCall,
        *,
        ok: bool,
        result: dict[str, Any],
        step_index: int,
        tool_index: int,
    ) -> None:
        loaded_skills, sources, artifacts = _tool_checkpoint_aggregates(
            call, ok, result
        )
        execution.tool_completed(
            _durable_history(),
            _durable_events(),
            step_index,
            tool_index,
            call.id,
            call.name,
            ok,
            _result_digest(result),
            skills_loaded=loaded_skills,
            source_refs=sources,
            artifact_refs=artifacts,
        )

    def _tool_checkpoint_aggregates(
        call: ToolCall, ok: bool, result: dict[str, Any]
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        sources, artifacts = _tool_provenance(call, result)
        loaded_skills: list[str] = []
        if call.name == "load_skill" and ok:
            skill_name = str(result.get("name") or call.arguments.get("name") or "")
            if skill_name:
                loaded_skills.append(skill_name)
        return loaded_skills, sources, artifacts

    def _checkpoint_approved_tool(
        call: ToolCall,
        *,
        ok: bool,
        result: dict[str, Any],
        step_index: int,
        tool_index: int,
        claimant: str,
    ) -> dict[str, Any]:
        loaded_skills, sources, artifacts = _tool_checkpoint_aggregates(
            call, ok, result
        )
        return execution.complete_approved_tool(
            _durable_history(),
            _durable_events(),
            step_index,
            tool_index,
            call.id,
            call.name,
            ok,
            _result_digest(result),
            claimant=claimant,
            result=result,
            skills_loaded=loaded_skills,
            source_refs=sources,
            artifact_refs=artifacts,
        )

    def _result_digest(result: dict[str, Any]) -> str:
        canonical = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _checkpoint_skipped(
        call: ToolCall,
        *,
        result: dict[str, Any],
        step_index: int,
        tool_index: int,
        outcome: str = "denied",
    ) -> None:
        execution.tool_skipped(
            _durable_history(),
            _durable_events(),
            step_index,
            tool_index,
            call.id,
            call.name,
            _result_digest(result),
            outcome=outcome,
        )

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
        else:
            await _emit(
                {
                    "type": "approval_resolved",
                    "id": str(item["id"]),
                    "name": str(item["name"]),
                    "resolution": resolution,
                    "decision": item.get("decision"),
                    "actor": item.get("actor"),
                    "requested_at": str(
                        item.get("requested_at")
                        or item.get("created_at")
                        or _now_iso()
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
        projected = await _terminal(
            {
                "type": "turn_stopped" if control is not None else "turn_end",
                "state": "stopped",
                "text": text_so_far,
                "message": "Run cancelled." if control is not None else "Run stopped.",
            }
        )
        return {
            "status": "stopped" if projected else "error",
            "text": text_so_far,
            "run_id": events.identity.run_id,
        }

    async def _interrupt_inflight_tool(text_so_far: str) -> dict[str, Any]:
        try:
            durable_history = _close_durable_transcript()
            if approval_claimant is None:
                execution.interrupt_inflight_tool(
                    durable_history, _durable_events()
                )
            else:
                execution.interrupt_approved_inflight_tool(
                    durable_history,
                    _durable_events(),
                    claimant=approval_claimant,
                )
        except Exception:
            return {
                "status": "error",
                "text": text_so_far,
                "run_id": events.identity.run_id,
            }
        return await _project_interrupted(
            text_so_far,
            "Run interrupted after a tool result could not be durably recorded.",
        )

    async def _project_interrupted(
        text_so_far: str, message: str
    ) -> dict[str, Any]:
        event = {
            "type": "turn_end",
            "state": "interrupted",
            "text": text_so_far,
            "message": message,
        }
        try:
            if control is None:
                await _emit(event)
            else:
                await control.send_terminal(event)
        except Exception:
            return {
                "status": "error",
                "text": text_so_far,
                "run_id": events.identity.run_id,
            }
        return {
            "status": "interrupted",
            "text": text_so_far,
            "run_id": events.identity.run_id,
        }

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
        await _emit({"type": "turn_start", "state": "running"})
        execution.user_input(_durable_history(), _durable_events(), len(text))
        if provider is None:
            await _terminal(
                {
                    "type": "error",
                    "state": "failed",
                    "message": "No model key. Set DEEPSEEK_API_KEY (deepseek-v4-pro) or MOONSHOT_API_KEY (kimi-k3) in ~/.config/club/.env.",
                }
            )
            return {"status": "error", "text": "", "run_id": events.identity.run_id}
        for step_index in range(MAX_STEPS):
            chunks: list[str] = []
            calls: list[ToolCall] = []
            _persist_closed(store, sid, history)
            execution.renew()
            execution.model_pending(
                _durable_history(), _durable_events(), step_index
            )
            model_messages = [
                {k: v for k, v in message.items() if k != "message_id"}
                for message in history
            ]
            cancelled_during_provider = False
            async with AgentRunHeartbeat(execution) as heartbeat:
                async for chunk in provider.astream(
                    messages=model_messages, tools=openai_tools
                ):
                    heartbeat.raise_if_failed()
                    if control is not None and control.cancel_requested.is_set():
                        cancelled_during_provider = True
                        break
                    if chunk.text_delta:
                        chunks.append(chunk.text_delta)
                        await _emit(
                            {"type": "assistant_delta", "delta": chunk.text_delta}
                        )
                    if chunk.tool_calls:
                        calls = chunk.tool_calls
            if cancelled_during_provider:
                return await _stopped(history, "".join(chunks) or last_text)
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
                execution.model_completed(
                    _durable_history(),
                    _durable_events(),
                    step_index,
                    0,
                    len(last_text),
                )
                break
            tool_msg = _stamp(_assistant_tool_message(last_text, calls))
            history.append(tool_msg)
            store.append(sid, tool_msg)
            execution.model_completed(
                _durable_history(),
                _durable_events(),
                step_index,
                len(calls),
                len(last_text),
            )
            for tool_index, call in enumerate(calls):
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
                    _checkpoint_skipped(
                        call,
                        result=result,
                        step_index=step_index,
                        tool_index=tool_index,
                    )
                    continue
                if gate.needs_user:
                    resource = approval_resource(
                        call.name, call.arguments, execute_kwargs.get("gmail")
                    )
                    parked = execution.waiting_approval_atomic(
                        _durable_history(),
                        _durable_events(),
                        call.id,
                        step_index,
                        tool_index,
                        call.id,
                        call.name,
                        call.name in _SAFE_RETRY_TOOLS,
                        arguments=call.arguments,
                        reason=gate.reason,
                        resource=resource,
                        approval_ttl_seconds=store.approval_ttl_seconds,
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
                        return {
                            "status": "waiting",
                            "text": last_text,
                            "run_id": events.identity.run_id,
                        }
                    choice = await wait_permission(call.id)
                    if choice == "cancel":
                        cancelled = inbox.cancel(call.id)
                        if cancelled is not None:
                            execution = AgentRunExecution.resume_closed_approval(
                                store,
                                events.identity.run_id,
                                call.id,
                                "cancelled",
                                MAX_STEPS,
                                lease_seconds=execution_lease_seconds,
                            )
                            await _approval_receipt(
                                cancelled, resolution="cancelled"
                            )
                        else:
                            expired = inbox.get(call.id)
                            if (
                                expired is not None
                                and expired.get("state") == "expired"
                            ):
                                execution = AgentRunExecution.resume_closed_approval(
                                    store,
                                    events.identity.run_id,
                                    call.id,
                                    "expired",
                                    MAX_STEPS,
                                    lease_seconds=execution_lease_seconds,
                                )
                                await _approval_receipt(
                                    expired, resolution="expired"
                                )
                        return await _stopped(history, last_text)
                    approval_claimant = f"turn:{events.identity.run_id}"
                    persisted_approval = inbox.get(call.id)
                    persisted_decision = (
                        persisted_approval.get("decision")
                        if persisted_approval is not None
                        and persisted_approval.get("state") == "resolved"
                        else None
                    )
                    bound_choice = (
                        str(persisted_decision)
                        if persisted_decision in {"allow", "deny"}
                        else choice
                    )
                    claim = inbox.decide_and_claim(
                        call.id,
                        bound_choice,
                        actor=None,
                        scope=str(parked.get("scope") or "once"),
                        claimant=approval_claimant,
                    )
                    if claim is None:
                        return {
                            "status": "conflict",
                            "text": last_text,
                            "run_id": events.identity.run_id,
                        }
                    execution = AgentRunExecution.resume_resolved_approval(
                        store,
                        events.identity.run_id,
                        call.id,
                        MAX_STEPS,
                        lease_seconds=execution_lease_seconds,
                    )
                    resolved_decision = str(claim.item.get("decision") or "")
                    if resolved_decision == "deny":
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
                        execution.approval_resolved(
                            _durable_history(), _durable_events(), call.id
                        )
                        continue
                    execution.approval_resolved(
                        _durable_history(), _durable_events(), call.id
                    )
                    if not claim.owned:
                        execution.waiting_external_execution(
                            _durable_history(),
                            _durable_events(),
                            call.id,
                            step_index,
                            tool_index,
                            call.id,
                            call.name,
                        )
                        receipt = await inbox.wait_for_execution(call.id)
                        execution_status = (
                            str(receipt.get("execution_status"))
                            if receipt is not None
                            else "unavailable"
                        )
                        if execution_status in {"pending", "executing"}:
                            return {
                                "status": "waiting",
                                "text": last_text,
                                "run_id": events.identity.run_id,
                            }
                        execution = AgentRunExecution.resume_external_completion(
                            store,
                            events.identity.run_id,
                            call.id,
                            MAX_STEPS,
                            lease_seconds=execution_lease_seconds,
                        )
                        receipt = execution.adopted_external_receipt
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
                        await _approval_receipt(receipt, resolution="allowed")
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
                execution.renew()
                execution.tool_pending(
                    _durable_history(),
                    _durable_events(),
                    step_index,
                    tool_index,
                    call.id,
                    call.name,
                    call.name in _SAFE_RETRY_TOOLS,
                )
                try:
                    kw = {k: v for k, v in execute_kwargs.items() if not k.startswith("_")}
                    kw["session_id"] = sid
                    async with AgentRunHeartbeat(execution):
                        ok, result = await asyncio.to_thread(
                            execute, call.name, call.arguments, **kw
                        )
                except AgentRunHeartbeatError:
                    raise
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
                    receipt = _checkpoint_approved_tool(
                        call,
                        ok=ok,
                        result=result,
                        step_index=step_index,
                        tool_index=tool_index,
                        claimant=approval_claimant,
                    )
                    await _approval_receipt(receipt, resolution="allowed")
                else:
                    _checkpoint_tool(
                        call,
                        ok=ok,
                        result=result,
                        step_index=step_index,
                        tool_index=tool_index,
                    )
                if control is not None:
                    await asyncio.sleep(0)
                    if control.cancel_requested.is_set():
                        return await _stopped(history, last_text)
                    control.current_action = None
        else:
            projected = await _terminal(
                {
                    "type": "turn_end",
                    "state": "stopped",
                    "text": last_text,
                    "message": f"Stopped after {MAX_STEPS} tool steps.",
                }
            )
            return {
                "status": "stopped" if projected else "error",
                "text": last_text,
                "run_id": events.identity.run_id,
            }
    except Exception as exc:
        if execution.metadata.get("phase") == "tool_in_flight":
            return await _interrupt_inflight_tool(last_text)
        if execution.current_lease is None:
            run = store.get_agent_run(events.identity.run_id)
            if run is not None and run.get("current_state") == "interrupted":
                return await _project_interrupted(
                    last_text,
                    "Run interrupted after execution authority was lost.",
                )
            if run is not None and run.get("current_state") in {
                "waiting_approval",
                "waiting_question",
            }:
                return {
                    "status": "error",
                    "text": last_text,
                    "run_id": events.identity.run_id,
                }
            return {
                "status": "conflict",
                "text": last_text,
                "run_id": events.identity.run_id,
            }
        _persist_closed(store, sid, history)
        await _terminal(
            {
                "type": "error",
                "state": "failed",
                "message": safe_error_summary(str(exc)),
            }
        )
        return {
            "status": "error",
            "text": last_text,
            "run_id": events.identity.run_id,
        }
    final_state = "partial" if had_tool_failure else "complete"
    projected = await _terminal(
        {"type": "turn_end", "text": last_text, "state": final_state}
    )
    if not projected:
        return {
            "status": "error",
            "text": last_text,
            "run_id": events.identity.run_id,
        }
    return {
        "status": "partial" if had_tool_failure else "ok",
        "text": last_text,
        "run_id": events.identity.run_id,
    }
