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
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from coworker.crm_sources import sources_visible
from coworker.crm_tasks import (
    DEFAULT_TIMEZONE, TIMEZONE_SETTING, TASK_STATES, TASK_TRANSITIONS, TaskConflict, TaskNotFound,
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
    _schema("crm_update_task", "Edit one named task field, complete, reopen or cancel an existing task. Read its task version first. Lifecycle commands change state only. For text/date edits use 'Change the task title/details/due date to: VALUE'; date edits require an explicit YYYY-MM-DD value. Compound edits require separate commands.", {
        "task_id": {"type": "string"}, "expected_version": _VERSION,
        "changes": {"type": "object", "properties": {"title": {"type": "string"}, "details": {"type": "string"}, "due_date": {"type": ["string", "null"]}, "state": {"type": "string", "enum": list(TASK_STATES)}}, "additionalProperties": False},
    }, ("task_id", "expected_version", "changes"), write=True),
    _schema("crm_update_description", "Save a director-requested description through the app's slot writer. An unspecified description means general; detailed requires the director to say detailed description. Read the slot version with board_get first. Releasing protection is a separate command without text. Description undo awaits #205.", {
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


@dataclass
class CRMMutationBudget:
    """One director request permits one successful mutation, plus exact replay."""

    consumed_request: str | None = None

    def check(self, request: str) -> bool:
        if self.consumed_request is None:
            return False
        if request != self.consumed_request:
            raise CRMToolError("crm_authority_consumed", "This director request already saved a CRM operation. Ask for another explicit command before changing another record or using another operation id.")
        return True

    def consume(self, request: str) -> None:
        self.consumed_request = request


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _replacement(value: str) -> str:
    """Ignore one sentence terminator while keeping literal/empty values distinct."""
    text = _normalized(value)
    return text[:-1] if text and text[-1] in ".!?" and any(char.isalnum() for char in text[:-1]) else text


def crm_outcome_references(result: dict[str, Any]) -> dict[str, Any]:
    """Runtime-authored pointers only; source and entity content stay in people.db."""
    change = result.get("change")
    if not isinstance(change, dict) or not change.get("change_id"):
        return {}
    entity = result.get("task") or result.get("description") or {}
    return {
        "outcome_ref": change["change_id"], "person_id": result.get("person_id"),
        "artifact_refs": [{"id": change["change_id"], "artifact_type": "crm_change", "title": "CRM change"}],
        "source_refs": [{"id": ref, "provider": "sourcecado", "title": "CRM source reference"}
                        for ref in entity.get("source_refs", [])[:50]],
    }


def _explicitly_undated(text: str) -> bool:
    return re.match(r"^(?:add|create|save|make)\s+(?:an?\s+)?undated\s+task\b", text) is not None


def _reminder_date(text: str, directive: Directive, timezone: str) -> str | None:
    if _explicitly_undated(text):
        # Explicitly undated work may contain literal calendar/clock words.
        # A model-supplied date still conflicts with the returned None below.
        return None
    moment = datetime.fromisoformat(directive.at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise CRMToolError("unverified_director_request", "The director request needs a timezone-aware timestamp before resolving a date.")
    today = moment.astimezone(ZoneInfo(timezone)).date()
    weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    token = r"(?:today|tomorrow|" + "|".join(weekdays) + r"|\d{4}-\d{2}-\d{2})"
    prefix = re.match(rf"^remind me\s+({token})\s+to\b", text)
    # Possessives ("on Monday's call") describe context rather than a deadline.
    calendar_units = r"seconds?|minutes?|hours?|days?|weeks?|weekends?|fortnights?|months?|quarters?|seasons?|years?"
    months = r"january|february|march|april|may|june|july|august|september|october|november|december"
    numeric_or_time = r"\d[\w:/-]*|noon|midnight"
    date_hint = rf"(?:{token}|{calendar_units}|{months}|{numeric_or_time})"
    modifiers = r"(?:(?:the|a|an|next|this|coming|following|around|early|late|end|of|in)\s+)*"
    # Topic verbs identify the relationship, rather than requiring a closed
    # list of things a director may work on. Known event forms remain timing.
    topic_verb = r"\b(?:follow up|work|focus|collaborate|check in|read up|comment|report)\s+$"
    event_hint = (r"\b(?:halloween|christmas|thanksgiving|easter|hanukkah|ramadan|holiday|birthday|anniversary|"
                  r"deadline|launch|conference|retreat|event|new year['’]?s?|independence day|labor day|memorial day)\b")
    tokens = [prefix[1]] if prefix else []
    handled = [prefix.span(1)] if prefix else []
    for suffix in re.finditer(r"\b(by|on|due)\b\s*", text):
        # By/due establish a deadline boundary even when its value is unknown.
        # Only named delivery methods exempt a by-clause. On also introduces
        # ordinary topics, so its calendar/numeric/time boundary is narrower.
        clause = re.split(r"[.!?;]", text[suffix.end():], maxsplit=1)[0].strip()
        if suffix[1] == "by" and clause in {"email", "e-mail", "phone", "text", "mail", "post", "hand"}:
            continue
        if suffix[1] == "on":
            if re.match(rf"^{token}['’]s\b", clause):
                continue
            if not re.match(rf"^{modifiers}{date_hint}(?![\w'’])", clause):
                if not re.search(event_hint, clause) and (re.search(topic_verb, text[:suffix.start()])
                        or re.match(r"^(?:the|our|your|their)\s+(?:project|work|proposal|research|roadmap|product|company|plan)\b", clause)):
                    continue
                raise CRMToolError("unverified_director_request", "The on-clause does not establish a supported date or topic. Ask for one explicit calendar deadline.")
        if not re.fullmatch(token, clause):
            raise CRMToolError("unverified_director_request", "State one unambiguous deadline: today, tomorrow, a weekday, or YYYY-MM-DD. Unsupported or alternative deadline wording needs clarification.")
        tokens.append(clause)
        handled.append((suffix.end(), suffix.end() + len(clause)))
    if not prefix and re.match(r"^remind me\s+(?!to\b)", text):
        raise CRMToolError("unverified_director_request", "State one unambiguous date: today, tomorrow, a weekday, or YYYY-MM-DD.")
    # A calendar word outside a supported clause still carries date intent.
    # Asking is safer than silently turning that request into undated work.
    dayparts = r"morning|afternoon|evening|night|dawn|dusk|midday|sunrise|sunset"
    temporal_words = rf"(?:{token}|{months}|{calendar_units}|{dayparts}|tonight|yesterday|someday|sometime|soon|later|now)"
    for mention in re.finditer(rf"\b{temporal_words}\b(?!['’]s\b)", text):
        before, after = text[:mention.start()], text[mention.end():]
        if mention[0] in {"may", "march"}:
            # Modal may and the verb march are ordinary prose. Calendar
            # prepositions/modifiers and adjacent day numbers disambiguate.
            calendar_context = re.search(
                r"\b(?:on|by|due|in|within|during|this|next|last|coming|following|every|around|early|late|of)\s+$|"
                r"\b\d{1,2}(?:st|nd|rd|th)?\s+$", before)
            prose_context = re.match(r"\s+[a-z]+\b", after) or (mention[0] == "march" and re.search(r"\bto\s+$", before))
            if not calendar_context and prose_context:
                continue
        if (mention[0] == "second" and re.search(r"\b(?:the|a|our|your|their|his|her)\s+$", before)
                and re.match(r"\s+(?!(?:from|later|ago)\b)[a-z]+\b", after)):
            continue  # An ordinal artifact, such as the second draft.
        if mention[0] == "minutes" and re.search(r"\b(?:meeting|call)\s+$", before):
            continue  # A record of the conversation, rather than a duration.
        if not any(start <= mention.start() < end for start, end in handled):
            raise CRMToolError("unverified_director_request", "The timing is outside the supported deadline grammar. Ask for an explicit by/on/due date, 'Remind me DATE to …', or 'Add an undated task: TITLE' when the time words are literal task text.")
    clock_words = r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|half|quarter|morning|afternoon|evening|night|dawn|dusk|midday|sunrise|sunset"
    if (re.search(rf"\b(?:this|next|last|coming|following|every|in|within|at)\s+[^.!?;\n]*\b(?:{calendar_units}|{dayparts})\b", text)
            or re.search(rf"\bat\s+(?:(?:a|the)\s+)?(?:{numeric_or_time}|{clock_words})\b", text)
            or re.search(rf"\b(?:{months})\d", text)):
        raise CRMToolError("unverified_director_request", "Relative periods and clock times need clarification. State one supported calendar deadline.")
    dates = set()
    for value in tokens:
        if value == "today":
            resolved = today
        elif value == "tomorrow":
            resolved = today + timedelta(days=1)
        elif value in weekdays:
            resolved = today + timedelta(days=(weekdays.index(value) - today.weekday()) % 7)
        else:
            resolved = date.fromisoformat(value)
        dates.add(resolved.isoformat())
    if len(dates) > 1:
        raise CRMToolError("unverified_director_request", "The command contains conflicting dates. Ask for one intended due date.")
    return next(iter(dates), None)


def _saved_task_operation(people: PersonStore, person_id: str, operation_id: str) -> dict | None:
    """Only target/date metadata; the writer still verifies fingerprint and access."""
    with people._lock:
        row = people._conn.execute(
            """SELECT c.entity_id, c.action, c.after_json, c.compensates_change_id FROM crm_operations o
               JOIN crm_changes c ON c.change_id = o.change_id
               WHERE o.operation_id = ? AND c.person_id = ? AND c.entity_type = 'task'""",
            (operation_id, person_id),
        ).fetchone()
    return ({"task_id": row["entity_id"], "action": row["action"],
             "compensates_change_id": row["compensates_change_id"],
             "due_date": json.loads(row["after_json"]).get("due_date")} if row else None)


def _verify_task_target(name: str, args: dict, people: PersonStore, person_id: str,
                        directive: Directive, allowed_sources: set[str] | None,
                        saved_operation: dict | None) -> None:
    if name not in {"crm_update_task", "crm_undo_change"}:
        return
    # Receipt recovery must survive the first write changing state/version.
    # This only bypasses selection; the service still checks the entire saved
    # request fingerprint and current source permissions before returning it.
    if saved_operation:
        if name == "crm_update_task" and args["task_id"] == saved_operation["task_id"]:
            return
        if name == "crm_undo_change" and args["change_id"] == saved_operation["compensates_change_id"]:
            return
    text = _normalized(directive.text)
    if name == "crm_update_task" and args["changes"].get("state") is None:
        # Replacement text is data, including identifiers. It cannot supply
        # the task selection missing from the command itself.
        text = re.split(r"\bto:?\s+", text, maxsplit=1)[0]
    if name == "crm_update_task" and args["task_id"] in text:
        named_ids = set(re.findall(r"\btask_[0-9a-f]{32}\b", text))
        if named_ids != {args["task_id"]}:
            raise CRMToolError("ambiguous_task", "Name one task id matching the requested task. Conflicting task ids require clarification.")
        task = TaskService(people).get(person_id, args["task_id"])
        if sources_visible(people, person_id, task["source_refs"], allowed_sources):
            return
    if name == "crm_update_task":
        listing = TaskService(people).list(timezone=DEFAULT_TIMEZONE, person_id=person_id, states=TASK_STATES, limit=500)
        if listing["truncated"]:
            raise CRMToolError("ambiguous_task", "The task list is incomplete. Ask for an explicit task id.")
        state = args["changes"].get("state")
        eligible_records = [task for task in listing["tasks"]
                            if state is None or task["state"] == state or (task["state"], state) in TASK_TRANSITIONS]
        candidates = [task for task in eligible_records
                      if sources_visible(people, person_id, task["source_refs"], allowed_sources)]
        field, selected, code = "task_id", args["task_id"], "ambiguous_task"
    else:
        if not str(args["change_id"]).startswith("chg_"):
            return  # The adapter reports the pending non-task undo capability.
        with people._lock:
            rows = people._conn.execute(
                """SELECT c.change_id, c.entity_id AS task_id, c.entity_version AS version,
                          c.action, t.title, t.source_refs FROM crm_changes c
                   JOIN person_tasks t ON t.task_id = c.entity_id AND t.person_id = c.person_id
                   WHERE c.person_id = ? AND c.entity_type = 'task'
                     AND c.before_json IS NOT NULL AND c.entity_version = t.version""", (person_id,),
            ).fetchall()
        eligible_records = [dict(row) for row in rows
                            if not re.search(r"\b(?:completion|complete|done)\b", text) or row["action"] == "complete"]
        candidates = [row for row in eligible_records
                      if sources_visible(people, person_id, json.loads(row["source_refs"]), allowed_sources)]
        field, selected, code = "change_id", args["change_id"], "ambiguous_crm_change"
    explicit = [row for row in candidates if row[field] in text or row["task_id"] in text]
    named_task_ids = set(re.findall(r"\btask_[0-9a-f]{32}\b", text))
    selected_task_ids = {row["task_id"] for row in candidates if row[field] == selected}
    if named_task_ids and (len(named_task_ids) != 1 or named_task_ids != selected_task_ids):
        raise CRMToolError(code, "The explicit task id does not identify this permitted change. Do not fall back to another task.")
    title = ""
    if not explicit:
        # Match the complete name after "task", excluding the lifecycle verb.
        # Generic words like "that task done" do not name a task titled Done.
        named = re.search(r"\btask\s+(.+)$", text)
        title = named[1].rstrip(".!?") if named else ""
        if name == "crm_undo_change":
            title = re.sub(r"\s+(?:completion|change|edit|update|cancellation|reopening)$", "", title)
            if title in {"completion", "change", "edit", "update", "cancellation", "reopening"}:
                title = ""
        elif args["changes"].get("state"):
            title = re.sub(r"\s+(?:done|complete|completed|cancelled|canceled|open|reopened)$", "", title)
            if title in {"done", "complete", "completed", "cancelled", "canceled", "open", "reopened"}:
                title = ""
        else:
            title = ""  # Field-edit wording names a field, not an old task title.
        explicit = [row for row in candidates if title and _normalized(row["title"]) == title]
        if title and any(_normalized(row["title"]) == title for row in eligible_records if row not in candidates):
            raise CRMToolError(code, "The title does not identify a unique permitted target. Ask for an explicit permitted task or change id.")
    if not explicit and len(candidates) != len(eligible_records):
        raise CRMToolError(code, "Some eligible work is unavailable to this run. Ask for an explicit permitted task or change id; do not guess a target.")
    eligible = explicit if explicit or title else candidates
    if len(eligible) != 1 or eligible[0][field] != selected:
        raise CRMToolError(code, "Which saved task or change did you mean? Give its exact title or id; a person-bound chat alone does not identify it.",
                           candidates=[{key: row[key] for key in (field, "title", "version")} for row in eligible[:20]])


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


def _human_request(name: str, args: dict, directive: Directive | None, *, timezone: str = DEFAULT_TIMEZONE,
                   saved_operation: dict | None = None) -> bool:
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
        "crm_update_task": r"^(?:mark|complete|finish|reopen|cancel|change|edit|update|clear|move|reschedule)\b.*\b(?:task|reminder)\b",
        "crm_update_description": r"^(?:change|edit|update|set|clear|release)\b.*\bdescription\b",
        "crm_undo_change": r"^undo\b",
    }
    if not re.search(patterns.get(name, r"(?!)"), text):
        if not supplied_quote:
            return False
        raise CRMToolError("unverified_director_request", "Ask the director to state the task or description edit explicitly; this wording cannot establish human authority.")
    # Only an explicit colon after a supported replacement command creates a
    # literal-data boundary. Without it, trailing conditions stay commands.
    literal_prefix = None
    if name == "crm_update_task":
        literal_prefix = re.match(r"^(?:change|edit|update)\s+(?:(?:the|that)\s+)?task"
                                  r"(?:\s+task_[0-9a-f]{32})?\s+(?:title|details)\s+to:\s+", text)
    elif name == "crm_update_description":
        literal_prefix = re.match(r"^(?:change|edit|update|set)\s+(?:the\s+)?"
                                  r"(?:(?:general|detailed)\s+)?description\s+to:\s+", text)
    command = text[:literal_prefix.end()] if literal_prefix else text
    if re.search(r"\b(?:not|never|unless|if|when|after|before)\b|\bdon['’]t\b", command):
        raise CRMToolError("unverified_director_request", "Negated or conditional wording needs clarification before a CRM write. Ask for one explicit immediate edit.")
    if name == "crm_undo_change":
        task_command = re.match(r"^undo\s+(?:(?:the|that|last|latest)\s+)?task\b", text)
        explicit_command = re.fullmatch(r"undo\s+(?:(?:the|that)\s+)?(?:(?:task|change)\s+)?(?:task|chg)_[0-9a-f]{32}[.!?]?", text)
        if not task_command and not explicit_command:
            raise CRMToolError("unverified_director_request", "Say which task change to undo, using task wording or its exact task/change id. Description, status, attention, suggestion and external-action undo cannot select a task.")
        # Check the current request before receipt selection can bypass target
        # lookup. Replaying a task receipt cannot authorize another entity/id.
        for identifier in re.findall(r"\b(?:task|chg)_[0-9a-f]{32}\b", text):
            selected = args["change_id"] if identifier.startswith("chg_") else (saved_operation or {}).get("task_id")
            if selected is not None and identifier != selected:
                raise CRMToolError("unverified_director_request", "The task/change id must match the director's current undo command.")
        return True
    if name == "crm_update_task":
        changes = args["changes"]
        lifecycle = re.match(r"^(mark|complete|finish|reopen|cancel)\b", text)
        if lifecycle:
            # A command about completion cannot also authorize a title/date
            # copied out of the same sentence by the model.
            if set(changes) != {"state"}:
                raise CRMToolError("unverified_director_request", "A completion, reopen or cancel command permits only a task state change. Ask separately for other edits.")
            verb = lifecycle[1]
            requested_state = {"complete": "completed", "finish": "completed", "reopen": "open", "cancel": "cancelled"}.get(verb)
            if verb == "mark":
                ending = re.search(r"\b(done|complete|completed|cancelled|canceled|open|reopened)[.!]?$", text)
                requested_state = ({"done": "completed", "complete": "completed", "completed": "completed", "cancelled": "cancelled", "canceled": "cancelled", "open": "open", "reopened": "open"}.get(ending[1]) if ending else None)
            if changes.get("state") != requested_state:
                raise CRMToolError("unverified_director_request", "The requested task state does not match the director's command.")
            return True
        # Keep the supported editing grammar small and field-scoped. A
        # coincidental word in the old title is not authority to replace it.
        selector = r"(?:\s+(?P<task_id>task_[0-9a-f]{32}))?"
        clear = re.fullmatch(r"clear\s+(?:(?:the|that)\s+)?task" + selector + r"\s+(?P<field>details|due date)[.!]?", text)
        if clear:
            if clear["task_id"] and clear["task_id"] != args["task_id"]:
                raise CRMToolError("unverified_director_request", "The task id must match the director's command.")
            field, empty = ("details", "") if clear["field"] == "details" else ("due_date", None)
            if changes != {field: empty}:
                raise CRMToolError("unverified_director_request", "A clear command permits only clearing the named task field.")
            return True
        edit = re.fullmatch(r"(?:change|edit|update)\s+(?:(?:the|that)\s+)?task" + selector + r"\s+(?P<field>title|details|due date)\s+to:?\s+(?P<value>.+)", text)
        if not edit or len(changes) != 1:
            raise CRMToolError("unverified_director_request", "Ask for one field explicitly: 'Change task [task_id] title/details/due date to: VALUE'. Date edits require YYYY-MM-DD.")
        if edit["task_id"] and edit["task_id"] != args["task_id"]:
            raise CRMToolError("unverified_director_request", "The task id must match the director's command.")
        field = {"title": "title", "details": "details", "due date": "due_date"}[edit["field"]]
        if set(changes) != {field} or changes[field] is None or _replacement(changes[field]) != _replacement(edit["value"]):
            raise CRMToolError("unverified_director_request", "The changed field and complete new value must match the director's field-specific command.")
        return True
    if name == "crm_update_description":
        slot_command = re.match(r"^(change|edit|update|set|clear|release)\s+(?:the\s+)?(?:(general|detailed)\s+)?description\b", text)
        if not slot_command or args["slot"] != (slot_command[2] or "general"):
            raise CRMToolError("unverified_director_request", "The requested description slot does not match the director's command. An unspecified description means general; say 'detailed description' for that slot.")
        if args.get("release"):
            if slot_command[1] != "release" or "text" in args or args.get("source_refs"):
                raise CRMToolError("unverified_director_request", "Release description protection with a separate release command, without text or sources.")
            return True
        if slot_command[1] == "release":
            raise CRMToolError("unverified_director_request", "A release command cannot change description text.")
        if args.get("text") == "":
            if slot_command[1] != "clear":
                raise CRMToolError("unverified_director_request", "The director must explicitly request an empty description.")
        else:
            replacement = re.search(r"\bto:?\s+(.+)$", text)
            if not replacement or _replacement(args.get("text") or "") != _replacement(replacement[1]):
                raise CRMToolError("unverified_director_request", "The complete description text must match the director's explicit replacement.")
        return True
    values = args.get("changes", {}) if name == "crm_update_task" else args
    for field in ("title", "details", "text"):
        value = values.get(field)
        if value and _normalized(value) not in _normalized(quote):
            raise CRMToolError("unverified_director_request", "The requested text must occur in the director command. Ask for the exact wording; nothing was saved.")
    if name == "crm_create_task":
        if _explicitly_undated(text) and args.get("due_date") is not None:
            raise CRMToolError("unverified_director_request", "An explicitly undated task cannot carry a due date, including when replaying a saved operation.")
        replay = saved_operation and saved_operation["action"] == "create" and saved_operation["due_date"] == args.get("due_date")
        requested_date = _reminder_date(text, directive, timezone)
        if not replay and args.get("due_date") != requested_date:
            raise CRMToolError("unverified_director_request", "The due date must match the director's date in the saved operator timezone. Ask for clarification instead of choosing another date.")
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
                     directive: Directive | None = None,
                     mutation_budget: CRMMutationBudget | None = None) -> tuple[bool, dict]:
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
        saved_operation = _saved_task_operation(people, pid, args["operation_id"])
        request = json.dumps({"tool": name, "person_id": pid,
            "arguments": {key: value for key, value in args.items() if key not in {"person_id", "person_name", "request_quote"}}},
            sort_keys=True, separators=(",", ":"))
        replay_authority = mutation_budget.check(request) if mutation_budget is not None else False
        human = _human_request(name, args, directive, timezone=timezone, saved_operation=saved_operation)
        if not human:
            raise CRMToolError("unverified_director_request", "Chat CRM writes require an explicit current director command. Ask for wording such as 'Remind me to send examples', 'Mark that task done', or 'Change the description to: …'. Nothing was saved.")
        if not replay_authority:
            _verify_task_target(name, args, people, pid, directive, allowed_source_ids, saved_operation)
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
        if mutation_budget is not None:
            mutation_budget.consume(request)
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
