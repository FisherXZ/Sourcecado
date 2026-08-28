"""The Agent Run receipt: a pure projection of one run into evidence.

Nothing here reads a store, a clock, or a network. `build_receipt` takes a run
row, its checkpoints, and already-resolved owner-native approval receipts, and
returns the one shape an operator inspects. Keeping it pure is the point: an
allowlist projection with no I/O is a thing a reviewer can read end to end and
be sure of.

The rule that keeps a receipt from becoming a second transcript is mechanical.
A receipt renders only fields this module names, and every named field is an
identifier, an enum, a count, a timestamp, or a bounded reason the runtime
itself wrote. Message bodies, tool arguments, command output, prompts, raw
errors, credentials, and model reasoning have no field to land in, and `_pick`
copies named scalars only, never a nested structure.

This allowlist is deliberately independent of the write-time allowlist in
`agent_runs.CHECKPOINT_PAYLOAD_FIELDS`. Both have to hold on their own; a read
side that leaned on the write side would keep passing after the write side
changed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from coworker.agent_run_state import is_terminal
from coworker.agent_runs import (
    INCOMPLETE_RECORD_KINDS,
    project_artifact_refs,
    project_source_refs,
    project_terminal_result,
    redact_secrets,
)
from coworker.run_evidence import (
    Evidence,
    absence_evidence,
    analyze_record,
    constrain,
    most_severe,
)

RECEIPT_VERSION = 1

RECEIPT_SECTIONS = frozenset(
    {
        "prompt",
        "model_attempts",
        "usage",
        "tools",
        "sources",
        "artifacts",
        "approvals",
        "recovery",
        "rationale",
        "outcome",
    }
)

_TEXT_LIMIT = 512
_MAX_ENTRIES = 200
_PARK_KINDS = frozenset({"waiting_approval", "waiting_input", "waiting_external"})
_MODEL_FIELDS = (
    "attempt_id", "provider", "model_id", "status", "error_class", "duration_ms",
)
_TOOL_FIELDS = (
    "tool_call_id",
    "tool_name",
    "status",
    "error_class",
    "duration_ms",
    "item_count",
    "reason",
)


def build_receipt(
    run: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    *,
    approvals: dict[str, dict[str, Any] | None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One receipt shape for every run, whatever triggered it or ended it."""
    moment = now or datetime.now(UTC)
    record = analyze_record(run, checkpoints)
    state = str(run.get("current_state") or "")
    created = parse_moment(run.get("created_at"))
    finished = parse_moment(run.get("finished_at"))
    return {
        "receipt_version": RECEIPT_VERSION,
        "run": {
            "run_id": str(run.get("run_id") or ""),
            "session_id": str(run.get("session_id") or ""),
            "person_id": run.get("person_id"),
            "parent_run_id": run.get("parent_run_id"),
            "trigger": str(run.get("trigger") or ""),
            "state": state,
            "goal_fingerprint": run.get("goal_fingerprint"),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
            "finished_at": run.get("finished_at"),
            "duration_ms": _elapsed_ms(created, finished or moment),
            "version": run.get("version"),
            "owned": _owned(run, moment),
        },
        "record": {
            "complete": record["settled"],
            "checkpoints_stored": record["stored"],
            "checkpoints_expected": record["expected"],
            "pruned_through_sequence": record["pruned_through"],
            "damaged": record["damaged"],
            "unsupported": list(record["unsupported"]),
        },
        "prompt": _prompt(checkpoints, record),
        "model_attempts": _model_attempts(checkpoints, record),
        "usage": _usage(run, record),
        "tools": _tools(checkpoints, record),
        "sources": _sources(run, record),
        "artifacts": _artifacts(run, record),
        "approvals": _approvals(run, checkpoints, approvals, record),
        "recovery": _recovery(checkpoints, record),
        "rationale": _rationale(checkpoints, record),
        "outcome": _outcome(run, record, state, created, finished),
    }


def _prompt(
    checkpoints: list[dict[str, Any]], record: dict[str, Any]
) -> dict[str, Any]:
    persona_id: str | None = None
    prompt_version: str | None = None
    for item in checkpoints:
        payload = item.get("payload") or {}
        persona_id = persona_id or _text(payload.get("persona_id"))
        prompt_version = prompt_version or _text(payload.get("prompt_version"))
    found = persona_id is not None or prompt_version is not None
    return {
        "evidence": (
            constrain(record, Evidence.PRESENT) if found else absence_evidence(record)
        ),
        "persona_id": persona_id,
        "prompt_version": prompt_version,
    }


def _model_attempts(
    checkpoints: list[dict[str, Any]], record: dict[str, Any]
) -> dict[str, Any]:
    attempts = _lifecycle(
        checkpoints,
        start_kinds={"model_pending"},
        end_kinds={"model_completed"},
        key_field="attempt_id",
        fields=_MODEL_FIELDS,
    )
    for attempt in attempts:
        attempt.pop("_unknown")
        attempt["outcome"] = "completed" if attempt.pop("_settled") else "pending"
    observed = Evidence.PRESENT
    if not attempts:
        observed = absence_evidence(record)
    elif any(attempt["outcome"] == "pending" for attempt in attempts):
        observed = Evidence.PARTIAL
    return {
        "evidence": constrain(record, observed) if attempts else observed,
        "attempts": attempts,
        "attempt_count": len(attempts),
        "distinct_models": len({a["model_id"] for a in attempts if a["model_id"]}),
    }


def _tools(checkpoints: list[dict[str, Any]], record: dict[str, Any]) -> dict[str, Any]:
    calls = _lifecycle(
        checkpoints,
        start_kinds={"tool_pending"},
        end_kinds={"tool_completed", "tool_outcome_unknown"},
        key_field="tool_call_id",
        fields=_TOOL_FIELDS,
        unknown_kinds={"tool_outcome_unknown"},
    )
    pending = 0
    unknown = 0
    for call in calls:
        never_reported = call.pop("_unknown")
        settled = call.pop("_settled")
        if never_reported:
            call["lifecycle"] = "unknown"
            unknown += 1
        elif settled:
            call["lifecycle"] = "completed"
        else:
            call["lifecycle"] = "pending"
            pending += 1
    if not calls:
        observed = absence_evidence(record)
    elif unknown:
        observed = Evidence.AMBIGUOUS
    elif pending:
        observed = Evidence.PARTIAL
    else:
        observed = Evidence.PRESENT
    return {
        "evidence": constrain(record, observed) if calls else observed,
        "calls": calls,
        "pending_count": pending,
        "unknown_count": unknown,
    }


def _lifecycle(
    checkpoints: list[dict[str, Any]],
    *,
    start_kinds: set[str],
    end_kinds: set[str],
    key_field: str,
    fields: tuple[str, ...],
    unknown_kinds: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Pair opening and closing checkpoints into one entry per call or attempt."""
    entries: dict[str, dict[str, Any]] = {}
    for item in checkpoints:
        kind = str(item.get("kind"))
        if kind not in start_kinds and kind not in end_kinds:
            continue
        payload = item.get("payload") or {}
        sequence = int(item.get("sequence") or 0)
        key = _text(payload.get(key_field)) or f"sequence:{sequence}"
        entry = entries.get(key)
        if entry is None:
            if len(entries) >= _MAX_ENTRIES:
                continue
            entry = {name: None for name in fields}
            entry.update(
                {"first_sequence": sequence, "last_sequence": sequence,
                 "_settled": False, "_unknown": False}
            )
            entries[key] = entry
        for name, value in _pick(payload, fields).items():
            entry[name] = value
        entry["last_sequence"] = sequence
        if kind in end_kinds:
            entry["_settled"] = True
        if unknown_kinds and kind in unknown_kinds:
            entry["_unknown"] = True
    return sorted(entries.values(), key=lambda entry: entry["first_sequence"])


def _usage_totals(run: dict[str, Any]) -> dict[str, int | float]:
    """Numbers only. A usage map is a count, never somewhere text can ride."""
    return {
        _text(key) or "": value
        for key, value in (run.get("usage") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _usage(run: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    totals = _usage_totals(run)
    return {
        "evidence": (
            Evidence.PRESENT if totals else absence_evidence(record, row_backed=True)
        ),
        "totals": totals,
    }


def _sources(run: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    refs = project_source_refs(run.get("source_refs") or [])[:_MAX_ENTRIES]
    return {
        "evidence": (
            Evidence.PRESENT if refs else absence_evidence(record, row_backed=True)
        ),
        "refs": refs,
        "stale_count": sum(1 for ref in refs if ref.get("stale")),
    }


def _artifacts(run: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    refs = project_artifact_refs(run.get("artifact_refs") or [])[:_MAX_ENTRIES]
    return {
        "evidence": (
            Evidence.PRESENT if refs else absence_evidence(record, row_backed=True)
        ),
        "refs": refs,
    }


def approval_ids(run: dict[str, Any], checkpoints: list[dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    candidates = list(run.get("approval_ids") or []) + [
        (item.get("payload") or {}).get("approval_id") for item in checkpoints
    ]
    for raw in candidates:
        approval_id = _text(raw)
        if approval_id is None or approval_id in seen or len(ordered) >= _MAX_ENTRIES:
            continue
        seen.add(approval_id)
        ordered.append(approval_id)
    return ordered


def _approvals(
    run: dict[str, Any],
    checkpoints: list[dict[str, Any]],
    resolved: dict[str, dict[str, Any] | None] | None,
    record: dict[str, Any],
) -> dict[str, Any]:
    ids = approval_ids(run, checkpoints)
    requests = _approval_requests(checkpoints)
    decisions = [
        _approval_entry(approval_id, requests.get(approval_id, {}), resolved, record)
        for approval_id in ids
    ]
    if not decisions:
        # No id to verify, so no verifier is needed: the run row itself says
        # whether an approval was ever requested.
        evidence = absence_evidence(record, row_backed=True)
    else:
        evidence = most_severe(item["evidence"] for item in decisions)
    return {"evidence": evidence, "decisions": decisions}


def _approval_requests(checkpoints: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    requests: dict[str, dict[str, Any]] = {}
    for item in checkpoints:
        payload = item.get("payload") or {}
        approval_id = _text(payload.get("approval_id"))
        if approval_id is None:
            continue
        entry = requests.setdefault(
            approval_id, {"requested_sequence": None, "tool_name": None}
        )
        if str(item.get("kind")) in _PARK_KINDS and entry["requested_sequence"] is None:
            entry["requested_sequence"] = int(item.get("sequence") or 0)
        entry["tool_name"] = entry["tool_name"] or _text(payload.get("tool_name"))
    return requests


def _approval_entry(
    approval_id: str,
    request: dict[str, Any],
    resolved: dict[str, dict[str, Any] | None] | None,
    record: dict[str, Any],
) -> dict[str, Any]:
    """One decision, always sourced from the owner-native receipt.

    The run store's own fields never become the decision. An id this module
    cannot verify is `missing`; it is never an allowance.
    """
    entry = {
        "approval_id": approval_id,
        "requested": request.get("requested_sequence") is not None,
        "requested_sequence": request.get("requested_sequence"),
        "tool_name": request.get("tool_name"),
        "decision": None,
        "state": None,
        "execution_status": None,
        "actor": None,
        "evidence": Evidence.UNSUPPORTED,
    }
    if resolved is None:
        return entry
    native = resolved.get(approval_id)
    if native is None:
        entry["evidence"] = Evidence.MISSING
        return entry
    state = _text(native.get("state"))
    execution = _text(native.get("execution_status"))
    decision = _text(native.get("decision"))
    entry["state"] = state
    entry["execution_status"] = execution
    if state == "expired":
        entry["evidence"] = Evidence.EXPIRED
    elif state == "pending":
        entry["evidence"] = Evidence.PARTIAL
    elif state == "resolved" and decision in {"allow", "deny"}:
        entry["decision"] = decision
        entry["actor"] = _text(native.get("actor"))
        entry["evidence"] = (
            Evidence.AMBIGUOUS if execution == "interrupted" else Evidence.PRESENT
        )
    elif state == "resolved":
        entry["evidence"] = Evidence.MISSING
    else:
        entry["evidence"] = Evidence.UNSUPPORTED
    return entry


def _recovery(
    checkpoints: list[dict[str, Any]], record: dict[str, Any]
) -> dict[str, Any]:
    events = [
        {
            "sequence": int(item.get("sequence") or 0),
            "kind": str(item.get("kind")),
            "state": str(item.get("state")),
            "reason": _text((item.get("payload") or {}).get("reason")),
            "tool_call_id": _text((item.get("payload") or {}).get("tool_call_id")),
            "created_at": item.get("created_at"),
        }
        for item in checkpoints
        if str(item.get("kind")) in INCOMPLETE_RECORD_KINDS
    ][:_MAX_ENTRIES]
    return {
        "evidence": Evidence.PRESENT if events else absence_evidence(record),
        "events": events,
    }


def _rationale(
    checkpoints: list[dict[str, Any]], record: dict[str, Any]
) -> dict[str, Any]:
    """The bounded reasons the runtime wrote down. Never model reasoning.

    There is deliberately no free-text field of the ledger's own here: a note
    can only repeat `reason` or `error_summary`, both already bounded and
    redacted by the checkpoint allowlist.
    """
    notes = []
    for item in checkpoints:
        payload = item.get("payload") or {}
        reason = _text(payload.get("reason"))
        summary = _text(payload.get("error_summary"))
        if reason is None and summary is None:
            continue
        notes.append(
            {
                "sequence": int(item.get("sequence") or 0),
                "kind": str(item.get("kind")),
                "reason": reason,
                "error_summary": summary,
            }
        )
        if len(notes) >= _MAX_ENTRIES:
            break
    return {
        "evidence": (
            constrain(record, Evidence.PRESENT) if notes else absence_evidence(record)
        ),
        "notes": notes,
    }


def _outcome(
    run: dict[str, Any],
    record: dict[str, Any],
    state: str,
    created: datetime | None,
    finished: datetime | None,
) -> dict[str, Any]:
    result = project_terminal_result(run.get("terminal_result"))
    terminal = is_terminal(state)
    if record["unsupported"]:
        evidence = Evidence.UNSUPPORTED
    elif not terminal:
        # Not "we lost the record" — the run has not ended. `open` says which.
        evidence = Evidence.MISSING
    elif result:
        evidence = Evidence.PRESENT
    else:
        evidence = Evidence.PARTIAL
    return {
        "evidence": evidence,
        "state": state,
        "open": not terminal,
        "result": result or None,
        "finished_at": run.get("finished_at"),
        "duration_ms": _elapsed_ms(created, finished) if finished else None,
    }


def run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """A pointer to a run, cheap enough to list. Evidence lives in the receipt."""
    result = project_terminal_result(run.get("terminal_result")) or {}
    created = parse_moment(run.get("created_at"))
    finished = parse_moment(run.get("finished_at"))
    return {
        "run_id": str(run.get("run_id") or ""),
        "session_id": str(run.get("session_id") or ""),
        "person_id": run.get("person_id"),
        "parent_run_id": run.get("parent_run_id"),
        "trigger": str(run.get("trigger") or ""),
        "state": str(run.get("current_state") or ""),
        "created_at": run.get("created_at"),
        "updated_at": run.get("updated_at"),
        "finished_at": run.get("finished_at"),
        "duration_ms": _elapsed_ms(created, finished) if finished else None,
        "source_count": len(run.get("source_refs") or []),
        "artifact_count": len(run.get("artifact_refs") or []),
        "approval_count": len(run.get("approval_ids") or []),
        "usage": _usage_totals(run),
        "outcome_status": result.get("status"),
    }


def _pick(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Only named scalars escape a payload. Nested structures never do."""
    picked: dict[str, Any] = {}
    for name in fields:
        value = payload.get(name)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            picked[name] = value
        elif isinstance(value, str):
            text = _text(value)
            if text is not None:
                picked[name] = text
    return picked


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = redact_secrets(value)[:_TEXT_LIMIT].strip()
    return text or None


def parse_moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _owned(run: dict[str, Any], now: datetime) -> bool:
    """Whether a process holds this run right now. The owner's id stays private."""
    expires = parse_moment(run.get("lease_expires_at"))
    return bool(run.get("lease_owner")) and expires is not None and expires > now
