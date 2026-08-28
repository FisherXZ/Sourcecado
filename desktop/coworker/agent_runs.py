"""Durable Agent Run vocabulary and the redaction rules for checkpoints.

A checkpoint records semantic progress, never content. Message bodies, model
reasoning, tool arguments, private tool output, and credentials stay in the
transcript, the event log, and the ledger. The allowlist below is the fence:
a field that is not named here never reaches the durable run store, so adding
one is a deliberate, reviewable change rather than an accident.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

RUN_TRIGGERS = frozenset({"chat", "queued_chat", "scheduled"})

LEASABLE_AGENT_RUN_STATES = frozenset({"running", "interrupted"})
WAITING_AGENT_RUN_STATES = frozenset(
    {"waiting_approval", "waiting_input", "waiting_external"}
)
TERMINAL_AGENT_RUN_STATES = frozenset({"complete", "partial", "stopped", "failed"})
AGENT_RUN_STATES = (
    LEASABLE_AGENT_RUN_STATES | WAITING_AGENT_RUN_STATES | TERMINAL_AGENT_RUN_STATES
)

CHECKPOINT_KINDS = frozenset(
    {
        "run_started",
        "model_pending",
        "model_completed",
        "tool_pending",
        "tool_completed",
        "tool_outcome_unknown",
        "waiting_approval",
        "waiting_input",
        "waiting_external",
        "approval_resolved",
        "process_interrupted",
        "terminal",
    }
)
# A run carrying either of these has a hole in its record: work happened that
# no checkpoint describes. Readers must not call silence elsewhere an absence,
# and retention must not delete the only marker that the hole exists.
INCOMPLETE_RECORD_KINDS = frozenset({"process_interrupted", "tool_outcome_unknown"})

_ID_LIMIT = 256
_SHORT_LIMIT = 128
_SUMMARY_LIMIT = 512
_LIST_LIMIT = 50


def _id_text(value: Any) -> str | None:
    text = redact_secrets(str(value))[:_ID_LIMIT].strip()
    return text or None


def _short_text(value: Any) -> str | None:
    text = redact_secrets(str(value))[:_SHORT_LIMIT].strip()
    return text or None


def _summary_text(value: Any) -> str | None:
    text = redact_secrets(str(value))[:_SUMMARY_LIMIT]
    return text or None


def _index(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _count(value: Any) -> int | None:
    parsed = _index(value)
    return None if parsed is None else max(0, parsed)


def _id_list(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    return merge_unique_strings([], [str(item) for item in value])[:_LIST_LIMIT]


def _usage(value: Any) -> dict[str, int | float] | None:
    if not isinstance(value, dict):
        return None
    return add_usage({}, value)


# The complete set of fields a checkpoint payload may carry, each with the
# coercion that bounds it. Anything else is dropped before persistence.
_CHECKPOINT_FIELDS = {
    "step": _index,
    "attempt_id": _id_text,
    "tool_name": _id_text,
    "tool_call_id": _id_text,
    "tool_call_ids": _id_list,
    "approval_id": _id_text,
    "person_id": _id_text,
    "provider": _id_text,
    "model_id": _id_text,
    "persona_id": _id_text,
    "prompt_version": _short_text,
    "status": _short_text,
    "reason": _short_text,
    "error_class": _short_text,
    "error_summary": _summary_text,
    "text_length": _count,
    "item_count": _count,
    "duration_ms": _count,
    "usage": _usage,
    "source_ref_ids": _id_list,
    "artifact_ref_ids": _id_list,
}
CHECKPOINT_PAYLOAD_FIELDS = frozenset(_CHECKPOINT_FIELDS)

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_HEADER_RE = re.compile(
    r"(?P<prefix>[\"']?authorization[\"']?[ \t]*[:=][ \t]*)"
    r"(?P<quote>[\"']?)(?:Bearer|Basic)[ \t]+[^\"'\s,;}]+(?P=quote)",
    re.IGNORECASE,
)
_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<name>(?:[A-Za-z][A-Za-z0-9_.-]*?)?"
    r"(?:token|secret|password|credential|cookie|api[-_]?key|authorization))"
    r"(?P<sep>[ \t]*[:=][ \t]*)"
    r"(?:(?:Bearer|Basic)[ \t]+)?"
    r"(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,;]+)",
    re.IGNORECASE,
)
_PROVIDER_TOKEN_RE = re.compile(
    r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|xoxb-[A-Za-z0-9-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})"
)


def redact_secrets(value: str) -> str:
    """Mask credential-shaped text so a summary can never carry a secret."""
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", str(value))
    redacted = _HEADER_RE.sub(
        lambda m: f"{m.group('prefix')}{m.group('quote')}[REDACTED]{m.group('quote')}",
        redacted,
    )
    redacted = _ASSIGNMENT_RE.sub(
        lambda m: f"{m.group('name')}{m.group('sep')}[REDACTED]", redacted
    )
    return _PROVIDER_TOKEN_RE.sub("[REDACTED]", redacted)


def checkpoint_payload(payload: Any) -> dict[str, Any]:
    """Project a caller's payload onto the allowlist. Unknown fields are dropped."""
    if not isinstance(payload, dict):
        return {}
    projected: dict[str, Any] = {}
    for name, coerce in _CHECKPOINT_FIELDS.items():
        if name not in payload:
            continue
        value = coerce(payload[name])
        if value is None:
            continue
        projected[name] = value
    return projected


def goal_fingerprint(goal: str) -> str:
    """Identify the originating goal without keeping the operator's words."""
    return hashlib.sha256(str(goal).encode("utf-8")).hexdigest()


def sanitize_url(value: Any) -> str | None:
    """Keep scheme, host, and path. Query and fragment can carry credentials."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{parsed.port}" if parsed.port is not None else host
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))


def project_source_refs(values: Any) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw in values if isinstance(values, (list, tuple)) else []:
        if not isinstance(raw, dict):
            continue
        projected.append(
            {
                "id": redact_secrets(str(raw.get("id") or ""))[:_ID_LIMIT],
                "title": redact_secrets(str(raw.get("title") or ""))[:_SUMMARY_LIMIT],
                "url": sanitize_url(raw.get("url")),
                "provider": redact_secrets(str(raw.get("provider") or ""))[:_SHORT_LIMIT],
                "stale": bool(raw.get("stale", False)),
            }
        )
    return projected


def project_artifact_refs(values: Any) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw in values if isinstance(values, (list, tuple)) else []:
        if not isinstance(raw, dict):
            continue
        projected.append(
            {
                "id": redact_secrets(str(raw.get("id") or ""))[:_ID_LIMIT],
                "artifact_type": redact_secrets(str(raw.get("artifact_type") or ""))[
                    :_SHORT_LIMIT
                ],
                "title": redact_secrets(str(raw.get("title") or ""))[:_SUMMARY_LIMIT],
                "external_url": sanitize_url(raw.get("external_url")),
            }
        )
    return projected


def project_terminal_result(value: Any) -> dict[str, Any] | None:
    """How the run ended, in shape only: never the answer the operator reads."""
    if not isinstance(value, dict):
        return None
    projected: dict[str, Any] = {}
    if value.get("status") is not None:
        projected["status"] = redact_secrets(str(value["status"]))[:_SHORT_LIMIT]
    if value.get("message_id") is not None:
        projected["message_id"] = redact_secrets(str(value["message_id"]))[:_ID_LIMIT]
    length = _count(value.get("text_length"))
    if length is None and isinstance(value.get("text"), str):
        length = len(value["text"])
    if length is not None:
        projected["text_length"] = length
    if value.get("error") is not None:
        projected["error"] = redact_secrets(str(value["error"]))[:_SUMMARY_LIMIT]
    if value.get("error_class") is not None:
        projected["error_class"] = redact_secrets(str(value["error_class"]))[
            :_SHORT_LIMIT
        ]
    return projected


def merge_unique_refs(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep first-seen references, preferring a record id as the stable key."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        key = (
            f"id:{item_id}"
            if item_id not in (None, "")
            else "json:" + json.dumps(item, sort_keys=True, separators=(",", ":"))
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def merge_unique_strings(existing: list[Any], incoming: list[Any]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in [*existing, *incoming]:
        value = redact_secrets(str(raw)).strip()[:_ID_LIMIT]
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def add_usage(
    existing: dict[str, Any], deltas: dict[str, Any]
) -> dict[str, int | float]:
    usage: dict[str, int | float] = {
        str(key): value
        for key, value in existing.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    for key, value in deltas.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        name = str(key)[:_SHORT_LIMIT]
        usage[name] = usage.get(name, 0) + value
    return usage


def json_list(raw: Any) -> list[Any]:
    parsed = _decode(raw, [])
    return parsed if isinstance(parsed, list) else []


def json_object(raw: Any) -> dict[str, Any]:
    parsed = _decode(raw, {})
    return parsed if isinstance(parsed, dict) else {}


def nullable_json_object(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    parsed = _decode(raw, None)
    return parsed if isinstance(parsed, dict) else None


def _decode(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback
