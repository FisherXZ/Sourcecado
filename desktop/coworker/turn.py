"""Extracted turn loop — WS and scheduler share this path."""

from __future__ import annotations

import asyncio
import json
import random
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from coworker.agent_run_dispatch import (
    AgentRunContext,
    AgentRunEffectQuarantined,
    AgentRunUnavailable,
    guarded_call,
    needs_fence,
)
from coworker.agent_runs import TERMINAL_AGENT_RUN_STATES
from coworker.compaction import (
    CompactionContext,
    SessionCompactor,
    is_context_overflow,
)
from coworker.events import TurnEventStream, TurnIdentity, build_event, new_turn_identity
from coworker.evidence_envelope import (
    ContextAuthority,
    EvidenceParts,
    director_directive,
    model_payload,
)
from coworker.inbox import Inbox
from coworker.ledger import record_tool_on_person
from coworker.permissions import RETRY_SAFE, decide
from coworker.person_identity import sanitize_apollo_name_masks
from coworker.provider import (
    ModelUsage,
    ProviderErrorKind,
    ProviderStart,
    ProviderStreamError,
    StreamKind,
    ToolCall,
    context_budget,
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
from coworker.run_budget import BudgetStop, RunBudgetMeter, RunBudgetPolicy
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
from coworker.tools import evidence_for, execute

INTERRUPTED_TOOL = '{"error": "tool call interrupted"}'
_APOLLO_TOOLS = frozenset({"apollo_search_people", "apollo_enrich_contact"})


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_apollo_json(value: object) -> str:
    if not isinstance(value, str):
        return json.dumps(sanitize_apollo_name_masks(value))
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return str(sanitize_apollo_name_masks(value))
    return json.dumps(sanitize_apollo_name_masks(decoded))


def _model_safe_apollo_message(message: dict[str, Any]) -> dict[str, Any]:
    """Project legacy Apollo transcript records without exposing name masks."""
    safe = dict(message)
    calls = message.get("tool_calls")
    if isinstance(calls, list):
        safe_calls: list[Any] = []
        for call in calls:
            if not isinstance(call, dict):
                safe_calls.append(call)
                continue
            safe_call = dict(call)
            function = call.get("function")
            if isinstance(function, dict) and function.get("name") in _APOLLO_TOOLS:
                safe_function = dict(function)
                safe_function["arguments"] = _safe_apollo_json(
                    function.get("arguments") or "{}"
                )
                safe_call["function"] = safe_function
            safe_calls.append(safe_call)
        safe["tool_calls"] = safe_calls
    if message.get("role") == "tool" and message.get("name") in _APOLLO_TOOLS:
        safe["content"] = _safe_apollo_json(message.get("content") or "{}")
    return safe


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
    parts: EvidenceParts | None = None,
) -> dict[str, Any]:
    sources, artifacts = _tool_provenance(call, result)
    # `EvidenceParts.references` is an allowlist projection with no field for
    # a body, so a receipt, a log line, and the window can all say where a
    # claim came from without reproducing what it said.
    evidence = parts.references() if parts is not None else []
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
        **({"evidence": evidence} if evidence else {}),
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
                    "arguments": json.dumps(
                        sanitize_apollo_name_masks(call.arguments)
                        if call.name in _APOLLO_TOOLS
                        else call.arguments
                    ),
                },
            }
            for call in calls
        ],
    }


def _tool_result_message(call: ToolCall, payload: dict[str, Any]) -> dict[str, Any]:
    """A result Sourcecado itself wrote: a gate refusal, a denied approval.

    These never carry a connector's text, so they go to the model as they
    are. Anything a connector produced goes through `_evidence_message`.
    """
    return {
        "role": "tool",
        "name": call.name,
        "tool_call_id": call.id,
        "content": json.dumps(payload),
    }


def _evidence_message(call: ToolCall, parts: EvidenceParts) -> dict[str, Any]:
    """The one place external text becomes model context.

    `model_payload` keeps Sourcecado's own metadata structured and puts
    everything somebody else wrote inside a fence, under a policy line
    stating what it may not do. A tool result with no external content
    renders exactly as it did before this boundary existed.
    """
    return {
        "role": "tool",
        "name": call.name,
        "tool_call_id": call.id,
        "content": json.dumps(model_payload(parts)),
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


def _bound_person_id(execute_kwargs: dict, sid: str) -> str | None:
    """The person this session works, so a run receipt names them."""
    people = execute_kwargs.get("people")
    if people is None:
        return None
    try:
        return people.person_for_session(sid)
    except Exception:
        return None


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
    compactor: SessionCompactor | None = None,
    run_budget_policy: RunBudgetPolicy | None = None,
    context_projection: Any = None,
    projection_identity: Any = None,
    agent_runs: Any = None,
    run_owner: Any = None,
    run_trigger: str = "chat",
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
    # One meter per run. Nothing carries over from a previous run, and
    # nothing it measures reaches a permission decision.
    budget = RunBudgetMeter(run_budget_policy)
    trace_context = TraceContext(
        session_id=events.identity.session_id,
        run_id=events.identity.run_id,
    )
    effective_tool_names = {
        str((schema.get("function") or {}).get("name") or "")
        for schema in openai_tools
        if isinstance(schema, dict)
    }
    # One turn, one ledger of who put what into context. The director channel
    # mints a directive; connectors mint envelopes. Nothing converts one into
    # the other, so identical text arriving on the two channels never carries
    # the same authority. The ledger is per turn: evidence read now cannot
    # justify an effect later.
    authority = ContextAuthority()
    authority.admit_directive(
        director_directive(text, session_id=sid, turn=events.identity.run_id)
    )
    turn_span = recorder.start_span(
        AgentTurnSpan(operation="agent.turn"), trace_context
    )
    # The durable Agent Run, sharing the turn's own run id so a receipt, a
    # person event, and a checkpoint all point at one identity.
    #
    # A run store that will not open a run does not stop the turn, and it does
    # not quietly let it through either: `guarded_call` refuses every
    # consequential tool while `run_context` is None, exactly as it does for a
    # context that lost its lease. Reading work still runs; sending does not.
    try:
        run_context = AgentRunContext.start(
            agent_runs,
            run_owner,
            session_id=sid,
            trigger=run_trigger,
            goal=text,
            run_id=events.identity.run_id,
            person_id=_bound_person_id(execute_kwargs, sid),
            provider_model_id=str(getattr(provider, "model_id", "") or "") or None,
        )
    except Exception as exc:
        # A caller that supplied a run store and got no run is fenced shut, not
        # unfenced. Reading work still runs; every consequential tool refuses.
        run_context = AgentRunContext.disarmed(
            agent_runs, run_owner, reason=f"{type(exc).__name__}: {exc}"
        )
    model_step = 0

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

    def _persist_tool_result(
        call: ToolCall, result: dict[str, Any], ok: bool, message: dict[str, Any]
    ) -> None:
        """Persist a tool result with the workspace runtime's own redaction.

        `sanitize_message` strips a workspace body from the field it used to
        live in. Fencing moves that body into the evidence block, so the
        redaction has to happen before the block is built, not after. The
        model still sees the full text this turn; the disk still does not.
        """
        runtime = execute_kwargs.get("workspace_runtime")
        if runtime is None or not runtime.owns_tool(call.name):
            _persist_message(message)
            return
        redacted = runtime.sanitize_result(call.name, result)
        _persist_message(
            _stamp(_evidence_message(call, evidence_for(call.name, redacted, ok=ok)))
        )

    def _record_call_outcome(
        call: ToolCall, ok: bool, result: dict[str, Any]
    ) -> None:
        """One place every finished call is written down.

        The person file gets the record it always got; the meter gets the
        reading the loop detector needs. A call that returns what an earlier
        identical call already returned is not progress, and a refusal counts
        the same as a result: asking for a denied tool ten times is a loop.
        """
        budget.record_tool_outcome(
            call_id=call.id,
            name=call.name,
            arguments=call.arguments,
            result=result,
            ok=ok,
        )
        _record_person_file(sid, call, ok, result, execute_kwargs)

    def _end_run(state: str, text_so_far: str) -> None:
        """Close the durable run in the same shape the operator was told.

        Shape only. The answer itself is in the transcript; the run row carries
        how it ended and how long it was, never what it said.
        """
        if run_context is None:
            return
        # The chat protocol carries states the run store does not know. `held`
        # is one: a quarantined effect already parked the run and released its
        # lease, and there is no run state that means "a person owns this now".
        if state not in TERMINAL_AGENT_RUN_STATES:
            return
        run_context.finish(state, status=state, text=text_so_far)

    async def _terminal(event: dict[str, Any]) -> None:
        _end_run(
            str(event.get("state") or "failed"), str(event.get("text") or "")
        )
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
        _end_run("stopped", text_so_far)
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

    def _compaction_context() -> CompactionContext:
        """Read live. The record region must describe the session as it is at
        the moment of compaction, not as it was when the turn began."""
        people_store = execute_kwargs.get("people")
        person = None
        if people_store is not None:
            bound = people_store.person_for_session(sid)
            if bound is not None:
                person = people_store.get(bound)
        pending = tuple(
            item
            for item in inbox.pending()
            if str(item.get("session_id") or sid) == sid
        )
        return CompactionContext(
            person=person,
            pending_approvals=pending,
            projection=context_projection,
            identity=projection_identity,
        )

    active_compactor = compactor or SessionCompactor(store=store, session_id=sid)
    active_compactor.bind_context(_compaction_context)

    people = execute_kwargs.get("people")
    if people is not None:
        bound_person_id = people.person_for_session(sid)
        if bound_person_id is not None and people.get(bound_person_id) is None:
            turn_span.fail(ErrorKind.POLICY)
            await _terminal(
                {
                    "type": "error",
                    "state": "failed",
                    "error_kind": ErrorKind.POLICY.value,
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
                "error_kind": ErrorKind.PROVIDER.value,
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
    last_input_tokens: int | None = None
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
        # Restore against the same list shape the boundary was computed over:
        # the system message at index 0, then the transcript. The user message
        # appended above sits past every stored boundary, so it cannot shift it.
        active_compactor.restore(
            [
                {k: v for k, v in message.items() if k != "message_id"}
                for message in history
            ]
        )
        budget_stop: BudgetStop | None = None
        pending_calls: tuple[tuple[str, str], ...] = ()
        while True:
            # The stop decision, before any spend. `check` puts the loop
            # detector ahead of the absolute budgets on purpose: a run that
            # is repeating itself is reported as stuck, not as expensive.
            budget_stop = budget.check()
            if budget_stop is not None:
                break
            budget.start_model_turn()
            model_step += 1
            if run_context is not None:
                run_context.note(
                    "model_pending",
                    state="running",
                    payload={
                        "step": model_step,
                        "attempt_id": f"model-{events.identity.run_id}-{model_step}",
                        "provider": _telemetry_provider_name(selected_provider),
                        "model_id": str(
                            getattr(selected_provider, "model_id", "") or ""
                        ),
                    },
                )
            _persist_closed(store, sid, history, workspace_runtime)
            # The canonical view. Compaction reads it and never writes it; the
            # transcript on disk stays the record of what actually happened.
            canonical_messages = [
                _model_safe_apollo_message(
                    {k: v for k, v in message.items() if k != "message_id"}
                )
                for message in history
            ]
            step_budget = context_budget(
                _telemetry_provider_name(selected_provider),
                str(getattr(selected_provider, "model_id", "") or "unknown"),
            )
            model_messages = await active_compactor.prepare(
                canonical_messages,
                budget=step_budget,
                reported_input_tokens=last_input_tokens,
                provider=selected_provider,
            )
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
                    # A request rejected for size is not a transient failure.
                    # Retrying the same view reproduces it, so compact harder
                    # and retry the same provider with a smaller one.
                    if is_context_overflow(exc):
                        compacted = await active_compactor.recover_from_overflow(
                            canonical_messages,
                            budget=step_budget,
                            provider=attempt_provider,
                        )
                        if compacted:
                            model_messages = active_compactor.view(canonical_messages)
                            continue
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
                # The same typed values the provider span just recorded, so
                # the budget is measured from telemetry rather than re-derived.
                budget.record_request(provider_usage, provider_cost)
                if run_context is not None:
                    run_context.note(
                        "model_completed",
                        state="running",
                        payload={
                            "step": model_step,
                            "attempt_id": (
                                f"model-{events.identity.run_id}-{model_step}"
                            ),
                            "provider": _telemetry_provider_name(attempt_provider),
                            "model_id": str(
                                getattr(attempt_provider, "model_id", "") or ""
                            ),
                            "status": str(
                                _telemetry_stop_reason(
                                    provider_finish_reason, used_tools=bool(calls)
                                ).value
                            ),
                        },
                        usage=(
                            {}
                            if provider_usage is None
                            else {
                                "input_tokens": provider_usage.input_tokens,
                                "output_tokens": provider_usage.output_tokens,
                                "total_tokens": provider_usage.total_tokens,
                            }
                        ),
                        provider_model_id=str(
                            getattr(attempt_provider, "model_id", "") or ""
                        )
                        or None,
                    )
                if provider_usage is not None:
                    # The provider's own count of the prompt it just read.
                    # Preferred over the estimate when sizing the next step.
                    last_input_tokens = provider_usage.input_tokens
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
            for index, call in enumerate(calls):
                if index:
                    budget_stop = budget.check()
                    if budget_stop is not None:
                        pending_calls = tuple(
                            (pending.id, pending.name) for pending in calls[index:]
                        )
                        break
                budget.charge_tool_call()
                approval_claimant: str | None = None
                approval_scope = "once"
                approval_fingerprint: str | None = None
                if call.name not in effective_tool_names:
                    result = {
                        "error": f"tool {call.name} is not available in this run"
                    }
                    had_tool_failure = True
                    await _emit(
                        _tool_finished_event(
                            call,
                            ok=False,
                            result=result,
                            identity=events.identity,
                        )
                    )
                    unavailable = _stamp(_tool_result_message(call, result))
                    history.append(unavailable)
                    _persist_message(unavailable)
                    _record_call_outcome(call, False, result)
                    continue
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
                    _record_call_outcome(call, False, result)
                    continue
                if gate.needs_user:
                    resource = approval_resource(
                        call.name,
                        call.arguments,
                        execute_kwargs.get("gmail"),
                        execute_kwargs.get("workspace_runtime"),
                    )
                    # What the operator is about to approve may have been
                    # copied out of something a stranger wrote. Say so on the
                    # request, by reference and never by body, and refuse to
                    # let that request become standing authority.
                    derived_refs = authority.derived_from_evidence(call.arguments)
                    if derived_refs:
                        resource = {
                            **(resource or {}),
                            "evidence_origin": "external",
                            "evidence_refs": list(derived_refs),
                        }
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
                            "scope": authority.clamp_scope(
                                parked["scope"], call.arguments
                            )[0],
                            **({"resource": resource} if resource else {}),
                        }
                    )
                    # Park the durable run on the person too. The lease is
                    # released with this write, so a crash while the operator
                    # is deciding leaves a run nobody owns and nothing resumes.
                    if run_context is not None:
                        run_context.park(
                            call.id, tool_name=call.name, step=model_step
                        )
                    if wait_permission is None:
                        return {"status": "waiting", "text": last_text}
                    choice = await wait_permission(call.id)
                    # Back through the approval door, which is the only way in
                    # to a parked run. A cancel did not authorize the call, and
                    # the door takes allow or deny only; the inbox and the
                    # transcript keep the word "cancelled".
                    if run_context is not None:
                        run_context.resume(
                            call.id, "allow" if choice == "allow" else "deny"
                        )
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
                        _record_call_outcome(call, False, result)
                        continue
                    approval_scope = authority.clamp_scope(
                        str(claim.item.get("scope") or "once"), call.arguments
                    )[0]
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
                        _record_call_outcome(call, False, result)
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
                        parts = authority.admit(
                            evidence_for(call.name, result, ok=ok)
                        )
                        await _emit(
                            _tool_finished_event(
                                call,
                                ok=ok,
                                result=result,
                                identity=events.identity,
                                parts=parts,
                            )
                        )
                        tool_result = _stamp(_evidence_message(call, parts))
                        history.append(tool_result)
                        _persist_tool_result(call, result, ok, tool_result)
                        _record_call_outcome(call, ok, result)
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
                        # Counts only, and the one warning per budget that
                        # says the run is approaching a stop.
                        "run_budget": budget.live_payload(),
                    }
                )
                tool_span = turn_span.child(_telemetry_tool_span(call.name))
                # A fenced tool gets its `tool_pending` from the dispatch, in
                # the same transaction as the effect row. A retry-safe one has
                # no effect record, so the checkpoint is written here or the
                # receipt shows no reading work at all.
                fenced = needs_fence(call.name)
                if run_context is not None and not fenced:
                    run_context.note(
                        "tool_pending",
                        state="running",
                        payload={
                            "step": model_step,
                            "tool_name": call.name,
                            "tool_call_id": call.id,
                        },
                    )
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

                    async def _invoke() -> tuple[bool, dict[str, Any]]:
                        return await asyncio.to_thread(
                            execute, call.name, call.arguments, **kw
                        )

                    # The one door to a tool in this loop. `guarded_call`
                    # commits the dispatch before the call and the outcome
                    # after it for everything outside `permissions.RETRY_SAFE`;
                    # calling `execute` directly here would be the trap
                    # `docs/agent-runs.md` names.
                    ok, result = await guarded_call(
                        run_context,
                        tool_name=call.name,
                        arguments=call.arguments,
                        call=_invoke,
                        tool_call_id=call.id,
                        approval_id=(
                            call.id if approval_claimant is not None else None
                        ),
                        step=model_step,
                    )
                except AgentRunEffectQuarantined:
                    # The call may have reached a real person. The turn ends
                    # rather than continuing as if it had merely failed.
                    tool_span.partial(ErrorKind.TOOL)
                    raise
                except AgentRunUnavailable as exc:
                    tool_span.partial(ErrorKind.TOOL)
                    ok, result = False, {
                        "error": (
                            "Sourcecado could not record this action durably, "
                            f"so it was not attempted. {exc}"
                        ),
                        "code": "agent_run_unavailable",
                    }
                except asyncio.CancelledError:
                    tool_span.cancel()
                    raise
                except Exception as exc:
                    ok, result = False, {"error": str(exc)}
                if ok:
                    tool_span.finish()
                else:
                    tool_span.partial(ErrorKind.TOOL)
                if run_context is not None and not fenced:
                    run_context.note(
                        "tool_completed",
                        state="running",
                        payload={
                            "step": model_step,
                            "tool_name": call.name,
                            "tool_call_id": call.id,
                            "status": "succeeded" if ok else "failed",
                            "error_class": (
                                None if ok else str(result.get("code") or "tool_error")
                            ),
                        },
                    )
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
                parts = authority.admit(evidence_for(call.name, result, ok=ok))
                await _emit(
                    _tool_finished_event(
                        call,
                        ok=ok,
                        result=result,
                        identity=events.identity,
                        parts=parts,
                    )
                )
                tool_result = _stamp(_evidence_message(call, parts))
                history.append(tool_result)
                _persist_tool_result(call, result, ok, tool_result)
                _record_call_outcome(call, ok, result)
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
        if budget_stop is not None:
            # A run that ran out of budget did not finish. It says so, lists
            # what it actually completed, names what it had queued and did
            # not run, and leaves continuing to the director.
            _persist_closed(store, sid, history, workspace_runtime)
            turn_span.partial(ErrorKind.POLICY)
            stop_notice = active_compactor.notice()
            await _terminal(
                {
                    "type": "turn_end",
                    "state": "stopped",
                    "text": last_text,
                    "message": budget_stop.message(),
                    "run_budget": budget.terminal_payload(
                        stop=budget_stop,
                        pending_calls=pending_calls,
                        final_answer=False,
                    ),
                    **({"compaction": stop_notice} if stop_notice else {}),
                }
            )
            return {"status": "stopped", "text": last_text}
    except asyncio.CancelledError:
        turn_span.cancel()
        raise
    except AgentRunEffectQuarantined as exc:
        # A consequential call was dispatched and never reported back. Saying
        # "failed" here would be a claim nobody can make, and an operator told
        # a send failed is an operator who sends it again. The effect is on the
        # review queue; the terminal points at it and stops.
        turn_span.partial(ErrorKind.TOOL)
        _persist_closed(store, sid, history, workspace_runtime)
        await _terminal(
            {
                "type": "error",
                "state": "held",
                "code": "outcome_unknown",
                "effect_id": exc.effect_id,
                "message": (
                    "The outcome of this action is unknown. It is held for "
                    "review; do not retry it until it is settled."
                ),
            }
        )
        return {"status": "held", "text": last_text}
    except Exception as exc:
        turn_span.fail(turn_error_kind)
        _persist_closed(store, sid, history, workspace_runtime)
        await _terminal(
            {
                "type": "error",
                "state": "failed",
                "error_kind": turn_error_kind.value,
                "message": turn_error_message or str(exc),
            }
        )
        return {"status": "error", "text": last_text}
    final_state = "partial" if had_tool_failure else "complete"
    # Counts only. The summary text stays in the provider view, so the operator
    # is told that older context was compacted without being shown a model's
    # account of the session as if it were Sourcecado's own reasoning.
    compaction_notice = active_compactor.notice()
    await _terminal(
        {
            "type": "turn_end",
            "text": last_text,
            "state": final_state,
            "run_budget": budget.terminal_payload(stop=None, final_answer=True),
            **({"compaction": compaction_notice} if compaction_notice else {}),
        }
    )
    if had_tool_failure:
        turn_span.partial(ErrorKind.TOOL)
    else:
        turn_span.finish()
    return {
        "status": "partial" if had_tool_failure else "ok",
        "text": last_text,
    }
