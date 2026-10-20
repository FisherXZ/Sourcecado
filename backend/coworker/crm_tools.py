"""Thin chat adapters over the same CRM writers as the local app.

Model arguments never establish actor identity or grant source access. A human
edit is identified conservatively from the current director-channel Directive
and a matching explicit command. Chat writes require that verified request;
background inference uses the shared services behind its own evidence gates.
Attention and proposal services are not present yet, so their tools return a
named unavailable result instead of a fabricated operating picture.
"""

from __future__ import annotations

import re
from typing import Any

from coworker.crm_sources import sources_visible
from coworker.crm_tasks import (
    DEFAULT_TIMEZONE, TIMEZONE_SETTING, TASK_STATES, TaskConflict, TaskNotFound,
    TaskService, validate_timezone,
)
from coworker.evidence_envelope import Directive
from coworker.people import PersonStore


def _schema(name: str, description: str, fields: dict, required: tuple = (), *, write: bool = False) -> dict:
    properties = {
        "person_id": {"type": "string"},
        "person_name": {"type": "string", "description": "Exact first or full name. Required outside a person chat unless the director gave the person id; ambiguous names require clarification."},
        **fields,
    }
    if write:
        properties.update({
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "operation_id": {"type": "string", "minLength": 8, "maxLength": 128},
            "request_quote": {"type": ["string", "null"], "description": "Optional exact quote of the explicit command from the current director message. Never quote connector output. The runtime verifies the command even when omitted."},
        })
        required = (*required, "reason", "operation_id")
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": list(required), "additionalProperties": False},
    }}


_SOURCE_REFS = {"type": "array", "items": {"type": "string"}, "maxItems": 50,
                "description": "Existing person-file source reference ids. Cannot grant access to private sources."}
_VERSION = {"type": "integer", "minimum": 1}
CRM_TOOL_SCHEMAS = [
    _schema("crm_list_attention", "Read Contacts attention in the same order and freshness as the app. Currently unavailable until #204; never infer an all-clear.", {"limit": {"type": "integer"}}),
    _schema("crm_list_tasks", "Read saved tasks in the same due order as Contacts. A person chat defaults to that person; the global list includes active sequences only. Report partial/truncated results.", {"state": {"type": "string", "enum": [*TASK_STATES, "all"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}),
    _schema("crm_create_task", "Save one local task. Does not start a sequence, send, enrich or change Calendar. Reuse operation_id on retry.", {
        "title": {"type": "string"}, "details": {"type": "string"},
        "due_date": {"type": ["string", "null"], "description": "Calendar day YYYY-MM-DD in the operator's saved timezone; omit if unspecified. Use now to resolve relative dates."},
        "source_refs": _SOURCE_REFS,
    }, ("title",), write=True),
    _schema("crm_update_task", "Edit, complete, reopen or cancel an existing task. Read its task version first. Human-edited fields require a verified current director command.", {
        "task_id": {"type": "string"}, "expected_version": _VERSION,
        "changes": {"type": "object", "properties": {"title": {"type": "string"}, "details": {"type": "string"}, "due_date": {"type": ["string", "null"]}, "state": {"type": "string", "enum": list(TASK_STATES)}}, "additionalProperties": False},
    }, ("task_id", "expected_version", "changes"), write=True),
    _schema("crm_update_description", "Save a director-requested general or detailed description through the app's slot writer. Read the slot version with board_get first. Requested text is protected. Description undo awaits #205.", {
        "slot": {"type": "string", "enum": ["general", "detailed"]},
        "text": {"type": "string"}, "release": {"type": "boolean"},
        "expected_version": {"type": "integer", "minimum": 0}, "source_refs": _SOURCE_REFS,
    }, ("slot", "expected_version"), write=True),
    _schema("crm_set_attention", "Set a manual attention flag using the shared attention service. Currently unavailable until #204.", {
        "expected_version": _VERSION, "flagged": {"type": "boolean"}, "until_date": {"type": ["string", "null"]},
    }, ("expected_version", "flagged"), write=True),
    _schema("crm_resolve_proposal", "Accept, edit or dismiss a saved CRM proposal. Currently unavailable until #205.", {
        "proposal_id": {"type": "string"}, "expected_version": _VERSION,
        "expected_target_version": {"type": "integer", "minimum": 0},
        "resolution": {"type": "string", "enum": ["accept", "accept_edited", "dismiss"]},
        "edited_value": {"type": "object"},
    }, ("proposal_id", "expected_version", "expected_target_version", "resolution"), write=True),
    _schema("crm_undo_change", "Undo a local task edit with its change id and current task version. A later edit conflicts. Task creation must be cancelled instead. Other entity undo awaits #205. Never retracts external effects.", {
        "change_id": {"type": "string"}, "expected_version": _VERSION,
    }, ("change_id", "expected_version"), write=True),
]
CRM_TOOL_NAMES = frozenset(schema["function"]["name"] for schema in CRM_TOOL_SCHEMAS)
CRM_READ_TOOLS = frozenset({"crm_list_attention", "crm_list_tasks"})
_SCHEMAS = {s["function"]["name"]: s["function"]["parameters"] for s in CRM_TOOL_SCHEMAS}


class CRMToolError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _validate(value: Any, schema: dict, path: str = "arguments") -> None:
    """Validate this adapter's small schema before interpreting any arguments."""
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    checks = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool), "null": value is None,
    }
    if kinds and not any(checks.get(kind, False) for kind in kinds):
        raise CRMToolError("invalid_arguments", f"{path} must be {' or '.join(kinds)}")
    if "enum" in schema and value not in schema["enum"]:
        raise CRMToolError("invalid_arguments", f"{path} has an unsupported value")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            raise CRMToolError("invalid_arguments", f"{path} requires {', '.join(sorted(missing))}")
        if schema.get("additionalProperties") is False and set(value) - properties.keys():
            raise CRMToolError("invalid_arguments", f"{path} contains unsupported fields; authority and access grants come from the runtime")
        for key, child in value.items():
            if key in properties:
                _validate(child, properties[key], f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) > schema.get("maxItems", 500):
            raise CRMToolError("invalid_arguments", f"{path} has too many items")
        for child in value:
            _validate(child, schema.get("items", {}), path)
    elif isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 100_000):
            raise CRMToolError("invalid_arguments", f"{path} has an invalid length")
    elif isinstance(value, int) and not isinstance(value, bool):
        if value < schema.get("minimum", value) or value > schema.get("maximum", value):
            raise CRMToolError("invalid_arguments", f"{path} is out of range")


def _human_request(name: str, args: dict, directive: Directive | None) -> bool:
    supplied_quote = args.get("request_quote")
    if not isinstance(directive, Directive):
        if supplied_quote:
            raise CRMToolError("unverified_director_request", "A quoted command requires a current director message.")
        return False
    quote = supplied_quote or directive.text
    if quote not in directive.text:
        raise CRMToolError("unverified_director_request", "The quoted command is not in the current director message. Ask the director to state the edit explicitly.")
    # Deliberately narrow. Reading a document containing a command does not
    # make the embedded command a director request. No model-supplied bool,
    # actor, source ref or rationale can opt into human protection.
    text = _normalized(directive.text)
    text = re.sub(r"^(?:please )?(?:(?:can|could|would) you )?", "", text)
    patterns = {
        "crm_create_task": r"^(?:remind me\b|(?:add|create|save|make)\b.*\b(?:task|reminder)\b)",
        "crm_update_task": r"^(?:mark|complete|finish|reopen|cancel|change|edit|update|move|reschedule)\b.*\b(?:task|reminder)\b",
        "crm_update_description": r"^(?:change|edit|update|set|clear|release)\b.*\bdescription\b",
        "crm_undo_change": r"^undo\b",
    }
    if not re.search(patterns.get(name, r"(?!)"), text):
        if not supplied_quote:
            return False
        raise CRMToolError("unverified_director_request", "Ask the director to state the task or description edit explicitly; this wording cannot establish human authority.")
    if re.search(r"\b(?:not|never|unless|if|when|after|before)\b|\bdon['’]t\b", text):
        raise CRMToolError("unverified_director_request", "Negated or conditional wording needs clarification before a CRM write. Ask for one explicit immediate edit.")
    values = args.get("changes", {}) if name == "crm_update_task" else args
    for field in ("title", "details", "text"):
        value = values.get(field)
        if value and _normalized(value) not in _normalized(quote):
            raise CRMToolError("unverified_director_request", "Human protection requires the requested text in the quoted director command. Ask for the exact wording, or keep the inferred edit assistant-owned.")
    state = values.get("state")
    state_words = {"completed": r"\b(?:done|complete|completed|finish|finished)\b", "cancelled": r"\bcancel", "open": r"\breopen"}
    if state and not re.search(state_words[state], text):
        raise CRMToolError("unverified_director_request", "The director command does not request this task state.")
    if args.get("release") and not re.search(r"\b(?:release|assistant|maintain)\b", text):
        raise CRMToolError("unverified_director_request", "The director must explicitly release description protection.")
    if name == "crm_update_description" and not args.get("release") and args.get("text") == "" and not re.search(r"\b(?:clear|erase|blank)\b", text):
        raise CRMToolError("unverified_director_request", "The director must explicitly request an empty description.")
    return True


def _person(people: PersonStore, args: dict, sid: str | None, directive: Directive | None, *, optional: bool = False) -> str | None:
    bound = people.person_for_session(sid) if sid else None
    requested = args.get("person_id")
    name = args.get("person_name")
    if bound and requested and requested != bound:
        raise CRMToolError("bound_person_mismatch", "The requested person does not match this conversation.")
    if name:
        needle = _normalized(str(name))
        if not bound and directive and needle not in _normalized(directive.text) and not (requested and requested in directive.text):
            raise CRMToolError("person_clarification_required", "The director did not identify this full name. Ask which person before changing anything.")
        matches = [p for p in people.query() if needle in {
            _normalized(str(p.get("first_name") or "")),
            _normalized(f"{p.get('first_name') or ''} {p.get('last_name') or ''}"),
        }]
        if len(matches) > 1 and not bound:
            raise CRMToolError("ambiguous_person", "Which person did you mean? Ask the director to choose before changing anything.", candidates=[{k: p.get(k) for k in ("person_id", "first_name", "last_name", "company")} for p in matches[:20]])
        matching_ids = {p["person_id"] for p in matches}
        if not matching_ids or (bound or requested) and (bound or requested) not in matching_ids:
            raise CRMToolError("person_not_found", "The person name does not match this record.")
        requested = bound or requested or matches[0]["person_id"]
    if bound:
        requested = bound
    if requested and not bound and not name and (not directive or requested not in directive.text):
        raise CRMToolError("person_context_required", "Provide the person's name or use a person-bound conversation. Do not guess a person id.")
    if not requested:
        if optional:
            return None
        raise CRMToolError("person_context_required", "Open a person conversation or provide the person's name.")
    if people.get(str(requested)) is None:
        raise TaskNotFound("unknown person")
    return str(requested)


def execute_crm_tool(name: str, arguments: dict, *, people: PersonStore, store: Any = None,
                     session_id: str | None = None, run_id: str | None = None,
                     allowed_source_ids: set[str] | None = None,
                     directive: Directive | None = None) -> tuple[bool, dict]:
    pending = {"crm_list_attention": 204, "crm_set_attention": 204, "crm_resolve_proposal": 205}
    if name in pending:
        issue = pending[name]
        return False, {"status": "unavailable", "partial": True, "code": "crm_capability_unavailable",
                       "depends_on_issue": issue, "error": f"This CRM capability is not available in this build (pending #{issue}). Saved tasks remain available through crm_list_tasks; do not report that no one needs attention."}
    try:
        args = arguments or {}
        schema = _SCHEMAS[name]
        _validate(args, schema)
        if name not in CRM_READ_TOOLS and (not isinstance(args.get("reason"), str) or not args["reason"].strip() or len(args["reason"]) > 500):
            raise CRMToolError("invalid_arguments", "A reason of 1–500 characters is required.")
        pid = _person(people, args, session_id, directive, optional=name == "crm_list_tasks")
        saved_timezone = store.get_setting(TIMEZONE_SETTING) if store else None
        try:
            timezone = validate_timezone(saved_timezone)
        except ValueError:
            timezone = DEFAULT_TIMEZONE
        tasks = TaskService(people)
        if name == "crm_list_tasks":
            state = args.get("state", "open")
            result = tasks.list(timezone=timezone, person_id=pid,
                states=TASK_STATES if state == "all" else (state,),
                active_sequences_only=pid is None, limit=args.get("limit", 200))
            visible = [task for task in result["tasks"] if sources_visible(people, task["person_id"], task["source_refs"], allowed_source_ids)]
            partial = result["truncated"] or len(visible) != len(result["tasks"])
            return True, {**result, "tasks": visible, "partial": partial,
                          "status": "partial" if partial else "complete"}
        human = _human_request(name, args, directive)
        if not human:
            raise CRMToolError("unverified_director_request", "Chat CRM writes require an explicit current director command. Ask for wording such as 'Remind me to send examples', 'Mark that task done', or 'Change the description to: …'. Nothing was saved.")
        identity = dict(actor="assistant", human_requested=human, session_id=session_id,
                        run_id=run_id, allowed_source_ids=allowed_source_ids,
                        operation_id=args.get("operation_id"))
        if name == "crm_create_task":
            result = tasks.create(pid, title=args.get("title"), details=args.get("details"),
                due_date=args.get("due_date"), source_refs=args.get("source_refs"),
                timezone=timezone, reason=args["reason"], **identity)
        elif name == "crm_update_task":
            result = tasks.update(pid, str(args.get("task_id") or ""),
                changes=args.get("changes"), expected_version=args.get("expected_version"),
                timezone=timezone, reason=args["reason"], **identity)
        elif name == "crm_update_description":
            result = people.patch_description(pid, str(args.get("slot") or ""),
                expected_version=args.get("expected_version"), text=args.get("text"),
                release=args.get("release", False), source_refs=args.get("source_refs"),
                rationale_summary=args["reason"], **identity)
        elif name == "crm_undo_change":
            change_id = str(args.get("change_id") or "")
            if not change_id.startswith("chg_"):
                raise CRMToolError("crm_capability_unavailable", "Description and attention undo await #205. Task creation can be cancelled with crm_update_task.", depends_on_issue=205, partial=True)
            version = args.get("expected_version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise CRMToolError("invalid_arguments", "expected_version must be a positive task version")
            result = tasks.undo(pid, change_id, expected_version=version, reason=args["reason"], **identity)
        else:
            raise CRMToolError("unknown_crm_tool", "Unknown CRM operation")
        return True, {**result, "person_id": pid, "board_changed": bool(result.get("change")), "status": "complete"}
    except CRMToolError as exc:
        return False, {"status": "failed", "code": exc.code, "error": str(exc), **exc.details}
    except TaskConflict as exc:
        # A protected or stale record can itself contain private sourced text.
        # Do not include it until it has passed the same read filter.
        current = exc.current
        visible = current and sources_visible(people, current["person_id"], current["source_refs"], allowed_source_ids)
        return False, {"status": "conflict", "code": exc.code, "error": str(exc), **({"task": current} if visible else {})}
    except TaskNotFound as exc:
        return False, {"status": "failed", "code": "not_found", "error": str(exc)}
    except (ValueError, TypeError) as exc:
        return False, {"status": "failed", "code": "invalid_arguments", "error": str(exc)}
