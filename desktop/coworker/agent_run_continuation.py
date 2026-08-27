"""Privacy-safe durable continuation projection and monotonic merge rules."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from coworker.agent_runs import redact_sensitive_assignments


MAX_SAFE_ID = 256
MAX_TOOL_NAME = 128
MAX_RECEIPTS = 256
_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PHASES = frozenset(
    {
        "model_in_flight",
        "model_ready",
        "tool_in_flight",
        "tools_ready",
        "waiting_approval",
        "approval_ready",
        "waiting_question",
        "waiting_external",
        "review_required",
        "terminal_ready",
        "complete",
        "partial",
        "stopped",
        "failed",
    }
)


def project_continuation(value: Any) -> dict[str, Any]:
    """Allowlist one durable continuation snapshot; drop all unknown data."""
    raw = value if isinstance(value, dict) else {}
    projected: dict[str, Any] = {"schema_version": 1}

    identity = raw.get("identity")
    if isinstance(identity, dict):
        safe_identity = {
            key: safe
            for key, limit in (
                ("message_id", MAX_SAFE_ID),
                ("part_id", MAX_SAFE_ID),
            )
            if (safe := _safe_text(identity.get(key), limit)) is not None
        }
        if safe_identity:
            projected["identity"] = safe_identity

    cursor = raw.get("cursor")
    if isinstance(cursor, dict):
        safe_cursor: dict[str, Any] = {}
        phase = cursor.get("phase")
        if phase in _PHASES:
            safe_cursor["phase"] = phase
        for key in ("step_index", "next_tool_index", "expected_tool_count"):
            if key in cursor:
                safe_cursor[key] = _nonnegative_int(cursor.get(key))
        for prefix in ("transcript", "event"):
            count_key = f"{prefix}_prefix_count"
            digest_key = f"{prefix}_prefix_sha256"
            count = cursor.get(count_key)
            digest = valid_sha256(cursor.get(digest_key))
            if _is_nonnegative_int(count) and digest is not None:
                safe_cursor[count_key] = count
                safe_cursor[digest_key] = digest
        projected["cursor"] = safe_cursor

    visible = raw.get("visible_partial")
    if isinstance(visible, dict):
        safe_visible = {
            "text_length": _nonnegative_int(visible.get("text_length")),
            "truncated": bool(visible.get("truncated", False)),
        }
        message_id = _safe_text(visible.get("message_id"), MAX_SAFE_ID)
        if message_id is not None:
            safe_visible["message_id"] = message_id
        projected["visible_partial"] = safe_visible

    interaction = raw.get("pending_interaction")
    interaction_id = (
        _safe_text(interaction.get("id"), MAX_SAFE_ID)
        if isinstance(interaction, dict)
        else None
    )
    if (
        isinstance(interaction, dict)
        and interaction.get("kind") in {"approval", "question"}
        and interaction_id
    ):
        projected["pending_interaction"] = {
            "kind": interaction["kind"],
            "id": interaction_id,
        }

    resolved = raw.get("resolved_approval")
    resolved_id = (
        _safe_text(resolved.get("id"), MAX_SAFE_ID)
        if isinstance(resolved, dict)
        else None
    )
    if (
        isinstance(resolved, dict)
        and resolved_id
        and resolved.get("decision") in {"allow", "deny"}
    ):
        projected["resolved_approval"] = {
            "id": resolved_id,
            "decision": resolved["decision"],
        }

    pending_model = raw.get("pending_model")
    if isinstance(pending_model, dict):
        attempt_id = _safe_text(pending_model.get("attempt_id"), MAX_SAFE_ID)
        status = pending_model.get("status")
        budget_reserved = pending_model.get("budget_reserved")
        if (
            attempt_id
            and status in {"in_flight", "retry_ready"}
            and isinstance(budget_reserved, bool)
        ):
            projected["pending_model"] = {
                "attempt_id": attempt_id,
                "status": status,
                "budget_reserved": budget_reserved,
            }

    pending_tool = raw.get("pending_tool")
    if isinstance(pending_tool, dict):
        retry_class = pending_tool.get("retry_class")
        status = pending_tool.get("status")
        attempt_id = _safe_text(pending_tool.get("attempt_id"), MAX_SAFE_ID)
        call_id = _safe_text(pending_tool.get("call_id"), MAX_SAFE_ID)
        name = _safe_text(pending_tool.get("name"), MAX_TOOL_NAME)
        budget_reserved = pending_tool.get("budget_reserved")
        if (
            retry_class in {"safe", "consequential"}
            and status
            in {"not_started", "retry_ready", "in_flight", "outcome_unknown"}
            and attempt_id
            and call_id
            and name
            and isinstance(budget_reserved, bool)
        ):
            projected["pending_tool"] = {
                "attempt_id": attempt_id,
                "call_id": call_id,
                "name": name,
                "retry_class": retry_class,
                "status": status,
                "budget_reserved": budget_reserved,
            }

    receipts: list[dict[str, Any]] = []
    raw_receipts = raw.get("completed_tool_receipts")
    if isinstance(raw_receipts, list):
        for receipt in raw_receipts[:MAX_RECEIPTS]:
            if not isinstance(receipt, dict):
                continue
            attempt_id = _safe_text(receipt.get("attempt_id"), MAX_SAFE_ID)
            call_id = _safe_text(receipt.get("call_id"), MAX_SAFE_ID)
            name = _safe_text(receipt.get("name"), MAX_TOOL_NAME)
            if not attempt_id or not call_id or not name:
                continue
            safe_receipt = {
                    "attempt_id": attempt_id,
                    "call_id": call_id,
                    "name": name,
                    "ok": bool(receipt.get("ok", False)),
                    "transcript_index": _nonnegative_int(
                        receipt.get("transcript_index")
                    ),
                    "result_sha256": valid_sha256(receipt.get("result_sha256")),
                }
            if receipt.get("outcome") in {
                "executed",
                "denied",
                "skipped",
                "executed_external",
                "failed_external",
                "failed_unexecuted",
            }:
                safe_receipt["outcome"] = receipt["outcome"]
            receipts.append(safe_receipt)
    if "completed_tool_receipts" in raw:
        projected["completed_tool_receipts"] = receipts

    budgets = raw.get("remaining_budgets")
    if isinstance(budgets, dict):
        projected["remaining_budgets"] = {
            key: _nonnegative_int(budgets.get(key))
            for key in ("work_steps", "tool_calls", "delivery_passes")
            if key in budgets
        }
    return projected


def merge_continuation(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Merge one snapshot while enforcing append-only and monotonic fields."""
    if not isinstance(incoming, dict):
        raise ValueError("continuation must be an object")
    _validate_incoming_prefix_pairs(incoming)
    _validate_incoming_cursor_fields(incoming)
    old = project_continuation(existing)
    new = project_continuation(incoming)
    _validate_pending_updates(incoming, old, new)
    merged = dict(old)
    merged["schema_version"] = 1

    if "identity" in incoming:
        candidate = _require_identity(incoming["identity"])
        established = old.get("identity")
        if established and candidate != established:
            raise ValueError("continuation identity cannot change")
        merged["identity"] = candidate

    if "cursor" in incoming:
        old_cursor = old.get("cursor", {})
        new_cursor = new.get("cursor", {})
        for key in ("step_index", "transcript_prefix_count", "event_prefix_count"):
            if key in new_cursor and key in old_cursor:
                if int(new_cursor[key]) < int(old_cursor[key]):
                    raise ValueError(f"continuation cursor {key} cannot decrease")
        old_step = int(old_cursor.get("step_index", 0))
        new_step = int(new_cursor.get("step_index", old_step))
        if new_step == old_step:
            if (
                "next_tool_index" in new_cursor
                and "next_tool_index" in old_cursor
                and int(new_cursor["next_tool_index"])
                < int(old_cursor["next_tool_index"])
            ):
                raise ValueError(
                    "continuation cursor next_tool_index cannot decrease"
                )
            if (
                "expected_tool_count" in new_cursor
                and "expected_tool_count" in old_cursor
            ):
                old_expected = int(old_cursor["expected_tool_count"])
                new_expected = int(new_cursor["expected_tool_count"])
                if new_expected < old_expected:
                    raise ValueError(
                        "continuation cursor expected_tool_count cannot decrease"
                    )
                if old_expected > 0 and new_expected != old_expected:
                    raise ValueError(
                        "continuation cursor expected_tool_count cannot change in place"
                    )
        for count_key, digest_key in (
            ("transcript_prefix_count", "transcript_prefix_sha256"),
            ("event_prefix_count", "event_prefix_sha256"),
        ):
            if (
                count_key in new_cursor
                and count_key in old_cursor
                and new_cursor[count_key] == old_cursor[count_key]
                and old_cursor.get(digest_key) is not None
                and digest_key in new_cursor
                and new_cursor.get(digest_key) != old_cursor.get(digest_key)
            ):
                raise ValueError(f"continuation {digest_key} cannot change in place")
        merged["cursor"] = {**old_cursor, **new_cursor}

    for section in (
        "visible_partial",
        "pending_interaction",
        "resolved_approval",
        "pending_model",
        "pending_tool",
    ):
        if section not in incoming:
            continue
        if section in new:
            merged[section] = new[section]
        else:
            merged.pop(section, None)

    if "completed_tool_receipts" in incoming:
        old_receipts = list(old.get("completed_tool_receipts", []))
        by_identity = {
            (receipt["attempt_id"], receipt["call_id"]): receipt
            for receipt in old_receipts
        }
        for receipt in new.get("completed_tool_receipts", []):
            identity = (receipt["attempt_id"], receipt["call_id"])
            prior = by_identity.get(identity)
            if prior is not None:
                if prior != receipt:
                    raise ValueError("completed tool receipt cannot change")
                continue
            if len(old_receipts) >= MAX_RECEIPTS:
                break
            old_receipts.append(receipt)
            by_identity[identity] = receipt
        merged["completed_tool_receipts"] = old_receipts

    if "remaining_budgets" in incoming:
        old_budgets = old.get("remaining_budgets", {})
        new_budgets = new.get("remaining_budgets", {})
        for key, value in new_budgets.items():
            if key in old_budgets and value > old_budgets[key]:
                raise ValueError(f"continuation budget {key} cannot increase")
        merged["remaining_budgets"] = {**old_budgets, **new_budgets}

    return project_continuation(merged)


def transcript_prefix_sha256(messages: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def valid_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    return lowered if _HEX_SHA256.fullmatch(lowered) is not None else None


def _safe_text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return redact_sensitive_assignments(value)[:limit]


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("continuation identity must be an object")
    message_id = _safe_text(value.get("message_id"), MAX_SAFE_ID)
    part_id = _safe_text(value.get("part_id"), MAX_SAFE_ID)
    if not message_id or not part_id:
        raise ValueError("continuation identity requires message_id and part_id")
    return {"message_id": message_id, "part_id": part_id}


def _validate_incoming_prefix_pairs(incoming: dict[str, Any]) -> None:
    cursor = incoming.get("cursor")
    if not isinstance(cursor, dict):
        return
    for prefix in ("transcript", "event"):
        count_key = f"{prefix}_prefix_count"
        digest_key = f"{prefix}_prefix_sha256"
        has_count = count_key in cursor
        has_digest = digest_key in cursor
        if not has_count and not has_digest:
            continue
        if not has_count or not has_digest:
            raise ValueError(f"continuation {prefix} prefix requires count and digest")
        if not _is_nonnegative_int(cursor[count_key]):
            raise ValueError(f"continuation {count_key} must be nonnegative")
        if valid_sha256(cursor[digest_key]) is None:
            raise ValueError(f"continuation {digest_key} must be a SHA-256 digest")


def _validate_incoming_cursor_fields(incoming: dict[str, Any]) -> None:
    cursor = incoming.get("cursor")
    if not isinstance(cursor, dict):
        return
    if "expected_tool_count" in cursor and not _is_nonnegative_int(
        cursor["expected_tool_count"]
    ):
        raise ValueError(
            "continuation expected_tool_count must be nonnegative"
        )
    if (
        _is_nonnegative_int(cursor.get("next_tool_index"))
        and _is_nonnegative_int(cursor.get("expected_tool_count"))
        and cursor["next_tool_index"] > cursor["expected_tool_count"]
    ):
        raise ValueError(
            "continuation next_tool_index cannot exceed expected_tool_count"
        )


def _validate_pending_updates(
    incoming: dict[str, Any],
    old: dict[str, Any],
    new: dict[str, Any],
) -> None:
    invariants = {
        "pending_model": ("attempt_id", "budget_reserved"),
        "pending_tool": (
            "attempt_id",
            "call_id",
            "name",
            "retry_class",
            "budget_reserved",
        ),
    }
    for section, keys in invariants.items():
        if section not in incoming or incoming[section] is None:
            continue
        if section not in new:
            raise ValueError(f"continuation {section} is malformed")
        prior = old.get(section)
        candidate = new[section]
        if not isinstance(prior, dict):
            continue
        changed = [
            key for key in keys if prior.get(key) != candidate.get(key)
        ]
        reserves_approved_tool = (
            section == "pending_tool"
            and changed == ["budget_reserved"]
            and prior.get("budget_reserved") is False
            and candidate.get("budget_reserved") is True
            and prior.get("status") == "not_started"
            and candidate.get("status") in {"not_started", "in_flight"}
        )
        if changed and not reserves_approved_tool:
            raise ValueError(f"continuation {section} cannot change reservation")
    if (
        "resolved_approval" in incoming
        and incoming["resolved_approval"] is not None
        and "resolved_approval" not in new
    ):
        raise ValueError("continuation resolved_approval is malformed")
    if (
        isinstance(old.get("resolved_approval"), dict)
        and isinstance(new.get("resolved_approval"), dict)
        and old["resolved_approval"] != new["resolved_approval"]
    ):
        raise ValueError("continuation resolved_approval cannot change")
