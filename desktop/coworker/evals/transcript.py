"""Transcript validation and atomic compaction for agent evaluations."""

from __future__ import annotations

from typing import Any


def _call_ids(message: dict[str, Any], *, index: int) -> tuple[list[str], list[str]]:
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list):
        return [], [f"message {index}: assistant tool_calls must be a list"]
    ids: list[str] = []
    issues: list[str] = []
    for offset, call in enumerate(calls):
        call_id = str(call.get("id") or "") if isinstance(call, dict) else ""
        if not call_id:
            issues.append(f"message {index}: tool call {offset} has no id")
        elif call_id in ids:
            issues.append(f"message {index}: duplicate tool call {call_id}")
        else:
            ids.append(call_id)
    return ids, issues


def transcript_issues(messages: list[dict[str, Any]]) -> list[str]:
    """Return assistant/tool adjacency violations in one model transcript."""
    issues: list[str] = []
    pending: set[str] | None = None
    pending_index: int | None = None
    for index, message in enumerate(messages):
        role = message.get("role")
        if pending is not None:
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                if not call_id:
                    issues.append(f"message {index}: tool result has no tool_call_id")
                elif call_id not in pending:
                    issues.append(
                        f"message {index}: unexpected or duplicate tool result {call_id}"
                    )
                else:
                    pending.remove(call_id)
                if not pending:
                    pending = None
                    pending_index = None
                continue
            issues.append(
                f"message {pending_index}: open tool calls {sorted(pending)!r}"
            )
            pending = None
            pending_index = None
        if role == "assistant" and message.get("tool_calls"):
            call_ids, call_issues = _call_ids(message, index=index)
            issues.extend(call_issues)
            if call_ids:
                pending = set(call_ids)
                pending_index = index
        elif role == "tool":
            issues.append(
                f"message {index}: orphan tool result "
                f"{str(message.get('tool_call_id') or '<missing>')}"
            )
    if pending is not None:
        issues.append(f"message {pending_index}: open tool calls {sorted(pending)!r}")
    return issues


def _atomic_units(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Build valid ordinary messages or closed assistant/tool groups.

    Malformed assistant/tool fragments are omitted as a whole. Compaction can
    therefore retain a complete unit or drop it, but cannot create an orphan.
    """
    units: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            call_ids, issues = _call_ids(message, index=index)
            pending = set(call_ids)
            group = [message]
            cursor = index + 1
            valid = not issues and bool(call_ids)
            while cursor < len(messages) and messages[cursor].get("role") == "tool":
                tool = messages[cursor]
                group.append(tool)
                call_id = str(tool.get("tool_call_id") or "")
                if not call_id or call_id not in pending:
                    valid = False
                else:
                    pending.remove(call_id)
                cursor += 1
            if valid and not pending:
                units.append(group)
            index = cursor
            continue
        if role != "tool":
            units.append([message])
        index += 1
    return units


def compact_transcript(
    messages: list[dict[str, Any]], *, retain_messages: int
) -> list[dict[str, Any]]:
    """Keep a suffix of complete transcript units at or above the target size."""
    units = _atomic_units(messages)
    retained: list[list[dict[str, Any]]] = []
    retained_count = 0
    for unit in reversed(units):
        if retained_count >= retain_messages:
            break
        retained.insert(0, unit)
        retained_count += len(unit)
    flattened = [message for unit in retained for message in unit]
    omitted = len(messages) - len(flattened)
    if omitted <= 0:
        return flattened
    return [
        {
            "role": "user",
            "content": f"[eval compaction] omitted {omitted} earlier messages",
        },
        *flattened,
    ]
