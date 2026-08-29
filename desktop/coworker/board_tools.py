"""Agent-facing tools for the person-file Board."""

from __future__ import annotations

from typing import Any

from coworker.brief import CHAT_HANDOFF_FIELD_CHARS, brief_payload, project
from coworker.people import ATTACHMENT_TYPES, PersonStore

_ATTACHMENT_SCHEMA = {"type": "string", "enum": sorted(ATTACHMENT_TYPES)}

BOARD_GET_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_get",
        "description": (
            "Read one complete living brief and four-field successor handoff. "
            "In a person-bound chat, Sourcecado resolves the bound person; "
            "person_id is only needed outside one. This tool never saves a handoff."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}

BOARD_QUERY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_query",
        "description": "List person files on the Board by sequence, company tag, or target.",
        "parameters": {
            "type": "object",
            "properties": {
                "sequence": {"type": "string", "enum": ["open", "in_conversation", "done"]},
                "company": {"type": "string"},
                "target": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}

BOARD_UPSERT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_upsert",
        "description": (
            "Attach an artifact, knowledge gap, or source ref to an existing person file. "
            "Does not create people. Use people_keep for Apollo rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string"},
                "record_type": _ATTACHMENT_SCHEMA,
                "fields": {"type": "object"},
                "idempotency_key": {"type": "string"},
                "rationale_summary": {"type": "string"},
            },
            "required": [
                "person_id",
                "record_type",
                "fields",
                "idempotency_key",
                "rationale_summary",
            ],
            "additionalProperties": False,
        },
    },
}

BOARD_MUTATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_mutate",
        "description": (
            "Version-checked person-file write: patch, transition, capture_outcome, or revert."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["patch", "transition", "capture_outcome", "revert"],
                },
                "person_id": {"type": "string"},
                "expected_version": {"type": "integer"},
                "rationale_summary": {"type": "string"},
                "fields": {"type": "object"},
                "to_state": {"type": "string", "enum": ["open", "in_conversation", "done"]},
                "outcome": {"type": "string"},
                "to_version": {"type": "integer"},
            },
            "required": ["action", "person_id", "expected_version", "rationale_summary"],
            "additionalProperties": False,
        },
    },
}

BOARD_DELETE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "board_delete",
        "description": "Delete a person file after explicit human approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "person_id": {"type": "string"},
                "expected_version": {"type": "integer"},
                "rationale_summary": {"type": "string"},
            },
            "required": ["person_id", "expected_version", "rationale_summary"],
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


def _partial_board_read(code: str, message: str) -> tuple[bool, dict[str, Any]]:
    return False, {
        "status": "partial",
        "partial": True,
        "code": code,
        "error": message,
        "partial_sources": ["board"],
        "unavailable_sources": [{"source": "board", "code": code}],
    }


def execute_board_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    people: PersonStore,
    actor: str,
    session_id: str | None,
    run_id: str | None,
    allowed_source_ids: set[str] | None,
) -> tuple[bool, dict[str, Any]]:
    args = arguments or {}
    actor = "assistant" if actor == "assistant" else "director"
    try:
        if name == "board_get":
            requested_person_id = str(args.get("person_id") or "").strip() or None
            try:
                bound_person_id = (
                    people.person_for_session(session_id) if session_id else None
                )
            except Exception:
                return _partial_board_read(
                    "board_binding_failed",
                    "The bound person for this Board read is unavailable.",
                )
            if (
                bound_person_id is not None
                and requested_person_id is not None
                and requested_person_id != bound_person_id
            ):
                return _partial_board_read(
                    "bound_person_mismatch",
                    "The requested person does not match this conversation.",
                )
            person_id = bound_person_id or requested_person_id
            if person_id is None:
                return _partial_board_read(
                    "person_context_required",
                    "Open a person-bound conversation or provide a person id.",
                )
            try:
                person = people.get(
                    person_id,
                    expand_sources=True,
                    allowed_source_ids=allowed_source_ids,
                )
                if person is None:
                    return _partial_board_read(
                        "board_person_unavailable",
                        "The Board person file is unavailable.",
                    )
                brief = brief_payload(
                    project(
                        person,
                        people.timeline(person_id),
                        session_id=session_id,
                    ),
                    handoff_field_chars=CHAT_HANDOFF_FIELD_CHARS,
                )
                person_receipt = {
                    key: value
                    for key, value in person.items()
                    if key
                    not in {
                        "attachments",
                        "sources",
                        "artifacts",
                        "knowledge_gaps",
                        "restricted_source_count",
                        "handoff_who",
                        "handoff_wanted",
                        "handoff_happened",
                        "handoff_they_want",
                    }
                }
            except Exception:
                return _partial_board_read(
                    "board_read_failed",
                    "The Board person-file read is unavailable.",
                )
            return True, {
                "status": "complete",
                "partial": False,
                "person_id": person_id,
                "person": person_receipt,
                "brief": brief,
            }
        if name == "board_query":
            records = people.query(
                sequence=str(args.get("sequence") or "") or None,
                company=str(args.get("company") or "") or None,
                target=str(args.get("target") or "") or None,
            )
            return True, {"people": records, "count": len(records)}
        identity = {
            "actor": actor,
            "session_id": session_id,
            "run_id": run_id,
            "rationale_summary": str(args.get("rationale_summary") or ""),
        }
        person_id = str(args.get("person_id") or "")
        if name == "board_upsert":
            record = people.upsert_attachment(
                person_id,
                record_type=str(args.get("record_type") or ""),
                fields=args.get("fields") if isinstance(args.get("fields"), dict) else {},
                idempotency_key=str(args.get("idempotency_key") or ""),
                allowed_source_ids=allowed_source_ids,
                **identity,
            )
            return True, {"record": record, "board_changed": True}
        if name == "board_delete":
            result = people.delete(
                person_id,
                expected_version=int(args.get("expected_version") or 0),
                **identity,
            )
            return True, {**result, "board_changed": True}
        if name != "board_mutate":
            return False, {"status": "failed", "error": f"unknown board tool {name}"}
        action = str(args.get("action") or "")
        expected_version = int(args.get("expected_version") or 0)
        if action == "patch":
            fields = (
                args.get("fields") if isinstance(args.get("fields"), dict) else {}
            )
            person = people.patch(
                person_id,
                fields=fields,
                expected_version=expected_version,
                **identity,
            )
        elif action == "transition":
            person = people.set_sequence(
                person_id,
                str(args.get("to_state") or ""),
                expected_version=expected_version,
                **identity,
            )
        elif action == "capture_outcome":
            person = people.capture_outcome(
                person_id,
                outcome=str(args.get("outcome") or ""),
                expected_version=expected_version,
                **identity,
            )
        elif action == "revert":
            person = people.revert(
                person_id,
                to_version=int(args.get("to_version") or 0),
                expected_version=expected_version,
                **identity,
            )
        else:
            raise ValueError(f"unknown board mutation {action}")
        return True, {"person": person, "board_changed": True}
    except (TypeError, ValueError) as exc:
        return False, {"status": "failed", "error": str(exc)}
