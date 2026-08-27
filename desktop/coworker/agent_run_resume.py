"""Explicit durable Agent Run resume coordination.

This module deliberately has no startup integration.  Callers must name the run
to resume, and the durable continuation remains the only execution authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from coworker.agent_run_execution import (
    EXECUTION_LEASE_SECONDS,
    AgentRunExecution,
    AgentRunExecutionOwnershipError,
)
from coworker.agent_run_continuation import transcript_prefix_sha256
from coworker.agent_run_heartbeat import AgentRunHeartbeat, AgentRunHeartbeatError
from coworker.agent_runs import TERMINAL_AGENT_RUN_STATES, safe_error_summary
from coworker.events import TurnEventStream, TurnIdentity, build_event
from coworker.provider import ToolCall

if TYPE_CHECKING:
    from coworker.store import ConversationStore
    from coworker.turn import RunControl


MAX_STEPS = 8


def _terminal_status(state: str) -> str:
    return {
        "complete": "ok",
        "partial": "partial",
        "stopped": "stopped",
        "failed": "error",
    }[state]


def _is_terminal_event(
    event: dict[str, Any], identity: TurnIdentity, state: str
) -> bool:
    if (
        event.get("run_id") != identity.run_id
        or event.get("message_id") != identity.message_id
    ):
        return False
    if state == "failed":
        return event.get("type") == "error" and event.get("state") == "failed"
    if state == "stopped" and event.get("type") == "turn_stopped":
        return event.get("state") == "stopped"
    return event.get("type") == "turn_end" and event.get("state") == state


async def _repair_terminal_projection(
    *,
    store: ConversationStore,
    run: dict[str, Any],
    identity: TurnIdentity,
    emit: Callable[[dict[str, Any]], Awaitable[None]] | None,
) -> dict[str, Any]:
    state = str(run["current_state"])
    events = store.load_events(identity.session_id)
    existing = next(
        (
            event
            for event in events
            if _is_terminal_event(event, identity, state)
        ),
        None,
    )
    terminal = run.get("terminal_result") or {}
    text = ""
    messages = store.load(identity.session_id)
    for message in reversed(messages):
        if (
            message.get("role") == "assistant"
            and message.get("message_id") == identity.message_id
            and isinstance(message.get("content"), str)
        ):
            text = message["content"]
            break
    visible_deltas: list[str] = []
    for event in events:
        if (
            event.get("run_id") != identity.run_id
            or event.get("message_id") != identity.message_id
        ):
            continue
        if event.get("type") in {
            "turn_start",
            "tool_started",
            "tool_finished",
            "permission_required",
            "approval_resolved",
        }:
            visible_deltas.clear()
        elif event.get("type") == "assistant_delta":
            visible_deltas.append(str(event.get("delta") or ""))
    visible_text = "".join(visible_deltas)
    expected_length = int(terminal.get("text_length") or 0)
    if visible_text and (not text or len(text) != expected_length):
        text = visible_text
    result = {
        "status": _terminal_status(state),
        "text": text,
        "run_id": identity.run_id,
    }
    if existing is not None:
        return result
    event_id = "event_terminal_" + hashlib.sha256(
        identity.run_id.encode("utf-8")
    ).hexdigest()[:32]
    if state == "failed":
        event = build_event(
            identity,
            "error",
            event_id=event_id,
            state="failed",
            message=str(terminal.get("error") or "Run failed."),
        )
    else:
        event = build_event(
            identity,
            "turn_end",
            event_id=event_id,
            state=state,
            text=text,
        )
    try:
        appended = store.append_event_once(identity.session_id, event)
    except Exception:
        return {"status": "error", "text": text, "run_id": identity.run_id}
    if appended and emit is not None:
        await emit(event)
    return result


def _committed_tool_batch(
    messages: list[dict[str, Any]],
    cursor: dict[str, Any],
    pending: dict[str, Any] | None,
) -> tuple[str, list[ToolCall]]:
    expected = int(cursor.get("expected_tool_count", 0))
    next_index = int(cursor.get("next_tool_index", 0))
    if expected < 1 or next_index < 0 or next_index >= expected:
        raise ValueError("resumable tool cursor is inconsistent")
    for message_index in range(len(messages) - 1, -1, -1):
        message = messages[message_index]
        raw_calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not isinstance(raw_calls, list):
            continue
        if len(raw_calls) != expected:
            continue
        calls: list[ToolCall] = []
        try:
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    raise ValueError
                function = raw.get("function")
                if not isinstance(function, dict):
                    raise ValueError
                arguments = json.loads(str(function.get("arguments") or "{}"))
                if not isinstance(arguments, dict):
                    raise ValueError
                call_id = str(raw.get("id") or "")
                name = str(function.get("name") or "")
                if not call_id or not name:
                    raise ValueError
                calls.append(ToolCall(id=call_id, name=name, arguments=arguments))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if pending is not None and (
            calls[next_index].id != pending.get("call_id")
            or calls[next_index].name != pending.get("name")
        ):
            continue
        trailing_results = [
            item
            for item in messages[message_index + 1 :]
            if item.get("role") == "tool"
        ]
        if len(trailing_results) != next_index or any(
            result.get("tool_call_id") != call.id
            or result.get("name") != call.name
            for result, call in zip(trailing_results, calls[:next_index])
        ):
            raise ValueError(
                "committed tool batch has invalid earlier result order"
            )
        return str(message.get("content") or ""), calls
    raise ValueError("committed assistant tool-call message is unavailable")


def _result_digest(result: dict[str, Any]) -> str:
    canonical = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identity_for(run: dict[str, Any], run_id: str) -> TurnIdentity | None:
    continuation = run.get("continuation") or {}
    stored = continuation.get("identity") or {}
    message_id = stored.get("message_id")
    part_id = stored.get("part_id")
    session_id = run.get("session_id")
    if not all(
        isinstance(value, str) and value
        for value in (session_id, message_id, part_id)
    ):
        return None
    return TurnIdentity(
        session_id=session_id,
        run_id=run_id,
        message_id=message_id,
        part_id=part_id,
    )


def _projection_status(
    values: list[dict[str, Any]], count: int, digest: str
) -> str:
    if len(values) < count:
        return "mismatch"
    if transcript_prefix_sha256(values[:count]) != digest:
        return "mismatch"
    return "exact" if len(values) == count else "extra_tail"


def _repair_projections(
    *,
    store: ConversationStore,
    execution: AgentRunExecution,
    identity: TurnIdentity,
    continuation: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    cursor = continuation.get("cursor") or {}
    cursor_target = {
        key: cursor.get(key)
        for key in (
            "transcript_prefix_count",
            "transcript_prefix_sha256",
            "event_prefix_count",
            "event_prefix_sha256",
        )
    }
    marker = continuation.get("projection_repair")
    if marker is not None and marker != cursor_target:
        execution.projection_mismatch(["transcript", "event"])
        return continuation, [], "review_required"
    target = dict(marker or cursor_target)
    try:
        transcript_count = int(target["transcript_prefix_count"])
        transcript_digest = str(target["transcript_prefix_sha256"])
        event_count = int(target["event_prefix_count"])
        event_digest = str(target["event_prefix_sha256"])
    except (KeyError, TypeError, ValueError):
        execution.projection_mismatch(["transcript", "event"])
        return continuation, [], "review_required"

    loaded = store.load(identity.session_id)
    durable_events = store.load_events(identity.session_id)
    transcript_status = _projection_status(
        loaded, transcript_count, transcript_digest
    )
    event_status = _projection_status(durable_events, event_count, event_digest)
    mismatches = [
        name
        for name, status in (
            ("transcript", transcript_status),
            ("event", event_status),
        )
        if status == "mismatch"
    ]
    if mismatches:
        execution.projection_mismatch(mismatches)
        return continuation, loaded, "review_required"

    needs_repair = marker is not None or "extra_tail" in {
        transcript_status,
        event_status,
    }
    if not needs_repair:
        return continuation, loaded, None

    if marker is None:
        execution.begin_projection_repair(target)
        continuation = store.get_agent_run(execution.run_id)["continuation"]

    def publish() -> None:
        store.rewrite_projections_in_transaction(
            identity.session_id,
            loaded[:transcript_count],
            durable_events[:event_count],
        )
        repaired_messages = store.load(identity.session_id)
        repaired_events = store.load_events(identity.session_id)
        if (
            _projection_status(
                repaired_messages, transcript_count, transcript_digest
            )
            != "exact"
            or _projection_status(repaired_events, event_count, event_digest)
            != "exact"
        ):
            raise OSError("projection repair did not publish exact targets")

    execution.publish_projection_repair(publish)
    continuation = store.get_agent_run(execution.run_id)["continuation"]
    return continuation, store.load(identity.session_id), None


def _classify_owned_continuation(
    *,
    store: ConversationStore,
    execution: AgentRunExecution,
    continuation: dict[str, Any],
    loaded: list[dict[str, Any]],
    retry_safe_tools: frozenset[str],
) -> tuple[
    dict[str, Any],
    tuple[str, list[ToolCall]] | None,
    str | None,
]:
    cursor = continuation.get("cursor") or {}
    phase = cursor.get("phase")
    pending_tool = continuation.get("pending_tool")
    pending_model = continuation.get("pending_model")
    recovered: tuple[str, list[ToolCall]] | None = None
    review_reason: str | None = None
    if phase == "model_ready":
        if not (
            isinstance(pending_model, dict)
            and pending_model.get("status") == "retry_ready"
            and pending_model.get("budget_reserved") is True
        ):
            review_reason = "invalid_resume_boundary"
    elif phase == "tools_ready":
        next_index = int(cursor.get("next_tool_index", 0))
        expected_count = int(cursor.get("expected_tool_count", 0))
        if isinstance(pending_tool, dict):
            matching_receipt = next(
                (
                    receipt
                    for receipt in continuation.get("completed_tool_receipts", [])
                    if receipt.get("attempt_id") == pending_tool.get("attempt_id")
                    and receipt.get("call_id") == pending_tool.get("call_id")
                    and receipt.get("name") == pending_tool.get("name")
                ),
                None,
            )
            if matching_receipt is not None:
                has_result = any(
                    message.get("role") == "tool"
                    and message.get("tool_call_id") == pending_tool.get("call_id")
                    for message in loaded
                )
                if not has_result:
                    review_reason = "invalid_resume_boundary"
                else:
                    execution.recover_completed_tool_receipt()
                    continuation = store.get_agent_run(execution.run_id)[
                        "continuation"
                    ]
                    cursor = continuation["cursor"]
                    next_index = int(cursor.get("next_tool_index", 0))
                    expected_count = int(cursor.get("expected_tool_count", 0))
                    if next_index < expected_count:
                        try:
                            recovered = _committed_tool_batch(
                                loaded, cursor, None
                            )
                        except ValueError:
                            review_reason = "invalid_resume_boundary"
            elif (
                pending_tool.get("retry_class") != "safe"
                or pending_tool.get("status") != "retry_ready"
                or pending_tool.get("budget_reserved") is not True
            ):
                review_reason = "invalid_resume_boundary"
            elif pending_tool.get("name") not in retry_safe_tools:
                review_reason = "policy_changed"
            else:
                try:
                    recovered = _committed_tool_batch(
                        loaded, cursor, pending_tool
                    )
                except ValueError:
                    review_reason = "invalid_resume_boundary"
        elif next_index < expected_count:
            try:
                recovered = _committed_tool_batch(loaded, cursor, None)
            except ValueError:
                review_reason = "invalid_resume_boundary"
        elif next_index != expected_count:
            review_reason = "invalid_resume_boundary"
    else:
        return continuation, None, "conflict"
    if review_reason is not None:
        execution.resume_review_required(review_reason)
        return continuation, None, "review_required"
    return continuation, recovered, None



async def resume_turn(
    *,
    run_id: str,
    store: ConversationStore,
    provider: Any,
    dependencies: dict[str, Any],
    emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    control: RunControl | None = None,
    lease_seconds: float | None = None,
) -> dict[str, Any]:
    """Resume exactly one interrupted model or safe-tool boundary."""
    # Lazy import keeps turn.py's public wrapper thin without a module cycle and
    # deliberately reuses the live loop's event/tool projection primitives.
    from coworker import turn as live_turn

    run = store.get_agent_run(run_id)
    if run is None:
        raise KeyError(run_id)
    identity = _identity_for(run, run_id)
    if identity is None:
        return {"status": "conflict", "text": "", "run_id": run_id}
    if run.get("current_state") in TERMINAL_AGENT_RUN_STATES:
        return await _repair_terminal_projection(
            store=store,
            run=run,
            identity=identity,
            emit=emit,
        )
    continuation = run.get("continuation") or {}
    phase = continuation.get("cursor", {}).get("phase")
    if run.get("current_state") != "interrupted":
        return {"status": "conflict", "text": "", "run_id": run_id}
    if phase == "review_required":
        return {"status": "review_required", "text": "", "run_id": run_id}
    if phase not in {"model_ready", "tools_ready"}:
        return {"status": "conflict", "text": "", "run_id": run_id}
    duration = EXECUTION_LEASE_SECONDS if lease_seconds is None else lease_seconds
    try:
        execution = AgentRunExecution.resume(
            store, identity, MAX_STEPS, lease_seconds=duration
        )
    except AgentRunExecutionOwnershipError:
        return {"status": "conflict", "text": "", "run_id": run_id}
    try:
        continuation, loaded, rejected = _repair_projections(
            store=store,
            execution=execution,
            identity=identity,
            continuation=continuation,
        )
    except Exception:
        if execution.current_lease is not None:
            try:
                execution.suspend_projection_repair()
            except Exception:
                pass
        return {"status": "error", "text": "", "run_id": run_id}
    if rejected is not None:
        return {"status": rejected, "text": "", "run_id": run_id}
    continuation, recovered, rejected = _classify_owned_continuation(
        store=store,
        execution=execution,
        continuation=continuation,
        loaded=loaded,
        retry_safe_tools=live_turn._SAFE_RETRY_TOOLS,
    )
    if rejected is not None:
        return {"status": rejected, "text": "", "run_id": run_id}

    events = TurnEventStream(identity=identity, store=store, emit=emit)
    if control is not None:
        await control.attach(events)

    async def send(event: dict[str, Any]) -> dict[str, Any]:
        return await events.send(event)

    persona = dependencies.get("persona")
    skills = dependencies.get("skills")
    execute_kwargs = dependencies.get("execute_kwargs") or {}
    system_prompt_fn = dependencies.get("system_prompt_fn")
    system_content = ""
    if system_prompt_fn is not None:
        system_content = system_prompt_fn(
            store,
            persona,
            skills,
            people=execute_kwargs.get("people"),
            session_id=identity.session_id,
        )
    history = [{"role": "system", "content": system_content}, *loaded]
    cursor = continuation.get("cursor") or {}
    step_index = int(cursor.get("step_index", 0))
    last_text = ""
    had_tool_failure = any(
        receipt.get("ok") is False
        or receipt.get("outcome")
        in {"failed_unexecuted", "failed_external", "denied"}
        for receipt in continuation.get("completed_tool_receipts", [])
    )
    calls_to_process: list[ToolCall] | None = None
    next_tool_index = 0
    if cursor.get("phase") == "tools_ready":
        if recovered is not None:
            last_text, calls_to_process = recovered
            next_tool_index = int(cursor.get("next_tool_index", 0))
        else:
            calls_to_process = []

    async def finish_terminal(
        state: str, text: str, message: str | None = None
    ) -> bool:
        status = {
            "complete": "ok",
            "partial": "partial",
            "stopped": "stopped",
            "failed": "error",
        }[state]
        execution.terminal(
            store.load(identity.session_id),
            store.load_events(identity.session_id),
            state,
            status,
            identity.message_id,
            len(text),
            error=(
                safe_error_summary(message)
                if state == "failed" and message
                else None
            ),
            error_class="run_error" if state == "failed" else None,
        )
        event = (
            {"type": "error", "state": "failed", "message": message or "Run failed."}
            if state == "failed"
            else {"type": "turn_end", "state": state, "text": text}
        )
        try:
            if control is None:
                await send(event)
            else:
                await control.send_terminal(event)
        except Exception:
            return False
        return True

    async def process_tools(
        calls: list[ToolCall], start_index: int, step: int
    ) -> tuple[str | None, bool]:
        nonlocal had_tool_failure
        for tool_index in range(start_index, len(calls)):
            call = calls[tool_index]
            gate = live_turn.decide(call.name)
            retry_safe = call.name in live_turn._SAFE_RETRY_TOOLS
            if gate.needs_user:
                resource = live_turn.approval_resource(
                    call.name, call.arguments, execute_kwargs.get("gmail")
                )
                parked = execution.waiting_approval_atomic(
                    store.load(identity.session_id),
                    store.load_events(identity.session_id),
                    call.id,
                    step,
                    tool_index,
                    call.id,
                    call.name,
                    retry_safe,
                    arguments=call.arguments,
                    reason=gate.reason,
                    resource=resource,
                    approval_ttl_seconds=store.approval_ttl_seconds,
                )
                await send(
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
                return "waiting", had_tool_failure
            if not gate.allowed:
                ok, result = False, {"error": gate.reason or "denied"}
                had_tool_failure = True
                await send(
                    live_turn._tool_finished_event(
                        call, ok=ok, result=result, identity=identity
                    )
                )
                tool_result = live_turn._tool_result_message(call, result)
                tool_result["message_id"] = identity.message_id
                history.append(tool_result)
                store.append(identity.session_id, tool_result)
                live_turn._record_person_file(
                    identity.session_id, call, ok, result, execute_kwargs
                )
                execution.tool_skipped(
                    store.load(identity.session_id),
                    store.load_events(identity.session_id),
                    step,
                    tool_index,
                    call.id,
                    call.name,
                    _result_digest(result),
                )
                continue
            if control is not None:
                control.current_action = call.name
            await send(
                {
                    "type": "tool_started",
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "started_at": live_turn._now_iso(),
                }
            )
            execution.renew()
            execution.tool_pending(
                store.load(identity.session_id),
                store.load_events(identity.session_id),
                step,
                tool_index,
                call.id,
                call.name,
                retry_safe,
            )
            try:
                kwargs = {
                    key: value
                    for key, value in execute_kwargs.items()
                    if not key.startswith("_")
                }
                kwargs["session_id"] = identity.session_id
                async with AgentRunHeartbeat(execution):
                    ok, result = await asyncio.to_thread(
                        live_turn.execute, call.name, call.arguments, **kwargs
                    )
            except AgentRunHeartbeatError:
                raise
            except Exception as exc:
                ok, result = False, {"error": str(exc)}
            if (
                call.name in {"remember", "memory_update", "memory_forget"}
                and system_prompt_fn is not None
                and ok
            ):
                history[0] = {
                    "role": "system",
                    "content": system_prompt_fn(
                        store,
                        persona,
                        skills,
                        people=execute_kwargs.get("people"),
                        session_id=identity.session_id,
                    ),
                }
            had_tool_failure = had_tool_failure or not ok
            await send(
                live_turn._tool_finished_event(
                    call, ok=ok, result=result, identity=identity
                )
            )
            tool_result = live_turn._tool_result_message(call, result)
            tool_result["message_id"] = identity.message_id
            history.append(tool_result)
            store.append(identity.session_id, tool_result)
            live_turn._record_person_file(
                identity.session_id, call, ok, result, execute_kwargs
            )
            sources, artifacts = live_turn._tool_provenance(call, result)
            loaded_skills: list[str] = []
            if call.name == "load_skill" and ok:
                skill_name = str(
                    result.get("name") or call.arguments.get("name") or ""
                )
                if skill_name:
                    loaded_skills.append(skill_name)
            execution.tool_completed(
                store.load(identity.session_id),
                store.load_events(identity.session_id),
                step,
                tool_index,
                call.id,
                call.name,
                ok,
                _result_digest(result),
                skills_loaded=loaded_skills,
                source_refs=sources,
                artifact_refs=artifacts,
            )
            if control is not None:
                control.current_action = None
                if control.cancel_requested.is_set():
                    return "stopped", had_tool_failure
        return None, had_tool_failure

    try:
        await send({"type": "turn_start", "state": "running"})
        while step_index < MAX_STEPS:
            if calls_to_process is not None:
                outcome, _ = await process_tools(
                    calls_to_process, next_tool_index, step_index
                )
                if outcome == "waiting":
                    return {"status": "waiting", "text": last_text, "run_id": run_id}
                if outcome == "stopped":
                    projected = await finish_terminal("stopped", last_text)
                    return {
                        "status": "stopped" if projected else "error",
                        "text": last_text,
                        "run_id": run_id,
                    }
                calls_to_process = None
                next_tool_index = 0
                step_index += 1
                continue

            if provider is None:
                projected = await finish_terminal(
                    "failed", "", "No model provider configured."
                )
                return {"status": "error", "text": "", "run_id": run_id}
            execution.renew()
            execution.model_pending(
                store.load(identity.session_id),
                store.load_events(identity.session_id),
                step_index,
            )
            chunks: list[str] = []
            model_calls: list[ToolCall] = []
            async with AgentRunHeartbeat(execution) as heartbeat:
                async for chunk in provider.astream(
                    messages=[
                        {
                            key: value
                            for key, value in message.items()
                            if key != "message_id"
                        }
                        for message in history
                    ],
                    tools=dependencies.get("openai_tools") or [],
                ):
                    heartbeat.raise_if_failed()
                    if control is not None and control.cancel_requested.is_set():
                        projected = await finish_terminal("stopped", "".join(chunks))
                        return {
                            "status": "stopped" if projected else "error",
                            "text": "".join(chunks),
                            "run_id": run_id,
                        }
                    if chunk.text_delta:
                        chunks.append(chunk.text_delta)
                        await send(
                            {"type": "assistant_delta", "delta": chunk.text_delta}
                        )
                    if chunk.tool_calls:
                        model_calls = chunk.tool_calls
            last_text = "".join(chunks)
            if model_calls:
                assistant = live_turn._assistant_tool_message(last_text, model_calls)
                assistant["message_id"] = identity.message_id
            else:
                assistant = {
                    "role": "assistant",
                    "content": last_text,
                    "message_id": identity.message_id,
                }
            if model_calls or last_text:
                history.append(assistant)
                store.append(identity.session_id, assistant)
            execution.model_completed(
                store.load(identity.session_id),
                store.load_events(identity.session_id),
                step_index,
                len(model_calls),
                len(last_text),
            )
            if not model_calls:
                final_state = "partial" if had_tool_failure else "complete"
                projected = await finish_terminal(final_state, last_text)
                return {
                    "status": (
                        "partial" if had_tool_failure else "ok"
                    ) if projected else "error",
                    "text": last_text,
                    "run_id": run_id,
                }
            calls_to_process = model_calls
            next_tool_index = 0

        projected = await finish_terminal("stopped", last_text)
        return {
            "status": "stopped" if projected else "error",
            "text": last_text,
            "run_id": run_id,
        }
    except Exception as exc:
        if execution.metadata.get("phase") == "tool_in_flight":
            try:
                execution.interrupt_inflight_tool(
                    store.load(identity.session_id),
                    store.load_events(identity.session_id),
                )
            except Exception:
                return {"status": "error", "text": last_text, "run_id": run_id}
            try:
                await send(
                    {
                        "type": "turn_end",
                        "state": "interrupted",
                        "text": last_text,
                        "message": (
                            "Run interrupted after a tool result could not be "
                            "durably recorded."
                        ),
                    }
                )
            except Exception:
                return {"status": "error", "text": last_text, "run_id": run_id}
            return {"status": "interrupted", "text": last_text, "run_id": run_id}
        if execution.current_lease is None:
            current = store.get_agent_run(run_id)
            if current is not None and current.get("current_state") == "interrupted":
                return {"status": "interrupted", "text": last_text, "run_id": run_id}
            return {"status": "conflict", "text": last_text, "run_id": run_id}
        projected = await finish_terminal(
            "failed", last_text, safe_error_summary(str(exc))
        )
        return {"status": "error", "text": last_text, "run_id": run_id}
