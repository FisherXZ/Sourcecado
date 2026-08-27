"""Extracted turn loop — WS and scheduler share this path."""

from __future__ import annotations

import asyncio
import json
import random
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from coworker.events import TurnEventStream, TurnIdentity, build_event, new_turn_identity
from coworker.inbox import Inbox
from coworker.ledger import record_tool_on_person
from coworker.permissions import RETRY_SAFE, decide
from coworker.provider import (
    ModelUsage,
    ProviderErrorKind,
    ProviderStart,
    ProviderStreamError,
    StreamKind,
    ToolCall,
    provider_model_metadata,
)
from coworker.provider_retry import (
    ProviderRequestCancelled,
    RecoveryAction,
    RetryController,
    RetryPolicy,
    cancellable_stream,
    compatible_failover_chain,
    safe_provider_failure_message,
)
from coworker.store import ConversationStore
from coworker.telemetry import (
    AgentTurnSpan,
    CostEstimate,
    ErrorKind,
    ProviderSpan,
    RetryEvent,
    RetryReason,
    StopReason,
    TelemetryRecorder,
    TraceContext,
    ToolSpan,
    UsageEvent,
)
from coworker.tools import execute

MAX_STEPS = 8
INTERRUPTED_TOOL = '{"error": "tool call interrupted"}'


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _telemetry_provider_name(provider: Any) -> str:
    provider_id = str(getattr(provider, "provider_id", ""))
    if provider_id:
        return provider_id
    name = type(provider).__name__.lower()
    name = name.removesuffix("provider") or "unknown"
    if name != "openaicompat":
        return name
    base_url = str(getattr(provider, "base_url", "")).lower()
    if "moonshot.ai" in base_url:
        return "moonshot"
    if "openai.com" in base_url:
        return "openai"
    return "openai_compatible"


def _telemetry_provider_span(
    provider: Any,
    start: ProviderStart | None = None,
) -> ProviderSpan:
    provider_name = start.provider if start is not None else _telemetry_provider_name(provider)
    model = start.model if start is not None else str(
        getattr(provider, "model_id", "unknown")
    )
    metadata = provider_model_metadata(provider_name, model)
    try:
        return ProviderSpan(
            provider=provider_name,
            model=model,
            operation="provider.request",
            context_window_tokens=metadata.context_window_tokens,
        )
    except ValueError:
        return ProviderSpan(
            provider="unknown",
            model="unknown",
            operation="provider.request",
        )


def _telemetry_tool_span(name: str) -> ToolSpan:
    try:
        return ToolSpan(tool_name=name, operation="tool.execute")
    except ValueError:
        return ToolSpan(tool_name="unknown", operation="tool.execute")


def _telemetry_usage(
    usage: ModelUsage,
    *,
    context_window_tokens: int | None = None,
) -> UsageEvent:
    values = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "cache_hit_input_tokens": usage.cached_input_tokens,
        "cache_miss_input_tokens": usage.uncached_input_tokens,
        "cache_write_input_tokens": usage.cache_write_input_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "current_context_tokens": usage.input_tokens,
    }
    try:
        return UsageEvent(
            **values,
            context_window_tokens=context_window_tokens,
        )
    except ValueError:
        return UsageEvent(**values)


def _telemetry_stop_reason(reason: str | None, *, used_tools: bool) -> StopReason:
    if reason == "tool_calls" or (reason is None and used_tools):
        return StopReason.TOOL_USE
    if reason == "length":
        return StopReason.MAX_TOKENS
    if reason == "stop" or reason is None:
        return StopReason.COMPLETED
    if reason == "insufficient_system_resource":
        return StopReason.ERROR
    return StopReason.UNKNOWN


def _telemetry_provider_error_kind(error: Exception) -> ErrorKind:
    if isinstance(error, ProviderStreamError):
        return {
            ProviderErrorKind.AUTHENTICATION: ErrorKind.AUTHENTICATION,
            ProviderErrorKind.RATE_LIMIT: ErrorKind.RATE_LIMIT,
            ProviderErrorKind.TIMEOUT: ErrorKind.TIMEOUT,
            ProviderErrorKind.CONNECTION: ErrorKind.CONNECTION,
            ProviderErrorKind.INVALID_REQUEST: ErrorKind.INVALID_REQUEST,
            ProviderErrorKind.CONFIGURATION: ErrorKind.INVALID_REQUEST,
            ProviderErrorKind.PROTOCOL: ErrorKind.PROVIDER,
            ProviderErrorKind.PROVIDER: ErrorKind.PROVIDER,
        }[error.error_kind]
    if isinstance(error, TimeoutError):
        return ErrorKind.TIMEOUT
    if isinstance(error, ConnectionError):
        return ErrorKind.CONNECTION
    return ErrorKind.PROVIDER


_TOOL_SOURCES: dict[str, tuple[str | None, str]] = {
    "gmail_search": ("gmail", "Gmail"),
    "gmail_read": ("gmail", "Gmail"),
    "gmail_draft": ("gmail", "Gmail"),
    "drive_search": ("drive", "Google Drive"),
    "drive_list_folder": ("drive", "Google Drive"),
    "drive_read": ("drive", "Google Drive"),
    "board_get": (None, "Board"),
    "board_query": (None, "Board"),
    "board_upsert": (None, "Board"),
    "board_mutate": (None, "Board"),
    "board_delete": (None, "Board"),
    "calendar_list": ("calendar", "Google Calendar"),
    "calendar_create": ("calendar", "Google Calendar"),
    "calendar_update": ("calendar", "Google Calendar"),
    "apollo_search_people": ("apollo", "Apollo"),
    "apollo_enrich_contact": ("apollo", "Apollo"),
}

_SAFE_RETRY_TOOLS = RETRY_SAFE


def approval_resource(
    name: str,
    arguments: dict[str, Any],
    gmail: Any,
    workspace_runtime: Any = None,
) -> dict[str, Any] | None:
    """The few fields an operator needs to judge a gmail_send: recipient,
    subject, account. Never the body, tokens, or raw headers (DU-12)."""
    if name == "shell_exec" and workspace_runtime is not None:
        try:
            return workspace_runtime.shell.approval_resource(
                grant_id=str(arguments.get("grant_id") or ""),
                command=str(arguments.get("command") or ""),
                cwd=str(arguments.get("cwd") or "."),
                environment=(
                    arguments.get("environment")
                    if isinstance(arguments.get("environment"), dict)
                    else {}
                ),
            )
        except Exception:
            return {
                "kind": "shell_command",
                "execution_target": "unknown",
                "command_summary": "Command details unavailable",
                "cwd": None,
                "fingerprint": None,
                "unsandboxed": True,
            }
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
                    str(raw["external_url"]) if raw.get("external_url") else None
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
                fn = (
                    call.get("function")
                    if isinstance(call.get("function"), dict)
                    else {}
                )
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
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                },
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


def _persist_closed(
    store: ConversationStore,
    sid: str,
    history: list[dict[str, Any]],
    workspace_runtime: Any = None,
) -> None:
    closed = close_open_tool_calls(history)
    if closed == history:
        return
    history[:] = closed
    messages = [m for m in closed if m.get("role") != "system"]
    if workspace_runtime is not None:
        messages = [workspace_runtime.sanitize_message(message) for message in messages]
    store.replace_all(sid, messages)


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
    telemetry: TelemetryRecorder | None = None,
    failover_providers: tuple[Any, ...] = (),
    retry_policy: RetryPolicy | None = None,
    retry_sleep: Callable[[float], Awaitable[None]] | None = None,
    retry_random: Callable[[], float] = random.random,
) -> dict[str, Any]:
    workspace_runtime = execute_kwargs.get("workspace_runtime")
    events = TurnEventStream(
        identity=identity or new_turn_identity(sid),
        store=store,
        emit=emit,
        persist_transform=(
            workspace_runtime.sanitize_event if workspace_runtime is not None else None
        ),
    )
    if control is not None:
        await control.attach(events)
    recorder = telemetry or TelemetryRecorder()
    trace_context = TraceContext(
        session_id=events.identity.session_id,
        run_id=events.identity.run_id,
    )
    turn_span = recorder.start_span(
        AgentTurnSpan(operation="agent.turn"), trace_context
    )

    async def _emit(event: dict[str, Any]) -> None:
        await events.send(event)

    def _stamp(message: dict[str, Any]) -> dict[str, Any]:
        """Identity for restore merges; stripped before the model sees it."""
        message["message_id"] = events.identity.message_id
        return message

    def _persist_message(message: dict[str, Any]) -> None:
        store.append(
            sid,
            workspace_runtime.sanitize_message(message)
            if workspace_runtime is not None
            else message,
        )

    async def _terminal(event: dict[str, Any]) -> None:
        if control is None:
            await _emit(event)
        else:
            await control.send_terminal(event)

    async def _approval_receipt(item: dict[str, Any], *, resolution: str) -> None:
        workspace_runtime = execute_kwargs.get("workspace_runtime")
        event = build_event(
            events.identity,
            "approval_resolved",
            event_id=f"event_{uuid.uuid4().hex}",
            id=str(item["id"]),
            name=str(item["name"]),
            resolution=resolution,
            decision=item.get("decision"),
            actor=item.get("actor"),
            requested_at=str(
                item.get("requested_at") or item.get("created_at") or _now_iso()
            ),
            resolved_at=str(item.get("resolved_at") or _now_iso()),
            scope=str(item.get("scope") or "once"),
            execution_status=str(item.get("execution_status") or "pending"),
            execution_error=item.get("execution_error"),
        )
        persisted = (
            workspace_runtime.sanitize_event(event)
            if workspace_runtime is not None
            else event
        )
        canonical, created = store.append_event_once(
            sid,
            persisted,
            matching_fields=("type", "id", "resolved_at"),
        )
        if created and workspace_runtime is not None:
            workspace_runtime.record_permission_decision(item)
        if emit is not None:
            await emit(event if created else canonical)
        if workspace_runtime is not None and (
            resolution != "allowed"
            or str(item.get("execution_status") or "") == "succeeded"
        ):
            workspace_runtime.discard_parked_arguments(str(item.get("id") or ""))

    async def _stopped(
        history: list[dict[str, Any]], text_so_far: str
    ) -> dict[str, Any]:
        turn_span.cancel()
        _persist_closed(store, sid, history, workspace_runtime)
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

    people = execute_kwargs.get("people")
    if people is not None:
        bound_person_id = people.person_for_session(sid)
        if bound_person_id is not None and people.get(bound_person_id) is None:
            turn_span.fail(ErrorKind.POLICY)
            await _terminal(
                {
                    "type": "error",
                    "state": "failed",
                    "message": "This conversation's bound person file is unavailable.",
                }
            )
            return {"status": "error", "text": ""}

    await _emit({"type": "turn_start", "state": "running"})
    if provider is None:
        turn_span.fail(ErrorKind.PROVIDER)
        await _terminal(
            {
                "type": "error",
                "state": "failed",
                "message": "No model key. Set DEEPSEEK_API_KEY (deepseek-v4-pro) or MOONSHOT_API_KEY (kimi-k3) in ~/.config/club/.env.",
            }
        )
        return {"status": "error", "text": ""}

    provider_chain = (provider,) + tuple(
        candidate
        for candidate in failover_providers
        if candidate is not provider
    )
    selected_provider = provider
    retry_count = 0

    last_text = ""
    had_tool_failure = False
    turn_error_kind = ErrorKind.INTERNAL
    turn_error_message: str | None = None
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
        if not (
            loaded
            and loaded[-1].get("role") == "user"
            and loaded[-1].get("content") == text
        ):
            user_msg = {"role": "user", "content": text}
            history.append(user_msg)
            _persist_message(user_msg)
        for _ in range(MAX_STEPS):
            _persist_closed(store, sid, history, workspace_runtime)
            model_messages = [
                {k: v for k, v in message.items() if k != "message_id"}
                for message in history
            ]
            selected_index = provider_chain.index(selected_provider)
            attempt_chain = compatible_failover_chain(
                provider_chain[selected_index:],
                selected_provider=selected_provider,
                messages=model_messages,
            )
            controller = RetryController(
                attempt_chain,
                policy=retry_policy or RetryPolicy(),
                sleep=retry_sleep or asyncio.sleep,
                random_value=retry_random,
                cancel_event=(control.cancel_requested if control is not None else None),
            )
            while True:
                attempt_provider = controller.provider
                chunks: list[str] = []
                calls: list[ToolCall] = []
                provider_usage: UsageEvent | None = None
                provider_finish_reason: str | None = None
                provider_cost: CostEstimate | None = None
                provider_span = None
                provider_context_window: int | None = None
                meaningful_stream = False
                stream_kwargs: dict[str, Any] = {
                    "messages": model_messages,
                    "tools": openai_tools,
                }
                if getattr(attempt_provider, "uses_transient_context", False):
                    stream_kwargs["context_id"] = sid

                def _ensure_provider_span(start: ProviderStart | None = None):
                    nonlocal provider_span, provider_context_window
                    if provider_span is None:
                        schema = _telemetry_provider_span(attempt_provider, start)
                        provider_context_window = schema.context_window_tokens
                        provider_span = turn_span.child(schema)
                    return provider_span

                try:
                    async for chunk in cancellable_stream(
                        attempt_provider.astream(**stream_kwargs),
                        cancel_event=(
                            control.cancel_requested if control is not None else None
                        ),
                    ):
                        if chunk.kind is StreamKind.START and chunk.start is not None:
                            _ensure_provider_span(chunk.start)
                            continue
                        if chunk.kind is StreamKind.REASONING:
                            continue
                        if chunk.kind is not None:
                            meaningful_stream = True
                        active_provider_span = _ensure_provider_span()
                        if control is not None and control.cancel_requested.is_set():
                            active_provider_span.cancel(
                                usage=provider_usage,
                                cost=provider_cost,
                            )
                            return await _stopped(
                                history, "".join(chunks) or last_text
                            )
                        if chunk.text_delta:
                            chunks.append(chunk.text_delta)
                            await _emit(
                                {"type": "assistant_delta", "delta": chunk.text_delta}
                            )
                        if chunk.tool_calls:
                            calls = chunk.tool_calls
                        if chunk.usage is not None:
                            provider_usage = _telemetry_usage(
                                chunk.usage,
                                context_window_tokens=provider_context_window,
                            )
                            active_provider_span.record(provider_usage)
                        if (
                            chunk.kind is StreamKind.TERMINAL
                            and chunk.terminal is not None
                        ):
                            provider_finish_reason = chunk.terminal.stop_reason
                            if chunk.terminal.usage is not None:
                                terminal_usage = _telemetry_usage(
                                    chunk.terminal.usage,
                                    context_window_tokens=provider_context_window,
                                )
                                if provider_usage is None:
                                    active_provider_span.record(terminal_usage)
                                provider_usage = terminal_usage
                            if chunk.terminal.estimated_cost_usd is not None:
                                try:
                                    provider_cost = CostEstimate(
                                        estimated_cost_usd=(
                                            chunk.terminal.estimated_cost_usd
                                        )
                                    )
                                except ValueError:
                                    provider_cost = None
                        if chunk.finish_reason is not None:
                            provider_finish_reason = chunk.finish_reason
                except ProviderRequestCancelled:
                    _ensure_provider_span().cancel(
                        usage=provider_usage,
                        cost=provider_cost,
                    )
                    return await _stopped(
                        history, "".join(chunks) or last_text
                    )
                except asyncio.CancelledError:
                    _ensure_provider_span().cancel(
                        usage=provider_usage,
                        cost=provider_cost,
                    )
                    turn_span.cancel()
                    raise
                except Exception as exc:
                    error_kind = _telemetry_provider_error_kind(exc)
                    _ensure_provider_span().fail(
                        error_kind,
                        usage=provider_usage,
                        cost=provider_cost,
                    )
                    directive = await controller.recover(
                        exc,
                        partial_stream=meaningful_stream,
                    )
                    if directive.action in {
                        RecoveryAction.RETRY,
                        RecoveryAction.FAILOVER,
                    }:
                        retry_count += 1
                        turn_span.record(
                            RetryEvent(
                                operation="provider.request",
                                retry_count=retry_count,
                                reason=(
                                    directive.reason
                                    or RetryReason.TRANSIENT_PROVIDER
                                ),
                                delay_ms=round(directive.delay_seconds * 1000),
                            )
                        )
                        recovery_provider = _telemetry_provider_span(
                            directive.provider
                        )
                        await _emit(
                            {
                                "type": "provider_recovery",
                                "action": directive.action.value,
                                "provider": recovery_provider.provider,
                                "model": recovery_provider.model,
                                "attempt": directive.attempt_number,
                                "reason": (
                                    directive.reason
                                    or RetryReason.TRANSIENT_PROVIDER
                                ).value,
                                "delay_ms": round(
                                    directive.delay_seconds * 1000
                                ),
                                "message": (
                                    "Switching to a verified fallback model provider."
                                    if directive.action is RecoveryAction.FAILOVER
                                    else "Retrying the model provider after a temporary failure."
                                ),
                            }
                        )
                        if directive.action is RecoveryAction.FAILOVER:
                            selected_provider = directive.provider
                        continue
                    if directive.action is RecoveryAction.CANCELLED:
                        return await _stopped(
                            history, "".join(chunks) or last_text
                        )
                    if directive.action is RecoveryAction.REVIEW:
                        turn_error_kind = error_kind
                        turn_error_message = safe_provider_failure_message(
                            exc,
                            review_required=True,
                        )
                        raise RuntimeError(
                            turn_error_message
                        ) from exc
                    turn_error_kind = error_kind
                    turn_error_message = safe_provider_failure_message(exc)
                    raise
                _ensure_provider_span().finish(
                    stop_reason=_telemetry_stop_reason(
                        provider_finish_reason,
                        used_tools=bool(calls),
                    ),
                    usage=provider_usage,
                    cost=provider_cost,
                )
                selected_provider = attempt_provider
                break
            if control is not None and control.cancel_requested.is_set():
                return await _stopped(history, "".join(chunks) or last_text)
            last_text = "".join(chunks)
            if not calls:
                if last_text:
                    assistant_msg = _stamp({"role": "assistant", "content": last_text})
                    history.append(assistant_msg)
                    _persist_message(assistant_msg)
                break
            tool_msg = _stamp(_assistant_tool_message(last_text, calls))
            history.append(tool_msg)
            _persist_message(tool_msg)
            for call in calls:
                approval_claimant: str | None = None
                approval_scope = "once"
                approval_fingerprint: str | None = None
                workspace_runtime = execute_kwargs.get("workspace_runtime")
                if workspace_runtime is not None and workspace_runtime.owns_tool(
                    call.name
                ):
                    workspace_runtime.park_arguments(
                        call.id, call.name, call.arguments
                    )
                gate = decide(
                    call.name,
                    call.arguments,
                    workspace_runtime=execute_kwargs.get("workspace_runtime"),
                    actor=str(execute_kwargs.get("actor") or "assistant"),
                    session_id=sid,
                    run_id=events.identity.run_id,
                )
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
                    _persist_message(denied)
                    _record_person_file(sid, call, False, result, execute_kwargs)
                    continue
                if gate.needs_user:
                    resource = approval_resource(
                        call.name,
                        call.arguments,
                        execute_kwargs.get("gmail"),
                        execute_kwargs.get("workspace_runtime"),
                    )
                    persisted_arguments = call.arguments
                    if workspace_runtime is not None and workspace_runtime.owns_tool(
                        call.name
                    ):
                        persisted_arguments = workspace_runtime.park_arguments(
                            call.id, call.name, call.arguments
                        )
                    parked = inbox.park(
                        call.name,
                        persisted_arguments,
                        item_id=call.id,
                        reason=gate.reason,
                        session_id=sid,
                        run_id=events.identity.run_id,
                        message_id=events.identity.message_id,
                        part_id=events.identity.part_id,
                        resource=(
                            workspace_runtime.sanitize_resource(resource)
                            if workspace_runtime is not None
                            else resource
                        ),
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
                            await _approval_receipt(cancelled, resolution="cancelled")
                        else:
                            expired = inbox.get(call.id)
                            if (
                                expired is not None
                                and expired.get("state") == "expired"
                            ):
                                await _approval_receipt(expired, resolution="expired")
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
                        _persist_message(failed)
                        _record_person_file(sid, call, False, result, execute_kwargs)
                        continue
                    approval_scope = str(claim.item.get("scope") or "once")
                    approval_resource_payload = claim.item.get("resource")
                    if isinstance(approval_resource_payload, dict):
                        raw_fingerprint = approval_resource_payload.get("fingerprint")
                        if isinstance(raw_fingerprint, str):
                            approval_fingerprint = raw_fingerprint
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
                        _persist_message(denied)
                        _record_person_file(sid, call, False, result, execute_kwargs)
                        if receipt is not None:
                            await _approval_receipt(receipt, resolution="denied")
                        continue
                    if not claim.owned:
                        receipt = await inbox.wait_for_execution(call.id)
                        if receipt is None:
                            ok, result = (
                                False,
                                {"error": "approval execution unavailable"},
                            )
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
                        _persist_message(tool_result)
                        _record_person_file(sid, call, ok, result, execute_kwargs)
                        if receipt is not None and receipt.get(
                            "execution_status"
                        ) not in ("executing", "pending"):
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
                tool_span = turn_span.child(_telemetry_tool_span(call.name))
                try:
                    kw = {
                        k: v for k, v in execute_kwargs.items() if not k.startswith("_")
                    }
                    kw["session_id"] = sid
                    kw["run_id"] = events.identity.run_id
                    kw["approval_granted"] = approval_claimant is not None
                    kw["approval_scope"] = approval_scope
                    kw["approval_fingerprint"] = approval_fingerprint
                    if approval_claimant is not None:
                        kw["actor"] = str(claim.item.get("actor") or "operator")
                    ok, result = await asyncio.to_thread(
                        execute, call.name, call.arguments, **kw
                    )
                except asyncio.CancelledError:
                    tool_span.cancel()
                    raise
                except Exception as exc:
                    ok, result = False, {"error": str(exc)}
                if ok:
                    tool_span.finish()
                else:
                    tool_span.partial(ErrorKind.TOOL)
                if (
                    call.name in {"remember", "memory_update", "memory_forget"}
                    and system_prompt_fn
                ):
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
                _persist_message(tool_result)
                _record_person_file(sid, call, ok, result, execute_kwargs)
                if approval_claimant is not None:
                    receipt = inbox.complete_execution(
                        call.id,
                        claimant=approval_claimant,
                        ok=ok,
                        result=result,
                    )
                    if receipt is not None:
                        await _approval_receipt(receipt, resolution="allowed")
                if control is not None:
                    await asyncio.sleep(0)
                    if control.cancel_requested.is_set():
                        return await _stopped(history, last_text)
                    control.current_action = None
        else:
            turn_span.cancel()
            await _terminal(
                {
                    "type": "turn_end",
                    "state": "stopped",
                    "text": last_text,
                    "message": f"Stopped after {MAX_STEPS} tool steps.",
                }
            )
            return {"status": "stopped", "text": last_text}
    except asyncio.CancelledError:
        turn_span.cancel()
        raise
    except Exception as exc:
        turn_span.fail(turn_error_kind)
        _persist_closed(store, sid, history, workspace_runtime)
        await _terminal(
            {
                "type": "error",
                "state": "failed",
                "message": turn_error_message or str(exc),
            }
        )
        return {"status": "error", "text": last_text}
    final_state = "partial" if had_tool_failure else "complete"
    await _terminal({"type": "turn_end", "text": last_text, "state": final_state})
    if had_tool_failure:
        turn_span.partial(ErrorKind.TOOL)
    else:
        turn_span.finish()
    return {
        "status": "partial" if had_tool_failure else "ok",
        "text": last_text,
    }
