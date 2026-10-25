"""Typed assistant adapters into the exact services used by the local UI."""
from coworker.crm_service import CrmService
from coworker.crm_tasks import DEFAULT_TIMEZONE, TIMEZONE_SETTING, TaskError, validate_timezone


def schema(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}}


TEXT = {"type": "string"}
VERSION = {"type": "integer", "minimum": 0}
PERSON = {"person_id": TEXT}
WRITE = {**PERSON, "operation_id": TEXT, "reason": TEXT}
CRM_TOOL_SCHEMAS = [
    schema("crm_list_attention", "Read saved attention, next actions and freshness; never calls Gmail or the model.", {}),
    schema("crm_list_tasks", "Read the shared saved tasks. Supply person_id to read one person.", PERSON),
    schema("crm_create_task", "Create a task explicitly requested by the director. Never sends or starts outreach.",
        {**WRITE, "title": TEXT, "details": TEXT, "due_date": {"type": ["string", "null"]}}, ("person_id", "title", "operation_id", "reason")),
    schema("crm_update_task", "Apply an explicitly requested task edit. Reuse operation_id when retrying. Read the task version first.",
        {**WRITE, "task_id": TEXT, "expected_version": VERSION, "changes": {"type": "object", "properties": {
            "title": TEXT, "details": TEXT, "due_date": {"type": ["string", "null"]}, "state": {"type": "string", "enum": ["open", "completed", "cancelled"]}}, "additionalProperties": False}},
        ("person_id", "task_id", "expected_version", "changes", "operation_id", "reason")),
    schema("crm_update_description", "Save a description explicitly requested by the director; this protects their wording. Passive email maintenance uses separate evidence gates.",
        {**WRITE, "slot": {"type": "string", "enum": ["general", "detailed"]}, "text": TEXT, "expected_version": VERSION},
        ("person_id", "slot", "text", "expected_version", "operation_id", "reason")),
    schema("crm_set_attention", "Set or clear the director's manual attention flag, independently of tasks and status.",
        {**WRITE, "flag_reason": {"type": ["string", "null"]}, "until": {"type": ["string", "null"]}, "expected_version": VERSION},
        ("person_id", "flag_reason", "reason", "expected_version", "operation_id")),
    schema("crm_resolve_proposal", "Accept or dismiss a suggestion only on the director's instruction. Use board_get to inspect proposals first.",
        {**WRITE, "proposal_id": TEXT, "decision": {"type": "string", "enum": ["accepted", "dismissed"]}},
        ("person_id", "proposal_id", "decision", "operation_id", "reason")),
    schema("crm_undo_change", "Undo a local CRM change explicitly requested by the director. Never retracts sent email.",
        {**WRITE, "change_id": TEXT}, ("person_id", "change_id", "operation_id", "reason")),
]
CRM_TOOL_NAMES = frozenset(s["function"]["name"] for s in CRM_TOOL_SCHEMAS)


def execute_crm_tool(name, args, *, people, store=None, actor="assistant", session_id=None, run_id=None):
    crm = CrmService(people)
    try:
        bound = people.person_for_session(session_id) if session_id else None
        pid = args.get("person_id") or bound
        if bound and pid != bound:
            raise TaskError("person does not match this conversation")
        try:
            timezone = validate_timezone(store.get_setting(TIMEZONE_SETTING)) if store else DEFAULT_TIMEZONE
        except TaskError:
            timezone = DEFAULT_TIMEZONE
        if name == "crm_list_attention":
            result = crm.contacts(timezone=timezone, include_saved=True)
            if bound:
                result["contacts"] = [p for p in result["contacts"] if p["person_id"] == bound]
            return True, result
        if name == "crm_list_tasks":
            return True, crm.tasks.list(timezone=timezone, person_id=pid)
        if not pid:
            raise TaskError("An unambiguous person_id is required; ask the director if names are ambiguous")
        if not isinstance(args.get("reason"), str) or not args["reason"].strip():
            raise TaskError("A reason is required")
        audit = dict(actor=actor, session_id=session_id, run_id=run_id, operation_id=args.get("operation_id"))
        if name == "crm_create_task":
            result = crm.tasks.create(pid, title=args.get("title"), details=args.get("details"), due_date=args.get("due_date"), timezone=timezone, reason=args["reason"], human_directed=True, **audit)
        elif name == "crm_update_task":
            result = crm.tasks.update(pid, args.get("task_id", ""), changes=args.get("changes", {}), expected_version=args.get("expected_version"), timezone=timezone, reason=args["reason"], human_directed=True, **audit)
        elif name == "crm_update_description":
            result = crm.save_description(pid, args.get("slot"), text=args.get("text"), expected_version=args.get("expected_version"), **audit)
        elif name == "crm_set_attention":
            result = crm.set_attention(pid, reason=args.get("flag_reason"), until=args.get("until"), expected_version=args.get("expected_version"), **audit)
        elif name == "crm_resolve_proposal":
            result = crm.resolve(pid, args.get("proposal_id"), decision=args.get("decision"), timezone=timezone, **audit)
        else:
            result = crm.undo(pid, args.get("change_id", ""), **audit)
        change = result.get("change")
        if change:
            result["artifacts"] = [{"id": change["change_id"], "artifact_type": "crm_change", "title": "CRM change receipt"}]
        return True, result
    except (TaskError, ValueError, TypeError) as exc:
        return False, {"status": "failed", "error": str(exc), "code": getattr(exc, "code", "invalid_crm_operation")}
