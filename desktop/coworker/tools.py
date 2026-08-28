"""Sourcecado tool schemas and execution dispatch."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from zoneinfo import ZoneInfo

from coworker.apollo import (
    MISSING_KEY,
    apollo_evidence,
    enrich_contact,
    enrichment_match,
    masked_name,
    search_people,
)
from coworker.apollo_curation import curate_apollo_candidates
from coworker.calendar import calendar_evidence
from coworker.drive import drive_evidence
from coworker.evidence_envelope import (
    TRUST_BY_ORIGIN,
    EvidenceParts,
    opaque,
    origin_of_ref,
    owned,
)
from coworker.mcp import mcp_evidence
from coworker.web import MISSING_KEY as TAVILY_MISSING, search_web, web_evidence
from coworker.gmail import GmailError, MissingGmail, gmail_evidence
from coworker.board_tools import BOARD_TOOL_NAMES, BOARD_TOOL_SCHEMAS, execute_board_tool
from coworker.drive_ingestion import DRIVE_INDEX_QUERY_SCHEMA, DriveIngestionStore
from coworker.people import PersonStore
from coworker.store import ConversationStore
from coworker.workspace_runtime import WORKSPACE_TOOL_NAMES, WORKSPACE_TOOL_SCHEMAS
from coworker.workspace_shell import shell_evidence, workspace_file_evidence

TZ = ZoneInfo("America/Los_Angeles")

NOW_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "now",
        "description": (
            "Current date and time in America/Los_Angeles. "
            "Use when the user asks what time or day it is."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}

REMEMBER_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "Save a durable fact or preference about Fisher. "
            "Use when asked to remember something, or when a stable fact will matter later."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The fact to remember, in one or two sentences.",
                }
            },
            "required": ["content"],
            "additionalProperties": False,
        },
    },
}

MEMORY_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "memory_update",
        "description": (
            "Rewrite an existing memory by [#id] from the known-memories list. "
            "Use this instead of remember when the fact is already saved."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "The [#id] of the memory to replace.",
                },
                "content": {
                    "type": "string",
                    "description": "The full corrected memory text.",
                },
            },
            "required": ["memory_id", "content"],
            "additionalProperties": False,
        },
    },
}

WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the public web. Returns titles, URLs, and snippets. "
            "Use for cited research. Not a gate on drafting."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

APOLLO_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "apollo_search_people",
        "description": (
            "Search people at an organization via Apollo. Returns titles and "
            "obfuscated last names — never emails. Use organizationName and/or personTitles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "organizationName": {"type": "string"},
                "personTitles": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
}

PEOPLE_KEEP_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "people_keep",
        "description": (
            "File curated Apollo search rows into person files. Use after "
            "apollo_search_people when the director names who to keep. "
            "Does not enrich and does not send. Never invent the target."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "people": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Apollo search rows to keep.",
                },
                "target": {
                    "type": "string",
                    "description": "Why the director wants to write these people.",
                },
            },
            "required": ["people", "target"],
            "additionalProperties": False,
        },
    },
}

APOLLO_ENRICH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "apollo_enrich_contact",
        "description": (
            "Enrich one contact via Apollo. Provide email, or firstName and lastName. "
            "Spends credits. Fisher must Allow or Deny first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string"},
                "firstName": {"type": "string"},
                "lastName": {"type": "string"},
                "organizationName": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}

GMAIL_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "gmail_search",
        "description": "Search Fisher's Gmail. Auto. Never sends.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

GMAIL_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "gmail_read",
        "description": "Read one Gmail message by id. Auto. Never sends.",
        "parameters": {
            "type": "object",
            "properties": {"message_id": {"type": "string"}},
            "required": ["message_id"],
            "additionalProperties": False,
        },
    },
}

DRIVE_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "drive_search",
        "description": "Search Google Drive file names and text. Readonly. This is fuzzy text search, not Drive query syntax; use drive_list_folder to open a folder.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

DRIVE_LIST_FOLDER_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "drive_list_folder",
        "description": "List the direct children of a Google Drive folder by folder id. Readonly. Call again for child folders to traverse a tree.",
        "parameters": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string"},
                "max_results": {"type": "integer"},
                "page_token": {"type": "string"},
            },
            "required": ["folder_id"],
            "additionalProperties": False,
        },
    },
}

DRIVE_READ_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "drive_read",
        "description": "Read one Google Drive file by id. Readonly. Folder ids are listed, not downloaded; prefer drive_list_folder for traversal.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
            "required": ["file_id"],
            "additionalProperties": False,
        },
    },
}

CALENDAR_LIST_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calendar_list",
        "description": "List upcoming Google Calendar events from now. Auto. Pass time_min/time_max only to change the window.",
        "parameters": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string"},
                "time_max": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
}

CALENDAR_CREATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calendar_create",
        "description": "Create a Google Calendar event. Fisher must Allow first. No delete.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "timezone": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["summary", "start", "end"],
            "additionalProperties": False,
        },
    },
}

CALENDAR_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "calendar_update",
        "description": "Update a Google Calendar event. Fisher must Allow first. No delete.",
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["event_id"],
            "additionalProperties": False,
        },
    },
}

GMAIL_SEND_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "gmail_send",
        "description": (
            "Send an existing Gmail draft by id. Fisher must Allow first. "
            "Use after a draft was created and reviewed. Does not create a new message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "The Gmail draft id to send.",
                }
            },
            "required": ["draft_id"],
            "additionalProperties": False,
        },
    },
}

GMAIL_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "gmail_draft",
        "description": (
            "Create a Gmail draft for Fisher to review. Never sends. "
            "Use when asked to draft an email. Fisher must Allow or Deny first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address."},
                "subject": {"type": "string", "description": "Subject line."},
                "body": {"type": "string", "description": "Plain-text body."},
            },
            "required": ["to", "subject", "body"],
            "additionalProperties": False,
        },
    },
}

LOAD_SKILL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": (
            "Load a skill's full instructions by name from the skills catalog. "
            "Call when a listed skill is relevant."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name from the catalog."}
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}

MEMORY_FORGET_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "memory_forget",
        "description": "Delete a memory by [#id] from the known-memories list.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "description": "The [#id] of the memory to delete.",
                }
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
    },
}

OPENAI_TOOLS = [
    NOW_SCHEMA,
    REMEMBER_SCHEMA,
    MEMORY_UPDATE_SCHEMA,
    MEMORY_FORGET_SCHEMA,
    GMAIL_SEARCH_SCHEMA,
    GMAIL_READ_SCHEMA,
    GMAIL_DRAFT_SCHEMA,
    GMAIL_SEND_SCHEMA,
    DRIVE_SEARCH_SCHEMA,
    DRIVE_LIST_FOLDER_SCHEMA,
    DRIVE_READ_SCHEMA,
    DRIVE_INDEX_QUERY_SCHEMA,
    CALENDAR_LIST_SCHEMA,
    CALENDAR_CREATE_SCHEMA,
    CALENDAR_UPDATE_SCHEMA,
    APOLLO_SEARCH_SCHEMA,
    PEOPLE_KEEP_SCHEMA,
    APOLLO_ENRICH_SCHEMA,
    LOAD_SKILL_SCHEMA,
    WEB_SEARCH_SCHEMA,
    *BOARD_TOOL_SCHEMAS,
    *WORKSPACE_TOOL_SCHEMAS,
]


# Tools whose result Sourcecado itself wrote: the clock, the skills catalog,
# the Board's own write receipts, and the echo of a draft the model composed.
# Every other tool result is somebody else's text until proven otherwise, and
# `evidence_for` proves nothing - it classifies, and unknown means external.
SOURCECADO_OWNED = frozenset(
    {
        "now",
        "load_skill",
        "remember",
        "memory_update",
        "memory_forget",
        "people_keep",
        "gmail_draft",
        "gmail_send",
        "board_get",
        "board_query",
        "board_upsert",
        "board_mutate",
        "board_delete",
        "request_directory",
        "fs_stat",
        "fs_list",
        "fs_find",
        "fs_write",
        "fs_edit",
        "fs_move",
        "fs_trash",
        "shell_kill",
        "shell_write_stdin",
    }
)

# The connector each tool speaks to, used when a call fails and the only text
# available is an error string somebody else may have written.
_FAILURE_CONNECTORS = {
    "gmail": "gmail",
    "drive": "drive",
    "calendar": "calendar",
    "apollo": "apollo",
    "web": "web",
    "fs": "workspace",
    "shell": "shell",
}

_EVIDENCE_ADAPTERS = {
    "gmail_read": gmail_evidence,
    "gmail_search": gmail_evidence,
    "drive_read": drive_evidence,
    "drive_search": drive_evidence,
    "drive_list_folder": drive_evidence,
    "calendar_list": calendar_evidence,
    "calendar_create": calendar_evidence,
    "calendar_update": calendar_evidence,
    "apollo_search_people": apollo_evidence,
    "apollo_enrich_contact": apollo_evidence,
    "web_search": web_evidence,
    "shell_exec": shell_evidence,
    "shell_poll": shell_evidence,
    "fs_read": workspace_file_evidence,
    "fs_search": workspace_file_evidence,
    "drive_index_query": workspace_file_evidence,
}


def _failure_connector(name: str) -> str | None:
    if name.startswith("mcp__"):
        return "granola" if name.startswith("mcp__granola__") else "mcp"
    return _FAILURE_CONNECTORS.get(str(name).split("_", 1)[0])


def board_origin_conflict(name: str, args: dict[str, Any]) -> str | None:
    """Refuse a Board write that files evidence under an origin it does not have.

    Board writes are automatic, so nothing pauses to ask whether a claim came
    from the director or from a stranger's document. The reference id answers
    that on its own. A write may state the origin it believes, and a write may
    say nothing; what it may not do is disagree with the id. That is the
    durable half of the same rule `Envelope` enforces in memory: origin is
    derived, never asserted.
    """
    if name != "board_upsert" or str(args.get("record_type") or "") != "source_ref":
        return None
    fields = args.get("fields")
    if not isinstance(fields, dict):
        return None
    derived = origin_of_ref(fields.get("id"))
    claimed_origin = str(fields.get("origin") or "").strip().lower()
    if claimed_origin and claimed_origin != str(derived):
        return (
            f"source reference {str(fields.get('id') or '')!r} is {derived} "
            f"evidence and cannot be filed as {claimed_origin}"
        )
    claimed_trust = str(fields.get("trust") or "").strip().lower()
    if claimed_trust and claimed_trust != str(TRUST_BY_ORIGIN[derived]):
        return (
            f"source reference {str(fields.get('id') or '')!r} is "
            f"{TRUST_BY_ORIGIN[derived]} and cannot be filed as {claimed_trust}"
        )
    return None


def evidence_for(name: str, payload: Any, *, ok: bool = True) -> EvidenceParts:
    """Classify one tool result before anything downstream reads it.

    This is the tool boundary's half of the contract: every connector result
    leaves here already carrying an origin, a trust class, and a stable
    source reference. `turn.py` owns the other half, which is delimiting.
    """
    if not isinstance(payload, dict):
        return opaque("unknown", str(name), payload)
    if name in SOURCECADO_OWNED:
        # Sourcecado composed this call and the result is its receipt. A
        # failure here is a short status the runtime shapes for the operator,
        # not a document. The known gap is a connector error string on one of
        # these tools: Gmail wrote it, and it renders unfenced.
        return owned(payload)
    if not ok:
        connector = _failure_connector(str(name))
        if connector is None:
            return owned(payload)
        # A connector's own failure message is written by the connector.
        # Sourcecado cannot tell one apart from a server that answers an
        # error with an instruction, so it does not try.
        return opaque(connector, str(name), payload)
    adapter = _EVIDENCE_ADAPTERS.get(str(name))
    if adapter is not None:
        return adapter(str(name), payload)
    if str(name).startswith("mcp__"):
        return mcp_evidence(str(name), payload)
    # A tool this build does not model. Fence the whole thing.
    return opaque("unknown", str(name), payload)


def now() -> dict[str, str]:
    dt = datetime.now(TZ)
    return {
        "iso": dt.isoformat(timespec="seconds"),
        "weekday": dt.strftime("%A"),
        "tz": "America/Los_Angeles",
    }


def _memory_id(raw: Any) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def execute(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    store: ConversationStore | None = None,
    gmail: Any = None,
    drive: Any = None,
    drive_ingestions: DriveIngestionStore | None = None,
    calendar: Any = None,
    http: Any = None,
    apollo_key: str | None = None,
    skills: Any = None,
    mcp: Any = None,
    people: PersonStore | None = None,
    session_id: str | None = None,
    tavily_key: str | None = None,
    actor: str = "assistant",
    run_id: str | None = None,
    allowed_source_ids: set[str] | None = None,
    workspace_runtime: Any = None,
    approval_granted: bool = False,
    approval_scope: str = "once",
    approval_fingerprint: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    args = arguments or {}
    if name in WORKSPACE_TOOL_NAMES:
        if workspace_runtime is None:
            return False, {"status": "failed", "error": "workspace runtime missing"}
        return workspace_runtime.execute_tool(
            name,
            args,
            approval_granted=approval_granted,
            approval_scope=approval_scope,
            approval_fingerprint=approval_fingerprint,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
        )
    if name in BOARD_TOOL_NAMES:
        if people is None:
            return False, {"status": "failed", "error": "people store missing"}
        conflict = board_origin_conflict(name, args)
        if conflict is not None:
            return False, {"status": "failed", "error": conflict}
        return execute_board_tool(
            name,
            args,
            people=people,
            actor=actor,
            session_id=session_id,
            run_id=run_id,
            allowed_source_ids=allowed_source_ids,
        )
    if name == "drive_index_query":
        if drive_ingestions is None:
            return False, {"status": "failed", "error": "Drive index is unavailable"}
        try:
            return True, drive_ingestions.query(
                str(args.get("job_id") or ""),
                str(args.get("query") or ""),
                include_external=bool(args.get("include_external")),
                limit=int(args.get("limit") or 20),
            )
        except ValueError as exc:
            return False, {"status": "failed", "error": str(exc)}
    if name == "now":
        return True, now()
    if name == "load_skill":
        skill_name = str(args.get("name") or "").strip()
        if not skill_name:
            return False, {"error": "name is required"}
        if skills is None:
            return False, {"error": "skills missing"}
        skill = skills.get(skill_name)
        if skill is None:
            return False, {"error": f"unknown skill {skill_name}"}
        return True, {
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.instructions,
        }
    if mcp is not None and mcp.has(name):
        try:
            result = mcp.call(name, args)
        except Exception as exc:
            return False, {"error": str(exc)}
        if isinstance(result, dict) and result.get("error"):
            return False, result
        return True, result
    if name == "gmail_search":
        client = gmail if gmail is not None else MissingGmail()
        try:
            return True, client.search(
                str(args.get("query") or ""),
                int(args.get("max_results") or 10),
            )
        except GmailError as exc:
            return False, {"error": str(exc)}
        except Exception as exc:
            return False, {"error": str(exc)}
    if name == "gmail_read":
        client = gmail if gmail is not None else MissingGmail()
        mid = str(args.get("message_id") or "").strip()
        if not mid:
            return False, {"error": "message_id is required"}
        try:
            return True, client.read(message_id=mid)
        except GmailError as exc:
            return False, {"error": str(exc)}
        except Exception as exc:
            return False, {"error": str(exc)}
    if name in {"drive_search", "drive_list_folder", "drive_read"}:
        if drive is None:
            return False, {"error": "Drive is not connected."}
        try:
            if name == "drive_search":
                return True, drive.search(str(args.get("query") or ""), int(args.get("max_results") or 10))
            if name == "drive_list_folder":
                folder_id = str(args.get("folder_id") or "").strip()
                if not folder_id:
                    return False, {"status": "failed", "error": "folder_id is required"}
                return True, drive.list_folder(
                    folder_id,
                    int(args.get("max_results") or 100),
                    str(args.get("page_token") or "") or None,
                )
            fid = str(args.get("file_id") or "").strip()
            if not fid:
                return False, {"error": "file_id is required"}
            result = drive.read(fid, int(args.get("max_chars") or 20000))
            return True, result
        except Exception as exc:
            return False, {"status": "failed", "error": str(exc)}
    if name in {"calendar_list", "calendar_create", "calendar_update"}:
        if calendar is None:
            return False, {"error": "Calendar is not connected."}
        try:
            if name == "calendar_list":
                return True, calendar.list_events(
                    time_min=str(args.get("time_min") or "") or None,
                    time_max=str(args.get("time_max") or "") or None,
                    max_results=int(args.get("max_results") or 10),
                )
            if name == "calendar_create":
                return True, calendar.create(
                    summary=str(args.get("summary") or ""),
                    start=str(args.get("start") or ""),
                    end=str(args.get("end") or ""),
                    timezone=str(args.get("timezone") or "America/Los_Angeles"),
                    description=str(args.get("description") or ""),
                )
            return True, calendar.update(
                event_id=str(args.get("event_id") or ""),
                summary=args.get("summary"),
                start=args.get("start"),
                end=args.get("end"),
                description=args.get("description"),
            )
        except Exception as exc:
            return False, {"error": str(exc)}
    if name == "gmail_draft":
        to = str(args.get("to") or "").strip()
        subject = str(args.get("subject") or "").strip()
        body = str(args.get("body") or "").strip()
        if not to or not subject or not body:
            return False, {"error": "to, subject, and body are required"}
        client = gmail if gmail is not None else MissingGmail()
        try:
            result = client.create_draft(to=to, subject=subject, body=body)
        except GmailError as exc:
            return False, {"error": str(exc)}
        except Exception as exc:
            return False, {"error": str(exc)}
        result = dict(result)
        result["sent"] = False
        return True, result
    if name == "gmail_send":
        draft_id = str(args.get("draft_id") or "").strip()
        if not draft_id:
            return False, {"error": "draft_id is required"}
        client = gmail if gmail is not None else MissingGmail()
        try:
            result = client.send(draft_id=draft_id)
        except GmailError as exc:
            return False, {"error": str(exc)}
        except Exception as exc:
            return False, {"error": str(exc)}
        result = dict(result)
        result["sent"] = True
        result["draft_id"] = draft_id
        return True, result
    if name in {"remember", "memory_update", "memory_forget"}:
        if store is None:
            return False, {"error": "memory store missing"}
        if name == "remember":
            content = str(args.get("content") or "").strip()
            if not content:
                return False, {"error": "content is required"}
            item = store.remember(content)
            return True, {"saved": True, "id": item["id"], "content": item["content"]}
        mid = _memory_id(args.get("memory_id"))
        if mid is None:
            return False, {"error": "memory_id is required"}
        if name == "memory_update":
            content = str(args.get("content") or "").strip()
            if not content:
                return False, {"error": "content is required"}
            item = store.memory_update(mid, content)
            if item is None:
                return False, {"error": f"no memory #{mid}"}
            return True, {"updated": True, "id": item["id"], "content": item["content"]}
        if not store.memory_forget(mid):
            return False, {"error": f"no memory #{mid}"}
        return True, {"forgotten": True, "id": mid}
    if name in {"apollo_search_people", "apollo_enrich_contact"}:
        if not apollo_key:
            return False, {"error": MISSING_KEY}
        from coworker.apollo import LiveHttp

        client = http if http is not None else LiveHttp()
        try:
            if name == "apollo_search_people":
                titles = args.get("personTitles")
                if isinstance(titles, str):
                    titles = [titles]
                return True, search_people(
                    http=client,
                    api_key=apollo_key,
                    organization_name=str(args.get("organizationName") or "") or None,
                    person_titles=list(titles) if titles else None,
                    limit=int(args.get("limit") or 10),
                )
            person_id = str(args.get("person_id") or "") or None
            if person_id is None and people is not None and session_id:
                person_id = people.person_for_session(session_id)
            if not person_id:
                return False, {"error": "Bind a person before enriching."}
            person = people.get(person_id) if people is not None else None
            if people is not None and person is None:
                return False, {"error": "unknown person"}
            match = {
                "email": str(args.get("email") or "") or None,
                "first_name": str(args.get("firstName") or "") or None,
                "last_name": str(args.get("lastName") or "") or None,
                "organization_name": str(args.get("organizationName") or "") or None,
            }
            usable = bool(match["email"]) or bool(
                match["first_name"]
                and match["last_name"]
                and not masked_name(match["last_name"])
            )
            if not usable and person is not None:
                # The shortlist hands the model an obfuscated surname. The
                # person file knows its own Apollo ID, so take the match key
                # from the file rather than letting the model guess a name.
                match = enrichment_match(person) or match
            result = enrich_contact(
                http=client,
                api_key=apollo_key,
                **match,
            )
            if people is not None:
                people.apply_enrichment(
                    person_id,
                    name=result.get("name"),
                    title=result.get("title"),
                    company=result.get("organizationName"),
                    email=result.get("email"),
                    linkedin_url=result.get("linkedinUrl"),
                    phone=result.get("phone"),
                )
            return True, result
        except Exception as exc:
            return False, {"error": str(exc)}
    if name == "people_keep":
        if people is None:
            return False, {"error": "people store missing"}
        rows = args.get("people") or []
        if not isinstance(rows, list):
            return False, {"error": "people must be a list"}
        if not rows:
            return True, {"kept": []}
        if any(not isinstance(row, dict) for row in rows):
            return False, {"error": "people rows must be objects"}
        try:
            result = curate_apollo_candidates(
                people,
                rows,
                target=str(args.get("target") or ""),
            )
        except ValueError as exc:
            return False, {"error": str(exc)}
        return bool(result["kept"]), result
    if name == "web_search":
        if not tavily_key:
            return False, {"error": TAVILY_MISSING}
        query = str(args.get("query") or "").strip()
        if not query:
            return False, {"error": "query is required"}
        from coworker.apollo import LiveHttp

        client = http if http is not None else LiveHttp()
        try:
            return True, search_web(
                http=client,
                api_key=tavily_key,
                query=query,
                max_results=int(args.get("max_results") or 5),
            )
        except Exception as exc:
            return False, {"error": str(exc)}
    return False, {"error": f"unknown tool {name}"}
