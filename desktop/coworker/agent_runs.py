"""Durable Agent Run vocabulary and JSON merge helpers.

Agent Run checkpoints describe semantic progress only. Presentation stream
deltas stay in the event log and never enter this durable authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

AGENT_RUN_STATES = frozenset(
    {
        "running",
        "waiting_approval",
        "waiting_question",
        "complete",
        "partial",
        "stopped",
        "failed",
        "interrupted",
    }
)
TERMINAL_AGENT_RUN_STATES = frozenset({"complete", "partial", "stopped", "failed"})
AGENT_RUN_CHECKPOINT_KINDS = frozenset(
    {
        "run_started",
        "user_input",
        "model_completed",
        "tool_completed",
        "waiting_approval",
        "approval_resolved",
        "terminal",
        "process_interrupted",
    }
)

_SENSITIVE_KEY_MARKERS = (
    "password",
    "apikey",
    "cookie",
    "authorization",
    "header",
    "token",
    "secret",
    "credential",
    "body",
    "rawsource",
    "rawpayload",
    "awsaccesskeyid",
)
_MAX_CHECKPOINT_STRING = 512
_MAX_CHECKPOINT_ITEMS = 50
_MAX_CHECKPOINT_DEPTH = 4
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:[A-Za-z][A-Za-z0-9_.-]*?)?"
    r"(?:token|secret|password|credential|cookie)|api[-_ ]?key|authorization"
    r"|awsaccesskeyid)"
    r"[ \t]*([=:])[ \t]*(?:(?:Bearer|Basic)[ \t]+)?"
    r"(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,;]+)",
    re.IGNORECASE,
)
_JSON_AUTH_RE = re.compile(
    r"(?P<prefix>[\"']?authorization[\"']?[ \t]*[:=][ \t]*)"
    r"(?P<quote>[\"']?)(?:Bearer|Basic)[ \t]+[^\"'\s,;}]+(?P=quote)",
    re.IGNORECASE,
)
_HIGH_ENTROPY_ASSIGNMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(signature|code|key)[ \t]*([=:])[ \t]*"
    r"(?=[A-Za-z0-9_./+=-]*[0-9_./+=-])"
    r"[A-Za-z0-9_./+=-]{20,}(?![A-Za-z0-9_./+=-])",
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
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----.*?"
    r"-----END (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_REF_STRING = 512
_SAFE_ID_STRING = 256


def json_list(raw: Any) -> list[Any]:
    parsed = _json_value(raw, [])
    return parsed if isinstance(parsed, list) else []


def json_object(raw: Any) -> dict[str, Any]:
    parsed = _json_value(raw, {})
    return parsed if isinstance(parsed, dict) else {}


def nullable_json_object(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    parsed = _json_value(raw, None)
    return parsed if isinstance(parsed, dict) else None


def merge_unique_json(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep first-seen values, preferring a record id as its stable key."""
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


def merge_unique_strings(existing: list[Any], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for raw in [*existing, *incoming]:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def add_usage(
    existing: dict[str, Any], deltas: dict[str, int | float]
) -> dict[str, int | float]:
    usage: dict[str, int | float] = {
        str(key): value
        for key, value in existing.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    for key, value in deltas.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        name = str(key)
        usage[name] = usage.get(name, 0) + value
    return usage


def bounded_checkpoint_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Bound semantic summaries and discard obvious credential-shaped fields."""
    bounded = sanitize_agent_run_value(
        payload or {},
        max_string=_MAX_CHECKPOINT_STRING,
        max_items=_MAX_CHECKPOINT_ITEMS,
        max_depth=_MAX_CHECKPOINT_DEPTH,
    )
    return bounded if isinstance(bounded, dict) else {}


def safe_error_summary(message: str) -> str:
    """Keep a useful bounded failure summary without credential-shaped values."""
    return redact_sensitive_assignments(str(message))[:_MAX_CHECKPOINT_STRING]


def original_goal_fingerprint(original_goal: str) -> str:
    return hashlib.sha256(str(original_goal).encode("utf-8")).hexdigest()


def project_source_refs(values: Any) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        projected.append(
            {
                "id": _safe_string(raw.get("id"), _SAFE_ID_STRING),
                "title": _safe_string(raw.get("title"), _SAFE_REF_STRING),
                "url": sanitize_http_url(raw.get("url")),
                "provider": _safe_string(raw.get("provider"), _SAFE_REF_STRING),
                "stale": bool(raw.get("stale", False)),
                "truncated": bool(raw.get("truncated", False)),
            }
        )
    return projected


def project_artifact_refs(values: Any) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for raw in values if isinstance(values, list) else []:
        if not isinstance(raw, dict):
            continue
        projected.append(
            {
                "id": _safe_string(raw.get("id"), _SAFE_ID_STRING),
                "artifact_type": _safe_string(
                    raw.get("artifact_type"), _SAFE_REF_STRING
                ),
                "title": _safe_string(raw.get("title"), _SAFE_REF_STRING),
                "external_url": sanitize_http_url(raw.get("external_url")),
                "stale": bool(raw.get("stale", False)),
                "truncated": bool(raw.get("truncated", False)),
            }
        )
    return projected


def project_terminal_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    projected: dict[str, Any] = {}
    if value.get("status") is not None:
        projected["status"] = _safe_string(value.get("status"), 64)
    if value.get("message_id") is not None:
        projected["message_id"] = _safe_string(value.get("message_id"), 256)
    raw_length = value.get("text_length")
    if isinstance(raw_length, (int, float)) and not isinstance(raw_length, bool):
        projected["text_length"] = max(0, int(raw_length))
    elif isinstance(value.get("text"), str):
        projected["text_length"] = len(value["text"])
    if value.get("error") is not None:
        projected["error"] = safe_error_summary(str(value.get("error")))
    if value.get("class") is not None:
        projected["class"] = _safe_string(value.get("class"), 128)
    return projected


def sanitize_http_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = parsed.port
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return None


def sanitize_agent_run_value(
    value: Any,
    *,
    max_string: int | None = None,
    max_items: int | None = None,
    max_depth: int = 12,
    _depth: int = 0,
) -> Any:
    """Recursively remove secret-shaped keys and redact values in safe fields."""
    if _depth >= max_depth:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        redacted = redact_sensitive_assignments(value)
        return redacted if max_string is None else redacted[:max_string]
    if isinstance(value, list):
        items = value if max_items is None else value[:max_items]
        return [
            sanitize_agent_run_value(
                item,
                max_string=max_string,
                max_items=max_items,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
            for item in items
        ]
    if isinstance(value, dict):
        items = list(value.items())
        if max_items is not None:
            items = items[:max_items]
        result: dict[str, Any] = {}
        for raw_key, item in items:
            key = str(raw_key)[:128]
            if _sensitive_key(key):
                continue
            result[key] = sanitize_agent_run_value(
                item,
                max_string=max_string,
                max_items=max_items,
                max_depth=max_depth,
                _depth=_depth + 1,
            )
        return result
    rendered = redact_sensitive_assignments(str(value))
    return rendered if max_string is None else rendered[:max_string]


def redact_sensitive_assignments(value: str) -> str:
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", value)
    redacted = _JSON_AUTH_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"[REDACTED]{match.group('quote')}"
        ),
        redacted,
    )
    redacted = _CREDENTIAL_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
    )
    redacted = _HIGH_ENTROPY_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted
    )
    return _PROVIDER_TOKEN_RE.sub("[REDACTED]", redacted)


def _safe_string(value: Any, limit: int) -> str:
    return redact_sensitive_assignments(str(value or ""))[:limit]


def _sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _json_value(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback
