"""Pure Agent Run execution transition reducer.

Normal completion is legal only from ``terminal_ready``. Explicit failure and
stop transitions are also legal from non-tool active phases so provider errors
and operator cancellation can release authority without pretending success.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from coworker.agent_run_continuation import (
    MAX_SAFE_ID,
    MAX_TOOL_NAME,
    merge_continuation,
    project_continuation,
    transcript_prefix_sha256,
    valid_sha256,
)
from coworker.agent_runs import redact_sensitive_assignments


class AgentRunTransitionError(ValueError):
    """The requested boundary is not legal from the persisted cursor."""


_ERROR_TERMINAL_PHASES = frozenset(
    {
        "model_ready",
        "model_in_flight",
        "tools_ready",
        "waiting_approval",
        "approval_ready",
        "terminal_ready",
        "review_required",
    }
)


def initial_continuation(
    identity: dict[str, str], max_steps: int
) -> dict[str, Any]:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise AgentRunTransitionError("max_steps must be a positive integer")
    return project_continuation(
        {
            "identity": identity,
            "cursor": {
                "phase": "model_ready",
                "step_index": 0,
                "next_tool_index": 0,
                "expected_tool_count": 0,
                **prefixes([], []),
            },
            "visible_partial": {
                "message_id": identity.get("message_id"),
                "text_length": 0,
                "truncated": False,
            },
            "completed_tool_receipts": [],
            "remaining_budgets": {
                "work_steps": max_steps,
                "tool_calls": max_steps,
                "delivery_passes": 1,
            },
        }
    )


def model_pending_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    step_index: int,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    attempt_id = model_attempt_id(run_id, step)
    pending = current.get("pending_model")
    retry = (
        cursor.get("phase") == "model_ready"
        and cursor.get("step_index") == step
        and pending
        == {
            "attempt_id": attempt_id,
            "status": "retry_ready",
            "budget_reserved": True,
        }
    )
    budgets = _budgets(current)
    if not retry:
        if pending is not None or current.get("pending_interaction") is not None:
            raise AgentRunTransitionError("model_pending conflicts with pending work")
        if current.get("pending_tool") is not None:
            raise AgentRunTransitionError("model_pending requires completed tools")
        phase = cursor.get("phase")
        current_step = int(cursor.get("step_index", 0))
        if phase == "model_ready":
            legal = step == 0 and current_step == 0
        elif phase == "tools_ready":
            legal = (
                int(cursor.get("next_tool_index", 0))
                == int(cursor.get("expected_tool_count", 0))
                and step == current_step + 1
            )
        else:
            legal = False
        if not legal:
            raise AgentRunTransitionError(
                "model_pending must be the initial model or follow all tools"
            )
        if budgets.get("work_steps", 0) < 1:
            raise AgentRunTransitionError("Agent Run work-step budget exhausted")
        budgets["work_steps"] -= 1
    return merge_continuation(
        current,
        {
            "cursor": {
                "phase": "model_in_flight",
                "step_index": step,
                "next_tool_index": 0,
                "expected_tool_count": 0,
                **prefixes(history, events),
            },
            "pending_model": {
                "attempt_id": attempt_id,
                "status": "in_flight",
                "budget_reserved": True,
            },
            "remaining_budgets": budgets,
        },
    )


def model_completed_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    step_index: int,
    tool_count: int,
    text_length: int,
    message_id: str,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    count = _index(tool_count, "tool_count")
    length = _index(text_length, "text_length")
    pending = current.get("pending_model")
    if (
        cursor.get("phase") != "model_in_flight"
        or cursor.get("step_index") != step
        or not isinstance(pending, dict)
        or pending.get("attempt_id") != model_attempt_id(run_id, step)
        or pending.get("status") != "in_flight"
        or pending.get("budget_reserved") is not True
    ):
        raise AgentRunTransitionError(
            "model_completed must match the exact in-flight reserved model attempt"
        )
    return merge_continuation(
        current,
        {
            "cursor": {
                "phase": "tools_ready" if count else "terminal_ready",
                "step_index": step,
                "next_tool_index": 0,
                "expected_tool_count": count,
                **prefixes(history, events),
            },
            "pending_model": None,
            "visible_partial": {
                "message_id": _text(message_id, MAX_SAFE_ID, "message_id"),
                "text_length": length,
                "truncated": False,
            },
        },
    )


def tool_pending_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    step_index: int,
    tool_index: int,
    call_id: str,
    name: str,
    retry_safe: bool,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    tool = _index(tool_index, "tool_index")
    safe_call_id = _text(call_id, MAX_SAFE_ID, "call_id")
    safe_name = _text(name, MAX_TOOL_NAME, "name")
    attempt_id = tool_attempt_id(run_id, step, tool, safe_call_id)
    retry_class = "safe" if retry_safe else "consequential"
    pending = current.get("pending_tool")
    exact_pending = (
        isinstance(pending, dict)
        and pending.get("attempt_id") == attempt_id
        and pending.get("call_id") == safe_call_id
        and pending.get("name") == safe_name
        and pending.get("retry_class") == retry_class
    )
    prebound_approval = (
        exact_pending
        and pending.get("status") == "not_started"
        and pending.get("budget_reserved") is False
    )
    retry = (
        exact_pending
        and pending.get("retry_class") == "safe"
        and pending.get("status") == "retry_ready"
        and pending.get("budget_reserved") is True
        and retry_safe
    )
    if (
        cursor.get("phase") != "tools_ready"
        or cursor.get("step_index") != step
        or cursor.get("next_tool_index") != tool
        or tool >= int(cursor.get("expected_tool_count", 0))
        or current.get("pending_interaction") is not None
        or current.get("pending_model") is not None
    ):
        raise AgentRunTransitionError(
            "tool_pending must match the exact next expected tool"
        )
    budgets = _budgets(current)
    if not retry:
        if pending is not None and not prebound_approval:
            raise AgentRunTransitionError("tool_pending conflicts with pending tool")
        if budgets.get("tool_calls", 0) < 1:
            raise AgentRunTransitionError("Agent Run tool-call budget exhausted")
        budgets["tool_calls"] -= 1
    return merge_continuation(
        current,
        {
            "cursor": {**cursor, "phase": "tool_in_flight", **prefixes(history, events)},
            "pending_tool": {
                "attempt_id": attempt_id,
                "call_id": safe_call_id,
                "name": safe_name,
                "retry_class": retry_class,
                "status": "in_flight",
                "budget_reserved": True,
            },
            "remaining_budgets": budgets,
        },
    )


def tool_completed_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    step_index: int,
    tool_index: int,
    call_id: str,
    name: str,
    ok: bool,
    result_digest: str,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    tool = _index(tool_index, "tool_index")
    safe_call_id = _text(call_id, MAX_SAFE_ID, "call_id")
    safe_name = _text(name, MAX_TOOL_NAME, "name")
    digest = valid_sha256(result_digest)
    if digest is None:
        raise AgentRunTransitionError("result_digest must be a SHA-256 digest")
    pending = current.get("pending_tool")
    if (
        cursor.get("phase") != "tool_in_flight"
        or cursor.get("step_index") != step
        or cursor.get("next_tool_index") != tool
        or not isinstance(pending, dict)
        or pending.get("attempt_id")
        != tool_attempt_id(run_id, step, tool, safe_call_id)
        or pending.get("call_id") != safe_call_id
        or pending.get("name") != safe_name
        or pending.get("status") != "in_flight"
        or pending.get("budget_reserved") is not True
    ):
        raise AgentRunTransitionError(
            "tool_completed must match the exact in-flight tool"
        )
    receipt = {
        "attempt_id": pending["attempt_id"],
        "call_id": safe_call_id,
        "name": safe_name,
        "ok": bool(ok),
        "outcome": "executed",
        "transcript_index": max(0, len(history) - 1),
        "result_sha256": digest,
    }
    return merge_continuation(
        current,
        {
            "cursor": {
                **cursor,
                "phase": "tools_ready",
                "next_tool_index": tool + 1,
                **prefixes(history, events),
            },
            "pending_tool": None,
            "completed_tool_receipts": [receipt],
        },
    )


def tool_skipped_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    step_index: int,
    tool_index: int,
    call_id: str,
    name: str,
    result_digest: str,
    outcome: str = "denied",
) -> dict[str, Any]:
    """Advance one exact unexecuted tool without reserving tool budget."""
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    tool = _index(tool_index, "tool_index")
    safe_call_id = _text(call_id, MAX_SAFE_ID, "call_id")
    safe_name = _text(name, MAX_TOOL_NAME, "name")
    digest = valid_sha256(result_digest)
    if digest is None:
        raise AgentRunTransitionError("result_digest must be a SHA-256 digest")
    if outcome not in {"denied", "skipped"}:
        raise AgentRunTransitionError("unexecuted tool outcome is invalid")
    pending = current.get("pending_tool")
    expected_attempt = tool_attempt_id(run_id, step, tool, safe_call_id)
    compatible_pending = pending is None or (
        isinstance(pending, dict)
        and pending.get("attempt_id") == expected_attempt
        and pending.get("call_id") == safe_call_id
        and pending.get("name") == safe_name
        and pending.get("status") == "not_started"
        and pending.get("budget_reserved") is False
    )
    if (
        cursor.get("phase") != "tools_ready"
        or cursor.get("step_index") != step
        or cursor.get("next_tool_index") != tool
        or tool >= int(cursor.get("expected_tool_count", 0))
        or current.get("pending_interaction") is not None
        or current.get("pending_model") is not None
        or not compatible_pending
    ):
        raise AgentRunTransitionError(
            "tool_skipped must match the exact next unexecuted tool"
        )
    receipt = {
        "attempt_id": expected_attempt,
        "call_id": safe_call_id,
        "name": safe_name,
        "ok": False,
        "outcome": outcome,
        "transcript_index": max(0, len(history) - 1),
        "result_sha256": digest,
    }
    return merge_continuation(
        current,
        {
            "cursor": {
                **cursor,
                "phase": "tools_ready",
                "next_tool_index": tool + 1,
                **prefixes(history, events),
            },
            "pending_tool": None,
            "completed_tool_receipts": [receipt],
        },
    )


def interrupt_inflight_tool_transition(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Suspend an exact in-flight tool according to its retry classification."""
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    pending = current.get("pending_tool")
    if (
        cursor.get("phase") != "tool_in_flight"
        or not isinstance(pending, dict)
        or pending.get("status") != "in_flight"
        or pending.get("budget_reserved") is not True
        or pending.get("retry_class") not in {"safe", "consequential"}
        or current.get("pending_model") is not None
        or current.get("pending_interaction") is not None
    ):
        raise AgentRunTransitionError(
            "tool interruption requires the exact reserved in-flight tool"
        )
    retry_safe = pending["retry_class"] == "safe"
    return merge_continuation(
        current,
        {
            "cursor": {
                **cursor,
                "phase": "tools_ready" if retry_safe else "review_required",
                **prefixes(history, events),
            },
            "pending_tool": {
                **pending,
                "status": "retry_ready" if retry_safe else "outcome_unknown",
            },
        },
    )


def adopt_completed_approval_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    step_index: int,
    tool_index: int,
    call_id: str,
    name: str,
    ok: bool,
    result_digest: str,
) -> dict[str, Any]:
    """Adopt one terminal externally executed approval into run accounting."""
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    tool = _index(tool_index, "tool_index")
    safe_call_id = _text(call_id, MAX_SAFE_ID, "call_id")
    safe_name = _text(name, MAX_TOOL_NAME, "name")
    digest = valid_sha256(result_digest)
    if digest is None:
        raise AgentRunTransitionError("result_digest must be a SHA-256 digest")
    pending = current.get("pending_tool")
    if (
        cursor.get("phase") != "tools_ready"
        or cursor.get("step_index") != step
        or cursor.get("next_tool_index") != tool
        or tool >= int(cursor.get("expected_tool_count", 0))
        or not isinstance(pending, dict)
        or pending.get("attempt_id")
        != tool_attempt_id(run_id, step, tool, safe_call_id)
        or pending.get("call_id") != safe_call_id
        or pending.get("name") != safe_name
        or pending.get("retry_class") != "consequential"
        or pending.get("status") != "not_started"
        or pending.get("budget_reserved") is not False
        or current.get("pending_interaction") is not None
        or current.get("pending_model") is not None
    ):
        raise AgentRunTransitionError(
            "external approval adoption must match the exact pending tool"
        )
    budgets = _budgets(current)
    if budgets.get("tool_calls", 0) < 1:
        raise AgentRunTransitionError("Agent Run tool-call budget exhausted")
    budgets["tool_calls"] -= 1
    receipt = {
        "attempt_id": pending["attempt_id"],
        "call_id": safe_call_id,
        "name": safe_name,
        "ok": bool(ok),
        "outcome": "executed_external" if ok else "failed_external",
        "transcript_index": len(history),
        "result_sha256": digest,
    }
    return merge_continuation(
        current,
        {
            "cursor": {
                **cursor,
                "phase": "tools_ready",
                "next_tool_index": tool + 1,
                **prefixes(history, events),
            },
            "pending_tool": None,
            "completed_tool_receipts": [receipt],
            "remaining_budgets": budgets,
        },
    )


def waiting_approval_transition(
    snapshot: dict[str, Any],
    run_id: str,
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    interaction_id: str,
    step_index: int,
    tool_index: int,
    call_id: str,
    name: str,
    retry_safe: bool,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    tool = _index(tool_index, "tool_index")
    safe_call_id = _text(call_id, MAX_SAFE_ID, "call_id")
    safe_name = _text(name, MAX_TOOL_NAME, "name")
    if (
        cursor.get("phase") != "tools_ready"
        or cursor.get("step_index") != step
        or cursor.get("next_tool_index") != tool
        or tool >= int(cursor.get("expected_tool_count", 0))
        or any(
            current.get(section) is not None
            for section in (
                "pending_interaction",
                "resolved_approval",
                "pending_model",
                "pending_tool",
            )
        )
    ):
        raise AgentRunTransitionError(
            "waiting_approval requires the exact next unstarted tool"
        )
    return merge_continuation(
        current,
        {
            "cursor": {**cursor, "phase": "waiting_approval", **prefixes(history, events)},
            "pending_interaction": {
                "kind": "approval",
                "id": _text(interaction_id, MAX_SAFE_ID, "interaction_id"),
            },
            "pending_tool": {
                "attempt_id": tool_attempt_id(
                    run_id, step, tool, safe_call_id
                ),
                "call_id": safe_call_id,
                "name": safe_name,
                "retry_class": "safe" if retry_safe else "consequential",
                "status": "not_started",
                "budget_reserved": False,
            },
        },
    )


def waiting_external_execution_transition(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    interaction_id: str,
    step_index: int,
    tool_index: int,
    call_id: str,
    name: str,
) -> dict[str, Any]:
    """Park a run while another claimant finishes an approved tool."""
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    step = _index(step_index, "step_index")
    tool = _index(tool_index, "tool_index")
    interaction = _text(interaction_id, MAX_SAFE_ID, "interaction_id")
    safe_call_id = _text(call_id, MAX_SAFE_ID, "call_id")
    safe_name = _text(name, MAX_TOOL_NAME, "name")
    pending = current.get("pending_tool")
    if (
        cursor.get("phase") != "tools_ready"
        or cursor.get("step_index") != step
        or cursor.get("next_tool_index") != tool
        or tool >= int(cursor.get("expected_tool_count", 0))
        or not isinstance(pending, dict)
        or pending.get("call_id") != safe_call_id
        or pending.get("name") != safe_name
        or pending.get("retry_class") != "consequential"
        or pending.get("status") != "not_started"
        or pending.get("budget_reserved") is not False
        or current.get("pending_interaction") is not None
        or current.get("pending_model") is not None
    ):
        raise AgentRunTransitionError(
            "waiting_external requires the exact pending approved tool"
        )
    return merge_continuation(
        current,
        {
            "cursor": {
                **cursor,
                "phase": "waiting_external",
                **prefixes(history, events),
            },
            "pending_interaction": {
                "kind": "approval",
                "id": interaction,
            },
        },
    )


def approval_ready_transition(
    snapshot: dict[str, Any], interaction_id: str, decision: str
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    interaction = _text(interaction_id, MAX_SAFE_ID, "interaction_id")
    if decision not in {"allow", "deny"}:
        raise AgentRunTransitionError("approval decision must be allow or deny")
    if (
        cursor.get("phase") != "waiting_approval"
        or current.get("pending_interaction")
        != {"kind": "approval", "id": interaction}
        or not isinstance(current.get("pending_tool"), dict)
        or current["pending_tool"].get("status") != "not_started"
        or current["pending_tool"].get("budget_reserved") is not False
    ):
        raise AgentRunTransitionError(
            "approval decision must match the exact waiting tool"
        )
    return merge_continuation(
        current,
        {
            "cursor": {**cursor, "phase": "approval_ready"},
            "resolved_approval": {"id": interaction, "decision": decision},
        },
    )


def approval_resolved_transition(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    interaction_id: str,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    interaction = _text(interaction_id, MAX_SAFE_ID, "interaction_id")
    resolved = current.get("resolved_approval")
    pending_tool = current.get("pending_tool")
    if (
        cursor.get("phase") != "approval_ready"
        or current.get("pending_interaction")
        != {"kind": "approval", "id": interaction}
        or not isinstance(resolved, dict)
        or resolved.get("id") != interaction
        or resolved.get("decision") not in {"allow", "deny"}
        or current.get("pending_model") is not None
        or not isinstance(pending_tool, dict)
        or pending_tool.get("status") != "not_started"
        or pending_tool.get("budget_reserved") is not False
    ):
        raise AgentRunTransitionError(
            "approval_resolved must match the pending approval"
        )
    patch: dict[str, Any] = {
        "cursor": {**cursor, "phase": "tools_ready", **prefixes(history, events)},
        "pending_interaction": None,
        "resolved_approval": None,
    }
    if resolved["decision"] == "deny":
        patch["cursor"] = {
            **patch["cursor"],
            "next_tool_index": int(cursor.get("next_tool_index", 0)) + 1,
        }
        patch["pending_tool"] = None
        patch["completed_tool_receipts"] = [
            {
                "attempt_id": pending_tool["attempt_id"],
                "call_id": pending_tool["call_id"],
                "name": pending_tool["name"],
                "ok": False,
                "outcome": "denied",
                "transcript_index": max(0, len(history) - 1),
                "result_sha256": None,
            }
        ]
    return merge_continuation(
        current,
        patch,
    )


def terminal_transition(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
    events: list[dict[str, Any]],
    state: str,
    message_id: str,
    text_length: int,
) -> dict[str, Any]:
    current = project_continuation(snapshot)
    cursor = _cursor(current)
    phase = cursor.get("phase")
    if state in {"complete", "partial"}:
        legal = phase == "terminal_ready"
    elif state in {"failed", "stopped"}:
        legal = phase in _ERROR_TERMINAL_PHASES
    else:
        legal = False
    if not legal:
        raise AgentRunTransitionError(
            f"terminal {state} is not legal from {phase}"
        )
    length = _index(text_length, "text_length")
    return merge_continuation(
        current,
        {
            "cursor": {**cursor, "phase": state, **prefixes(history, events)},
            "visible_partial": {
                "message_id": _text(message_id, MAX_SAFE_ID, "message_id"),
                "text_length": length,
                "truncated": False,
            },
            "pending_interaction": None,
            "pending_model": None,
            "pending_tool": None,
        },
    )


def model_attempt_id(run_id: str, step: int) -> str:
    return _bounded_attempt_id(f"{run_id}:{step}:model", [run_id, step, "model"])


def tool_attempt_id(run_id: str, step: int, tool: int, call_id: str) -> str:
    return _bounded_attempt_id(
        f"{run_id}:{step}:{call_id}:{tool}", [run_id, step, tool, call_id]
    )


def prefixes(
    history: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "transcript_prefix_count": len(history),
        "transcript_prefix_sha256": transcript_prefix_sha256(history),
        "event_prefix_count": len(events),
        "event_prefix_sha256": transcript_prefix_sha256(events),
    }


def _cursor(snapshot: dict[str, Any]) -> dict[str, Any]:
    return dict(snapshot.get("cursor", {}))


def _budgets(snapshot: dict[str, Any]) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in snapshot.get("remaining_budgets", {}).items()
    }


def _index(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentRunTransitionError(f"{field} must be a nonnegative integer")
    return value


def _text(value: str, limit: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or redact_sensitive_assignments(value) != value
    ):
        raise AgentRunTransitionError(f"invalid {field}")
    return value


def _bounded_attempt_id(readable: str, identity: list[Any]) -> str:
    if len(readable) <= MAX_SAFE_ID:
        return readable
    canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return "attempt_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
