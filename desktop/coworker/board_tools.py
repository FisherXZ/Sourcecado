"""Agent-facing tools for the durable Board and sourcing index."""

from __future__ import annotations

from typing import Any

from coworker.sourcing_index import RECORD_TYPES, SourcingIndex

_RECORD_TYPE_SCHEMA = {"type": "string", "enum": sorted(RECORD_TYPES)}

BOARD_GET_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_get",
        "description": "Read one exact Board or sourcing-index record by id.",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "expand_sources": {"type": "boolean"},
            },
            "required": ["record_id"],
            "additionalProperties": False,
        },
    },
}

BOARD_QUERY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_query",
        "description": "Filter Board and sourcing-index records by type and exact field values.",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": _RECORD_TYPE_SCHEMA,
                "filters": {"type": "object"},
                "expand_sources": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
}

BOARD_UPSERT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_upsert",
        "description": "Idempotently create a typed Board or sourcing-index record with provenance.",
        "parameters": {
            "type": "object",
            "properties": {
                "record_type": _RECORD_TYPE_SCHEMA,
                "fields": {"type": "object"},
                "idempotency_key": {"type": "string"},
                "rationale_summary": {"type": "string"},
                "source_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["record_type", "fields", "idempotency_key", "rationale_summary"],
            "additionalProperties": False,
        },
    },
}

BOARD_MUTATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_mutate",
        "description": (
            "Apply one reversible, version-checked Board operation: patch, link, unlink, "
            "transition, capture_outcome, complete_action, or revert."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "patch",
                        "link",
                        "unlink",
                        "transition",
                        "capture_outcome",
                        "complete_action",
                        "revert",
                    ],
                },
                "record_id": {"type": "string"},
                "target_id": {"type": "string"},
                "relationship": {"type": "string"},
                "fields": {"type": "object"},
                "expected_version": {"type": "integer"},
                "to_stage": {"type": "string"},
                "evidence_record_ids": {"type": "array", "items": {"type": "string"}},
                "outcome": {"type": "string"},
                "to_version": {"type": "integer"},
                "rationale_summary": {"type": "string"},
                "source_refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["action", "record_id", "rationale_summary"],
            "additionalProperties": False,
        },
    },
}

BOARD_DELETE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_delete",
        "description": "Permanently delete one Board record after explicit human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "expected_version": {"type": "integer"},
                "rationale_summary": {"type": "string"},
            },
            "required": ["record_id", "expected_version", "rationale_summary"],
            "additionalProperties": False,
        },
    },
}

BOARD_TOOL_SCHEMAS = [
    BOARD_GET_SCHEMA,
    BOARD_QUERY_SCHEMA,
    BOARD_UPSERT_SCHEMA,
    BOARD_MUTATE_SCHEMA,
    BOARD_DELETE_SCHEMA,
]
BOARD_TOOL_NAMES = frozenset(schema["function"]["name"] for schema in BOARD_TOOL_SCHEMAS)


def _text_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def execute_board_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    index: SourcingIndex,
    actor: str,
    session_id: str | None,
    run_id: str | None,
    allowed_source_ids: set[str] | None,
) -> tuple[bool, dict[str, Any]]:
    args = arguments or {}
    try:
        if name == "board_get":
            record = index.get(
                str(args.get("record_id") or ""),
                expand_sources=bool(args.get("expand_sources")),
                allowed_source_ids=allowed_source_ids,
            )
            return (True, {"record": record}) if record is not None else (
                False,
                {"status": "failed", "error": "record not found or not granted"},
            )
        if name == "board_query":
            records = index.query(
                record_type=str(args.get("record_type") or "") or None,
                filters=args.get("filters") if isinstance(args.get("filters"), dict) else {},
                expand_sources=bool(args.get("expand_sources")),
                allowed_source_ids=allowed_source_ids,
            )
            return True, {"records": records, "count": len(records)}
        identity = {
            "actor": actor,
            "session_id": session_id,
            "run_id": run_id,
            "rationale_summary": str(args.get("rationale_summary") or ""),
        }
        source_refs = _text_list(args.get("source_refs"))
        if name == "board_upsert":
            record = index.upsert(
                record_type=str(args.get("record_type") or ""),
                fields=args.get("fields") if isinstance(args.get("fields"), dict) else {},
                idempotency_key=str(args.get("idempotency_key") or ""),
                source_refs=source_refs,
                **identity,
            )
            return True, {"record": record, "board_changed": True}
        if name == "board_delete":
            result = index.delete(
                str(args.get("record_id") or ""),
                expected_version=int(args.get("expected_version") or 0),
                **identity,
            )
            return True, {**result, "board_changed": bool(result.get("deleted"))}
        if name != "board_mutate":
            return False, {"status": "failed", "error": f"unknown board tool {name}"}
        action = str(args.get("action") or "")
        record_id = str(args.get("record_id") or "")
        if action == "patch":
            result = index.patch(
                record_id,
                fields=args.get("fields") if isinstance(args.get("fields"), dict) else {},
                expected_version=int(args.get("expected_version") or 0),
                source_refs=source_refs,
                **identity,
            )
            return True, {"record": result, "board_changed": True}
        if action in {"link", "unlink"}:
            method = index.link if action == "link" else index.unlink
            result = method(
                record_id,
                str(args.get("target_id") or ""),
                relationship=str(args.get("relationship") or ""),
                source_refs=source_refs,
                **identity,
            )
            return True, {action: result, "board_changed": True}
        if action == "transition":
            result = index.transition(
                record_id,
                to_stage=str(args.get("to_stage") or ""),
                evidence_record_ids=_text_list(args.get("evidence_record_ids")),
                expected_version=int(args.get("expected_version") or 0),
                source_refs=source_refs,
                **identity,
            )
        elif action == "capture_outcome":
            result = index.capture_outcome(
                record_id,
                outcome=str(args.get("outcome") or ""),
                expected_version=int(args.get("expected_version") or 0),
                source_refs=source_refs,
                **identity,
            )
        elif action == "complete_action":
            result = index.complete_action(
                record_id,
                expected_version=int(args.get("expected_version") or 0),
                source_refs=source_refs,
                **identity,
            )
        elif action == "revert":
            result = index.revert(
                record_id,
                to_version=int(args.get("to_version") or 0),
                expected_version=int(args.get("expected_version") or 0),
                **identity,
            )
        else:
            raise ValueError(f"unknown board mutation {action}")
        return True, {"record": result, "board_changed": True}
    except (TypeError, ValueError) as exc:
        return False, {"status": "failed", "error": str(exc)}
