"""Sourcecado sidecar: local API, streamed chat, approvals, and durable state."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

import coworker.turn as turn_runtime
from coworker.automation.scheduler import (
    ROUTINE_TEMPLATES,
    SUPPORTED_CADENCES,
    Scheduler,
    next_monday_0900,
    now_iso,
)
from coworker.agent_run_repository import AgentRunRepository
from coworker.apollo import (
    ENRICH_CREDIT_COST,
    MISSING_KEY as APOLLO_MISSING_KEY,
    enrich_contact,
    enrichment_match,
    enrichment_resource,
)
from coworker.apollo_curation import curate_apollo_candidates
from coworker.calendar import calendar_from_secrets
from coworker.connectors.google_oauth import (
    CALENDAR_SCOPE,
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    GOOGLE_KEY,
    GMAIL_KEY,
    READ_SCOPE,
    authorization_url,
    exchange_code,
    google_client_credentials,
    has_calendar_access,
    has_scope,
    load_google,
    merge_scopes,
    save_google,
)
from coworker.drive import drive_from_secrets
from coworker.drive_evidence import attach as attach_drive_evidence
from coworker.drive_ingestion import DriveIngestionCoordinator, DriveIngestionStore
from coworker.drive_ingestion_api import drive_ingestion_router
from coworker.effective_tools import (
    EffectiveToolCatalog,
    ToolAvailability,
    ToolCatalogError,
    effective_tool_catalog,
)
from coworker.events import build_event, new_turn_identity, TurnIdentity
from coworker.gmail import (
    GmailError,
    MissingGmail,
    SendAuthority,
    SendAuthorityError,
    authority_for_draft,
    draft_snapshot,
    gmail_from_secrets,
    send_reviewed_draft,
)
from coworker.inbox import Inbox
from coworker.mcp import LiveMcp, write_default_mcp_json
from coworker.mcp_oauth import McpOAuth
from coworker.meeting_evidence import MeetingEvidenceStore
from coworker.brief import build_brief
from coworker.people import PersonStore
from coworker.persona import ManifestError, Persona, load_persona
from coworker.reply_filing import InboundReader, refresh_replies
from coworker.prompt_contract import (
    AssembledSystemPrompt,
    PromptDefinition,
    PromptSection,
    SOURCING_DIRECTOR_V1,
    assemble_system_prompt,
)
from coworker.skills import BUILTIN_SKILLS, SkillLoader, catalog_text
from coworker.provider import (
    ToolCall,
    provider_model_metadata,
    provider_verifications,
    safe_model_identifier,
)
from coworker.provider_retry import verified_provider_chain
from coworker.run_ledger import RunLedger
from coworker.run_ledger_api import run_ledger_router
from coworker.secrets import SecretStore
from coworker.store import ConversationStore, valid_session_id
from coworker.telemetry import (
    AgentTurnSpan,
    ErrorKind,
    InMemoryTelemetryAdapter,
    RetryEvent,
    RetryReason,
    TelemetryAdapter,
    TelemetryRecorder,
    ToolSpan,
    TraceContext,
)
from coworker.tools import OPENAI_TOOLS, execute
from coworker.workspace import GrantUnavailable
from coworker.workspace_files import WorkspacePathError
from coworker.workspace_runtime import WorkspaceRuntime
from coworker.turn import (
    close_open_tool_calls,
    _tool_failure,
    RunControl,
    RunCoordinator,
    run_turn,
)

_UNSET = object()

_ALLOWED_ORIGIN_RE = re.compile(
    r"^(tauri://localhost"
    r"|https?://localhost(:\d+)?"
    r"|https?://127\.0\.0\.1(:\d+)?"
    r"|https?://tauri\.localhost)$"
)

TOKEN_HEADER = "X-Club-Token"
TOKEN_ENV = "CLUB_API_TOKEN"
SLICE = 29
MAX_STEPS = 8
_PERSON_FILE_EVENT_CAP = 12


def _runtime_prompt_definition(persona: Persona | None) -> PromptDefinition:
    if persona is None or persona.id == "sourcing":
        return SOURCING_DIRECTOR_V1
    return PromptDefinition(
        version=f"persona-{persona.id}-v1",
        sections=(PromptSection("persona", persona.name, persona.body),),
        static_budget_chars=6_000,
        dynamic_budgets=dict(SOURCING_DIRECTOR_V1.dynamic_budgets),
        labels_budget_chars=500,
        total_budget_chars=15_500,
    )


def _bounded_context(text: str, budget_chars: int) -> str:
    return text if len(text) <= budget_chars else text[:budget_chars]


def system_prompt_assembly(
    store: ConversationStore,
    persona: Persona | None = None,
    skills: SkillLoader | None = None,
    *,
    people: PersonStore | None = None,
    session_id: str | None = None,
) -> AssembledSystemPrompt:
    dynamic: list[PromptSection] = []
    items = store.list_memories()
    if items:
        memory = "\n".join(f"[#{item['id']}] {item['content']}" for item in items)
        dynamic.append(
            PromptSection(
                "saved_memory",
                "Saved memory",
                _bounded_context(memory, 4_000),
            )
        )
    if skills is not None:
        catalog = catalog_text(skills)
        if catalog:
            dynamic.append(
                PromptSection(
                    "skill_catalog",
                    "Skill catalog",
                    _bounded_context(catalog, 3_000),
                )
            )
    if people is not None and session_id:
        person_id = people.person_for_session(session_id)
        person = people.get(person_id) if person_id else None
        if person is not None:
            events = people.timeline(person_id)
            brief = build_brief(person, events)
            recent = events[-_PERSON_FILE_EVENT_CAP:]
            learned_lines = [
                (
                    f"[{event.get('source')}:{event.get('event_id')}] "
                    f"{event.get('summary')}"
                )
                for event in recent
                if event.get("summary")
                and event.get("source")
                and event.get("event_id")
            ]
            person_context = (
                "Person file:\n"
                f"who: {brief['who']}\n"
                f"why: {brief['why']}\n"
                "learned:\n"
                + ("\n".join(f"- {line}" for line in learned_lines) or "-")
                + "\n"
                f"missing: {', '.join(brief['missing'])}"
            )
            dynamic.append(
                PromptSection(
                    "person_file",
                    "Person File context",
                    _bounded_context(person_context, 2_000),
                )
            )
    return assemble_system_prompt(
        definition=_runtime_prompt_definition(persona),
        dynamic_sections=tuple(dynamic),
    )


def system_prompt(
    store: ConversationStore,
    persona: Persona | None = None,
    skills: SkillLoader | None = None,
    *,
    people: PersonStore | None = None,
    session_id: str | None = None,
) -> str:
    return system_prompt_assembly(
        store,
        persona,
        skills,
        people=people,
        session_id=session_id,
    ).text


def state_dir() -> Path:
    override = os.environ.get("CLUB_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "club"


def _open_browser(url: str) -> bool:
    try:
        import subprocess

        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


_SECRET_EVENT_KEYS = frozenset(
    {"access_token", "refresh_token", "authorization", "api_key", "token"}
)


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    if isinstance(payload, dict):
        payload = {
            key: value
            for key, value in payload.items()
            if key.lower() not in _SECRET_EVENT_KEYS
        }
    return {**event, "payload": payload}


def _origin_allowed(origin: str | None) -> bool:
    return origin is None or bool(_ALLOWED_ORIGIN_RE.match(origin))


def _tokens_match(got: str, expected: str) -> bool:
    if not got or len(got) != len(expected):
        return False
    return secrets.compare_digest(got, expected)


def _ws_token_ok(ws: WebSocket, expected: str) -> bool:
    header = ws.headers.get("sec-websocket-protocol", "")
    parts = [p.strip() for p in header.split(",") if p.strip()]
    query = (ws.query_params.get("token") or "").strip()
    candidates = parts + ([query] if query else [])
    return any(_tokens_match(c, expected) for c in candidates)


# Execution states that carry a final outcome; matches the overwrite guard in
# ConversationStore.complete_inbox_execution. Receipts are only ever built
# from items in one of these states.
_TERMINAL_EXECUTION = frozenset(
    {"succeeded", "failed", "not_run", "cancelled", "expired", "interrupted"}
)


async def _await_permission(
    call_id: str, inbox: Inbox, control: RunControl | None = None
) -> str:
    while True:
        if control is not None and control.cancel_requested.is_set():
            return "cancel"
        item = inbox.get(call_id)
        if item is None or item.get("state") in ("cancelled", "expired"):
            return "cancel"
        if item.get("state") == "resolved" and item.get("decision") in ("allow", "deny"):
            return str(item["decision"])
        await asyncio.sleep(0.05)


def _close_open_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return close_open_tool_calls(messages)


def create_app(
    *,
    token: str,
    provider: Any = _UNSET,
    state: Path | None = None,
    persona_id: str | None = None,
    gmail: Any = None,
    http: Any = None,
    apollo_key: str | None = None,
    public_url: str | None = None,
    mcp: Any = None,
    browser_opener: Callable[[str], bool] | None = None,
    workspace_runtime: WorkspaceRuntime | None = None,
    telemetry_adapter: TelemetryAdapter | None = None,
    provider_failovers: tuple[Any, ...] | None = None,
) -> FastAPI:
    if not token:
        raise ValueError("sidecar token must be non-empty")

    app = FastAPI(title="Club sidecar", version="0.0.2")
    app.state.token = token
    if provider is _UNSET:
        provider_chain = verified_provider_chain()
        app.state.provider = provider_chain[0] if provider_chain else None
        app.state.provider_failovers = provider_chain[1:]
    else:
        app.state.provider = provider
        app.state.provider_failovers = tuple(provider_failovers or ())
    app.state.telemetry_adapter = (
        telemetry_adapter
        if telemetry_adapter is not None
        else InMemoryTelemetryAdapter()
    )
    app.state.telemetry_recorder = TelemetryRecorder(app.state.telemetry_adapter)
    root = state if state is not None else state_dir()
    app.state.store = ConversationStore(root)
    app.state.people = PersonStore(root)
    app.state.meeting_evidence = MeetingEvidenceStore(root, people=app.state.people)
    app.state.workspace_runtime = workspace_runtime or WorkspaceRuntime(root)
    app.state.secrets = SecretStore(Path(root) / "secrets.json")
    store = app.state.store
    if store.open_session_id() is None:
        if (store.conv_dir / "main.jsonl").exists() or store.index("main"):
            store.set_open_session("main")
        else:
            store.set_open_session(store.create_session()["session_id"])
    app.state.inbox = Inbox(app.state.store)
    app.state.scheduler = Scheduler(app.state.store, app.state.inbox)
    if persona_id:
        pid = persona_id
    elif os.environ.get("CLUB_PERSONA"):
        pid = os.environ.get("CLUB_PERSONA") or "sourcing"
    else:
        pid = app.state.store.get_setting("persona") or "sourcing"
    app.state.persona = load_persona(pid)
    app.state.http = http
    app.state.gmail = (
        gmail if gmail is not None else gmail_from_secrets(app.state.secrets, http=http)
    )
    app.state.drive = drive_from_secrets(app.state.secrets, http=http)
    app.state.drive_ingestions = DriveIngestionStore(root)
    app.state.drive_ingestion_coordinator = DriveIngestionCoordinator(
        app.state.drive_ingestions,
        lambda: app.state.drive,
    )
    app.include_router(
        drive_ingestion_router(
            store=app.state.drive_ingestions,
            coordinator=app.state.drive_ingestion_coordinator,
            people=app.state.people,
        )
    )
    # Read side only. Registering an owner and reclaiming leases belongs to
    # whatever starts the sidecar process, not to opening the store.
    app.state.agent_runs = AgentRunRepository(root)
    app.state.run_ledger = RunLedger(app.state.agent_runs, approvals=app.state.store)
    app.include_router(run_ledger_router(ledger=app.state.run_ledger))
    app.state.calendar = calendar_from_secrets(app.state.secrets, http=http)
    app.state.apollo_key = apollo_key if apollo_key is not None else os.environ.get("APOLLO_API_KEY")
    app.state.tavily_key = os.environ.get("TAVILY_API_KEY")
    app.state.public_url = public_url or "http://127.0.0.1:8765"
    app.state.browser_opener = browser_opener or _open_browser
    app.state.oauth_state = ""
    app.state.skills = SkillLoader([BUILTIN_SKILLS, Path(root) / "skills"])
    write_default_mcp_json(Path(root) / "mcp.json")
    app.state.mcp_oauth = McpOAuth(
        app.state.secrets,
        app.state.public_url,
        http=http,
        browser_opener=app.state.browser_opener,
    )
    app.state.mcp = (
        mcp
        if mcp is not None
        else LiveMcp(
            secrets=app.state.secrets,
            config_path=Path(root) / "mcp.json",
            oauth=app.state.mcp_oauth,
        )
    )

    def _effective_tool_catalog(
        persona: Persona | None = None,
    ) -> EffectiveToolCatalog:
        return effective_tool_catalog(
            persona=persona or app.state.persona,
            registered_schemas=(*OPENAI_TOOLS, *app.state.mcp.schemas()),
            workspace_runtime=app.state.workspace_runtime,
            availability=ToolAvailability(
                gmail=not isinstance(app.state.gmail, MissingGmail),
                drive=app.state.drive is not None,
                calendar=app.state.calendar is not None,
                apollo=bool(app.state.apollo_key),
                web=bool(app.state.tavily_key),
            ),
        )

    app.state.effective_tool_catalog = _effective_tool_catalog
    _effective_tool_catalog()  # Fail startup on invalid persona/registry contracts.

    def _default_job_runner(job: dict[str, Any]) -> dict[str, Any]:
        import asyncio

        sid = f"sched-{job['id']}"
        if app.state.store.index(sid) is None:
            app.state.store.create_session(sid)
            app.state.store.rename_session(sid, str(job.get("prompt") or "scheduled"))
        return asyncio.run(
            run_turn(
                text=str(job.get("prompt") or ""),
                sid=sid,
                store=app.state.store,
                provider=app.state.provider,
                failover_providers=app.state.provider_failovers,
                persona=app.state.persona,
                skills=app.state.skills,
                inbox=app.state.inbox,
                openai_tools=list(_effective_tool_catalog().schemas),
                execute_kwargs={
                    "store": app.state.store,
                    "gmail": app.state.gmail,
                    "drive": app.state.drive,
                    "drive_ingestions": app.state.drive_ingestions,
                    "calendar": app.state.calendar,
                    "http": app.state.http,
                    "apollo_key": app.state.apollo_key,
                    "tavily_key": app.state.tavily_key,
                    "skills": app.state.skills,
                    "mcp": app.state.mcp,
                    "people": app.state.people,
                    "workspace_runtime": app.state.workspace_runtime,
                },
                emit=None,
                wait_permission=None,
                system_prompt_fn=system_prompt,
                telemetry=app.state.telemetry_recorder,
            )
        )

    app.state.scheduler.job_runner = _default_job_runner
    app.state.run_coordinator = RunCoordinator()
    app.state.live_event_senders: set[Any] = set()
    recovery_projection_lock = threading.RLock()

    def _persist_approval_receipt(
        item: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if item is None:
            return None
        required = ("session_id", "run_id", "message_id", "part_id")
        if not all(item.get(field) for field in required):
            return None
        sid = str(item["session_id"])
        event = build_event(
            TurnIdentity(
                session_id=sid,
                run_id=str(item["run_id"]),
                message_id=str(item["message_id"]),
                part_id=str(item["part_id"]),
            ),
            "approval_resolved",
            event_id=f"event_{secrets.token_hex(16)}",
            id=str(item["id"]),
            name=str(item["name"]),
            resolution=(
                "allowed"
                if item.get("decision") == "allow"
                else "denied"
                if item.get("decision") == "deny"
                else str(item.get("state") or "cancelled")
            ),
            decision=item.get("decision"),
            actor=item.get("actor"),
            requested_at=str(item.get("requested_at") or item.get("created_at")),
            resolved_at=str(item.get("resolved_at")),
            scope=str(item.get("scope") or "once"),
            execution_status=str(item.get("execution_status") or "pending"),
            execution_error=item.get("execution_error"),
        )
        persisted, created = store.append_event_once(
            sid,
            event,
            matching_fields=("type", "id", "resolved_at"),
        )
        if created:
            app.state.workspace_runtime.record_permission_decision(item)
        if str(item.get("decision") or "") != "allow" or str(
            item.get("execution_status") or ""
        ) == "succeeded":
            app.state.workspace_runtime.discard_parked_arguments(
                str(item.get("id") or "")
            )
        return persisted

    def _claimed_arguments(item: dict[str, Any]) -> dict[str, Any]:
        arguments = dict(item.get("arguments") or {})
        if app.state.workspace_runtime.owns_tool(str(item.get("name") or "")):
            return app.state.workspace_runtime.restore_parked_arguments(
                str(item.get("id") or ""), arguments
            )
        return arguments

    async def _publish_live_events(events: list[dict[str, Any]]) -> None:
        for event in events:
            for registration in tuple(app.state.live_event_senders):
                owner_loop, sender, _alive = registration
                try:
                    current_loop = asyncio.get_running_loop()
                    if owner_loop is current_loop:
                        await sender(event)
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            sender(event), owner_loop
                        )
                        await asyncio.wrap_future(future)
                except Exception:
                    app.state.live_event_senders.discard(registration)

    def _bound_resource(item: dict[str, Any]) -> dict[str, Any] | None:
        """The person-bound authority this approval was parked with, if any."""
        resource = item.get("resource")
        if not isinstance(resource, dict):
            return None
        if resource.get("kind") in {"gmail_send_authority", "apollo_enrichment"}:
            return resource
        return None

    def _execute_bound_send(item: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """One approved send, against the binding the director actually read.

        Reached only by the single caller that won the execution claim in
        ConversationStore.decide_and_claim_inbox_execution. Everything below is
        about sending the *right* message; whether a send happens at all is
        already settled by that claim.
        """
        authority = SendAuthority.from_resource(
            item.get("resource"), approval_id=str(item.get("id") or "")
        )
        if authority is None:
            return False, {"error": "This send approval is missing its binding."}
        try:
            sent = send_reviewed_draft(app.state.gmail, authority)
        except SendAuthorityError as exc:
            return False, {"error": str(exc), "code": exc.code, "sent": False}
        except GmailError as exc:
            return False, {"error": str(exc), "code": "gmail_failed", "sent": False}
        except Exception as exc:
            return False, {"error": str(exc), "code": "gmail_failed", "sent": False}
        try:
            filed = app.state.people.record_approved_send(
                authority.person_id,
                message_id=sent["message_id"],
                thread_id=sent["thread_id"],
                draft_id=authority.draft_id,
                to=authority.to,
                subject=authority.subject,
                body_digest=authority.body_digest,
                account=authority.account,
                approval_id=authority.approval_id,
                actor="director",
                session_id=str(item.get("session_id") or "") or None,
                run_id=str(item.get("run_id") or "") or None,
            )
        except Exception as exc:
            # The message is already gone. Report the send truthfully and say
            # the receipt failed, rather than implying nothing was sent.
            return True, {
                **sent,
                "receipt_error": str(exc),
                "person_event_id": None,
            }
        return True, {
            **sent,
            "person_event_id": filed["event"]["event_id"],
            "sequence_state": (filed["person"] or {}).get("sequence_state"),
            "advanced_to_open": filed["advanced_to_open"],
        }

    def _execute_bound_enrichment(
        item: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """One approved Apollo enrichment, onto exactly the approved person."""
        resource = item.get("resource")
        if not isinstance(resource, dict):
            return False, {"error": "This enrichment approval is missing its binding."}
        person_id = str(resource.get("person_id") or "")
        person = app.state.people.get(person_id) if person_id else None
        if person is None:
            return False, {"error": "unknown person"}
        if not app.state.apollo_key:
            return False, {"error": APOLLO_MISSING_KEY}
        match = enrichment_match(person)
        if match is None:
            return False, {"error": "This person file has no email and no full name."}
        from coworker.apollo import LiveHttp

        http = app.state.http if app.state.http is not None else LiveHttp()
        try:
            result = enrich_contact(
                http=http, api_key=app.state.apollo_key, **match
            )
        except Exception as exc:
            return False, {"error": str(exc)}
        filed = app.state.people.record_apollo_enrichment(
            person_id,
            result=result,
            approval_id=str(item.get("id") or ""),
            credits=ENRICH_CREDIT_COST,
            matched_on=str(resource.get("matched_on") or "name"),
            actor="director",
            session_id=str(item.get("session_id") or "") or None,
            run_id=str(item.get("run_id") or "") or None,
        )
        if filed is None:
            return False, {"error": "unknown person"}
        return True, {
            "person_id": person_id,
            "credits": ENRICH_CREDIT_COST,
            "fields_applied": filed["fields_applied"],
            "source_ref_id": filed["source_ref"]["id"],
            "apollo_id": result.get("apolloId"),
        }

    async def _execute_claimed_approval(
        item: dict[str, Any], *, claimant: str
    ) -> dict[str, Any] | None:
        """Run an allow-claimed approval server-side and persist its receipt."""
        approval_span = None
        approval_tool_span = None
        try:
            approval_span = app.state.telemetry_recorder.start_span(
                AgentTurnSpan(operation="agent.approval"),
                TraceContext(
                    session_id=str(item.get("session_id") or ""),
                    run_id=str(item.get("run_id") or ""),
                ),
            )
            try:
                tool_schema = ToolSpan(
                    tool_name=str(item.get("name") or "unknown"),
                    operation="tool.execute",
                )
            except ValueError:
                tool_schema = ToolSpan(
                    tool_name="unknown",
                    operation="tool.execute",
                )
            approval_tool_span = approval_span.child(tool_schema)
        except ValueError:
            pass
        bound = _bound_resource(item)
        try:
            if bound is not None:
                runner = (
                    _execute_bound_send
                    if bound["kind"] == "gmail_send_authority"
                    else _execute_bound_enrichment
                )
                ok, result = await asyncio.to_thread(runner, item)
            else:
                ok, result = await asyncio.to_thread(
                    execute,
                    item["name"],
                    _claimed_arguments(item),
                    store=app.state.store,
                    gmail=app.state.gmail,
                    drive=app.state.drive,
                    calendar=app.state.calendar,
                    http=app.state.http,
                    apollo_key=app.state.apollo_key,
                    tavily_key=app.state.tavily_key,
                    skills=app.state.skills,
                    mcp=app.state.mcp,
                    people=app.state.people,
                    workspace_runtime=app.state.workspace_runtime,
                    approval_granted=True,
                    approval_scope=str(item.get("scope") or "once"),
                    approval_fingerprint=(
                        str((item.get("resource") or {}).get("fingerprint"))
                        if isinstance(item.get("resource"), dict)
                        and (item.get("resource") or {}).get("fingerprint")
                        else None
                    ),
                    session_id=str(item.get("session_id") or ""),
                    actor=str(item.get("actor") or "assistant"),
                    run_id=str(item.get("run_id") or "") or None,
                )
        except asyncio.CancelledError:
            if approval_tool_span is not None:
                approval_tool_span.cancel()
            if approval_span is not None:
                approval_span.cancel()
            raise
        except Exception as exc:
            ok, result = False, {"error": str(exc)}
        if approval_tool_span is not None and approval_span is not None:
            if ok:
                approval_tool_span.finish()
                approval_span.finish()
            else:
                approval_tool_span.partial(ErrorKind.TOOL)
                approval_span.partial(ErrorKind.TOOL)
        receipt = app.state.inbox.complete_execution(
            str(item["id"]),
            claimant=claimant,
            ok=ok,
            result=result,
        )
        _persist_approval_receipt(receipt)
        return receipt

    def _replace_recovery_tool_result(
        item: dict[str, Any], result: dict[str, Any]
    ) -> None:
        session_id = str(item.get("session_id") or "")
        original_call_id = str(item.get("original_call_id") or "")
        if not session_id or not original_call_id:
            return
        messages = store.load(session_id)
        for index, message in enumerate(messages):
            if (
                message.get("role") == "tool"
                and message.get("tool_call_id") == original_call_id
            ):
                messages[index] = {
                    **message,
                    "name": str(item.get("name") or message.get("name") or "tool"),
                    "content": json.dumps(result, ensure_ascii=False),
                }
                store.replace_all(session_id, messages)
                return

    async def _project_recovery_terminal(
        item: dict[str, Any],
        *,
        decision: str,
        ok: bool,
        result: dict[str, Any],
    ) -> None:
        session_id = str(item.get("session_id") or "")
        run_id = str(item.get("run_id") or "")
        command_id = str(item.get("recovery_command_id") or "")
        original_call_id = str(item.get("original_call_id") or "")
        message_id = str(item.get("message_id") or "")
        part_id = str(item.get("part_id") or "")
        if not all(
            (session_id, run_id, command_id, original_call_id, message_id, part_id)
        ):
            return
        to_publish: list[dict[str, Any]] = []
        with recovery_projection_lock:
            existing_events = store.load_events(session_id)
            if any(
                event.get("type") == "tool_recovery"
                and event.get("command_id") == command_id
                and event.get("status") != "approval_required"
                for event in existing_events
            ):
                return
            pending_recovery = next(
                (
                    event
                    for event in reversed(existing_events)
                    if event.get("type") == "tool_recovery"
                    and event.get("command_id") == command_id
                    and event.get("status") == "approval_required"
                ),
                None,
            )
            original_failure = (
                dict(pending_recovery.get("failure") or {})
                if pending_recovery is not None
                else {}
            )
            identity = TurnIdentity(
                session_id=session_id,
                run_id=run_id,
                message_id=message_id,
                part_id=part_id,
            )
            name = str(item.get("name") or "tool")
            recovery_failure = None
            # Anything short of a real result is an unknown outcome, never a
            # success or denial: interrupted, expired, or still executing.
            interrupted = item.get("execution_status") not in (
                "succeeded",
                "failed",
                "not_run",
            )
            if decision == "allow" and not interrupted:
                _replace_recovery_tool_result(item, result)
                if not ok:
                    recovery_failure = _tool_failure(
                        ToolCall(
                            id=original_call_id,
                            name=name,
                            arguments=dict(item.get("arguments") or {}),
                        ),
                        result,
                        identity,
                    )
                existing_finished = next(
                    (
                        event
                        for event in existing_events
                        if event.get("type") == "tool_finished"
                        and event.get("recovery_command_id") == command_id
                    ),
                    None,
                )
                if existing_finished is None:
                    finished = build_event(
                        identity,
                        "tool_finished",
                        event_id=f"event_{secrets.token_hex(16)}",
                        id=original_call_id,
                        name=name,
                        ok=ok,
                        result=result,
                        finished_at=datetime.now(UTC).isoformat(),
                        recovery_command_id=command_id,
                        **(
                            {"failure": recovery_failure}
                            if recovery_failure
                            else {}
                        ),
                    )
                    store.append_event(session_id, finished)
                    to_publish.append(finished)
            approval = _persist_approval_receipt(item)
            if approval is not None:
                to_publish.append(approval)
            status = (
                "interrupted"
                if interrupted
                else "denied"
                if decision == "deny"
                else "succeeded"
                if ok
                else "failed"
            )
            outcome = (
                str(item.get("execution_error") or "Recovery outcome is unknown.")
                if interrupted
                else "Retry was denied; prior successful work was preserved."
                if decision == "deny"
                else "Failed step recovered."
                if ok
                else "Retry failed; successful prior work was preserved."
            )
            recovery = build_event(
                identity,
                "tool_recovery",
                event_id=f"event_{secrets.token_hex(16)}",
                command_id=command_id,
                call_id=original_call_id,
                name=name,
                action="retry",
                status=status,
                outcome=outcome,
                **(
                    {"failure": recovery_failure or original_failure}
                    if status in {"failed", "interrupted"}
                    else {}
                ),
            )
            store.append_event(session_id, recovery)
            to_publish.append(recovery)
        await _publish_live_events(to_publish)

    async def _resolve_recovery_approval(
        item_id: str,
        decision: str,
        *,
        actor: str,
        scope: str,
        claimant: str,
    ) -> tuple[Any, dict[str, Any], bool, dict[str, Any]] | None:
        item = app.state.inbox.get(item_id)
        if item is None or item.get("kind") != "recovery_approval":
            return None
        claim = app.state.inbox.decide_and_claim(
            item_id,
            decision,
            actor=actor,
            scope=scope,
            claimant=claimant,
        )
        if claim is None:
            return None
        if decision == "allow" and claim.claimed and claim.owned:
            recovery_span = None
            recovery_tool_span = None
            try:
                recovery_span = app.state.telemetry_recorder.start_span(
                    AgentTurnSpan(operation="agent.recovery"),
                    TraceContext(
                        session_id=str(item.get("session_id") or ""),
                        run_id=str(item.get("run_id") or ""),
                    ),
                )
                recovery_span.record(
                    RetryEvent(
                        operation="tool.execute",
                        retry_count=1,
                        reason=RetryReason.TRANSIENT_TOOL,
                    )
                )
                try:
                    tool_schema = ToolSpan(
                        tool_name=str(item.get("name") or "unknown"),
                        operation="tool.execute",
                    )
                except ValueError:
                    tool_schema = ToolSpan(
                        tool_name="unknown", operation="tool.execute"
                    )
                recovery_tool_span = recovery_span.child(tool_schema)
            except ValueError:
                pass
            try:
                ok, result = await asyncio.to_thread(
                    turn_runtime.execute,
                    item["name"],
                    _claimed_arguments(item),
                    store=store,
                    gmail=app.state.gmail,
                    drive=app.state.drive,
                    drive_ingestions=app.state.drive_ingestions,
                    calendar=app.state.calendar,
                    http=app.state.http,
                    apollo_key=app.state.apollo_key,
                    tavily_key=app.state.tavily_key,
                    skills=app.state.skills,
                    mcp=app.state.mcp,
                    people=app.state.people,
                    workspace_runtime=app.state.workspace_runtime,
                    approval_granted=True,
                    approval_scope=str(item.get("scope") or "once"),
                    approval_fingerprint=(
                        str((item.get("resource") or {}).get("fingerprint"))
                        if isinstance(item.get("resource"), dict)
                        and (item.get("resource") or {}).get("fingerprint")
                        else None
                    ),
                    session_id=str(item.get("session_id") or ""),
                    actor=str(item.get("actor") or "assistant"),
                    run_id=str(item.get("run_id") or "") or None,
                )
                result = dict(result)
            except asyncio.CancelledError:
                if recovery_tool_span is not None:
                    recovery_tool_span.cancel()
                if recovery_span is not None:
                    recovery_span.cancel()
                raise
            except Exception as exc:
                ok, result = False, {"error": str(exc)}
            if recovery_tool_span is not None and recovery_span is not None:
                if ok:
                    recovery_tool_span.finish(retry_count=1)
                    recovery_span.finish(retry_count=1)
                else:
                    recovery_tool_span.partial(ErrorKind.TOOL, retry_count=1)
                    recovery_span.partial(ErrorKind.TOOL, retry_count=1)
            receipt = app.state.inbox.complete_execution(
                item_id,
                claimant=claimant,
                ok=ok,
                result=result,
            )
        elif decision == "deny":
            receipt = claim.item
        else:
            receipt = await app.state.inbox.wait_for_execution(item_id)
        if receipt is None:
            return None
        if receipt.get("execution_status") in ("executing", "pending"):
            # Outcome pending on a live claimant; do not write a durable
            # terminal for an execution that may still finish.
            return None
        ok, result = app.state.inbox.execution_outcome(receipt)
        await _project_recovery_terminal(
            receipt,
            decision=decision,
            ok=ok,
            result=result,
        )
        return claim, receipt, ok, result

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5180",
            "http://localhost:5180",
            "tauri://localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def gate(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if request.url.path in {"/v1/gmail/callback", "/v1/mcp/oauth/callback"}:
            return await call_next(request)
        if not _origin_allowed(request.headers.get("origin")):
            return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
        got = request.headers.get(TOKEN_HEADER, "")
        if not _tokens_match(got, app.state.token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/v1/health")
    def health():
        p = app.state.provider
        return {
            "status": "ok",
            "piece": "sidecar",
            "slice": SLICE,
            "model": None if p is None else p.model_id,
            "persona": app.state.persona.id,
        }

    @app.get("/v1/hello")
    def hello():
        return {
            "message": "sidecar alive",
            "piece": "brain",
            "window": "remote control. the agent does not live in the pixels",
        }

    @app.get("/v1/persona")
    def persona():
        p = app.state.persona
        return {
            "id": p.id,
            "name": p.name,
            "tools": list(_effective_tool_catalog().names),
        }

    @app.get("/v1/skills")
    def skills():
        return {"skills": app.state.skills.catalog()}

    @app.get("/v1/settings")
    def settings():
        p = app.state.provider
        profile = load_google(app.state.secrets)
        on_duty = app.state.persona
        reports = provider_verifications()
        active_provider = str(getattr(p, "provider_id", "")) if p is not None else ""
        active_model = str(getattr(p, "model_id", "")) if p is not None else ""
        safe_active_model = (
            active_model if active_model and safe_model_identifier(active_model) else None
        )
        return {
            "persona": {"id": on_duty.id, "name": on_duty.name},
            "model": safe_active_model,
            "providers": [
                {
                    "provider": report.provider,
                    "model": report.model,
                    "selected": (
                        report.provider == active_provider
                        and report.model == safe_active_model
                    ),
                    "eligible": report.eligible,
                    "failures": [
                        "missing_credentials"
                        if failure == "missing_api_key"
                        else failure
                        for failure in report.failures
                    ],
                    "context_window_tokens": provider_model_metadata(
                        report.provider, report.model
                    ).context_window_tokens,
                    "capabilities": {
                        "text": report.capabilities.text,
                        "transient_reasoning": (
                            report.capabilities.transient_reasoning
                        ),
                        "tool_calling": report.capabilities.tool_calling,
                        "terminal_usage": report.capabilities.terminal_usage,
                        "cache_usage": report.capabilities.cache_usage,
                        "reasoning_usage": report.capabilities.reasoning_usage,
                    },
                }
                for report in reports
            ],
            "gmail": {
                "connected": bool(profile.get("refresh_token")),
                "email": profile.get("email"),
            },
            "apollo": {"configured": bool(app.state.apollo_key)},
            "workspace": app.state.workspace_runtime.diagnostics(),
        }

    @app.post("/v1/settings/persona")
    async def set_persona(request: Request):
        payload = await request.json()
        pid = str(payload.get("id") or "").strip()
        try:
            nxt = load_persona(pid)
            _effective_tool_catalog(nxt)
        except (ManifestError, ToolCatalogError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        app.state.store.set_setting("persona", nxt.id)
        app.state.persona = nxt
        return {"persona": {"id": nxt.id, "name": nxt.name}}

    @app.get("/v1/workspaces")
    def workspace_list():
        return app.state.workspace_runtime.diagnostics()

    @app.post("/v1/workspaces", status_code=201)
    async def workspace_create(request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid workspace request"}, status_code=400)
        try:
            grant = app.state.workspace_runtime.add_grant(
                str(payload.get("path") or ""),
                label=str(payload.get("label") or "Workspace"),
                access=str(payload.get("access") or "read_only"),
                allow_shell=bool(payload.get("allow_shell")),
                request_id=(
                    str(payload.get("request_id"))
                    if payload.get("request_id")
                    else None
                ),
            )
        except (GrantUnavailable, WorkspacePathError, KeyError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"grant": grant}

    @app.get("/v1/workspaces/receipts")
    def workspace_receipts(limit: int = 100):
        return {"receipts": app.state.workspace_runtime.audit.list(limit=limit)}

    @app.get("/v1/workspaces/host-approvals")
    def workspace_host_approvals():
        return {
            "approvals": app.state.workspace_runtime.shell.approvals.list_all()
        }

    @app.delete("/v1/workspaces/host-approvals/{approval_id}")
    def workspace_host_approval_revoke(approval_id: str):
        try:
            approval = app.state.workspace_runtime.shell.approvals.revoke(
                approval_id
            )
        except KeyError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"approval": approval}

    @app.patch("/v1/workspaces/{grant_id}")
    async def workspace_update(grant_id: str, request: Request):
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid workspace request"}, status_code=400)
        changes = {
            key: payload[key]
            for key in ("label", "access", "allow_shell", "path")
            if key in payload
        }
        try:
            grant = app.state.workspace_runtime.update_grant(
                grant_id, **changes
            )
        except (GrantUnavailable, WorkspacePathError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"grant": grant}

    @app.delete("/v1/workspaces/{grant_id}")
    def workspace_revoke(grant_id: str):
        try:
            grant = app.state.workspace_runtime.revoke_grant(grant_id)
        except (GrantUnavailable, WorkspacePathError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return {"grant": grant}

    @app.post("/v1/workspaces/tasks/{task_id}/cancel")
    def workspace_task_cancel(task_id: str):
        try:
            task = app.state.workspace_runtime.shell.kill(task_id)
        except KeyError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"task": task}

    async def _reap_and_publish_expired() -> None:
        """Persist and broadcast receipts for freshly reaped approvals."""
        expired = store.reap_overdue_inbox()
        receipts = [
            receipt
            for receipt in (_persist_approval_receipt(item) for item in expired)
            if receipt is not None
        ]
        if receipts:
            await _publish_live_events(receipts)

    @app.get("/v1/inbox")
    async def inbox_list():
        await _reap_and_publish_expired()
        return {"items": app.state.inbox.pending()}

    @app.post("/v1/inbox/{item_id}")
    async def inbox_resolve(item_id: str, request: Request):
        payload = await request.json()
        decision = str(payload.get("decision") or "").strip().lower()
        actor = str(payload.get("actor") or "operator").strip() or "operator"
        scope = str(payload.get("scope") or "once").strip() or "once"
        item = app.state.inbox.get(item_id)
        if item is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        claimant = f"http:{secrets.token_hex(16)}"
        if item.get("kind") == "recovery_approval":
            resolved = await _resolve_recovery_approval(
                item_id,
                decision,
                actor=actor,
                scope=scope,
                claimant=claimant,
            )
            if resolved is None:
                return JSONResponse(
                    {"error": "resolved elsewhere"}, status_code=409
                )
            claim, receipt, ok, result = resolved
            response = {"ok": ok, "item": receipt, "result": result}
            if not claim.decision_recorded:
                response["idempotent"] = True
            return response
        claim = app.state.inbox.decide_and_claim(
            item_id,
            decision,
            actor=actor,
            scope=scope,
            claimant=claimant,
        )
        if claim is None:
            return JSONResponse({"error": "resolved elsewhere"}, status_code=409)
        if decision == "deny":
            receipt = claim.item
            if claim.decision_recorded:
                _persist_approval_receipt(receipt)
        elif claim.claimed and claim.owned:
            receipt = await _execute_claimed_approval(claim.item, claimant=claimant)
        else:
            receipt = await app.state.inbox.wait_for_execution(item_id)
        if receipt is None:
            return JSONResponse({"error": "execution unavailable"}, status_code=409)
        if receipt.get("execution_status") in ("executing", "pending"):
            # Wait deadline hit while a live claimant still runs: recoverable,
            # not an error and not a fabricated outcome. Retry the POST later.
            return JSONResponse(
                {"ok": False, "pending": True, "item": receipt},
                status_code=202,
            )
        if receipt.get("execution_status") in ("interrupted", "expired"):
            approval = _persist_approval_receipt(receipt)
            if approval is not None:
                await _publish_live_events([approval])
        ok, result = app.state.inbox.execution_outcome(receipt)
        response = {"ok": ok, "item": receipt, "result": result}
        if not claim.decision_recorded:
            response["idempotent"] = True
        return response

    @app.post("/v1/schedule/tick")
    def schedule_tick():
        runs = app.state.scheduler.tick()
        return {"runs": runs}

    @app.post("/v1/schedule", status_code=201)
    async def schedule_create(request: Request):
        payload = await request.json()
        template_id = str(payload.get("template_id") or "").strip()
        cadence = str(payload.get("cadence") or "").strip()
        name = str(payload.get("name") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        template_ids = {str(item["id"]) for item in ROUTINE_TEMPLATES}
        fields: dict[str, str] = {}
        if template_id not in template_ids:
            fields["template_id"] = "Choose a supported template."
        if cadence not in SUPPORTED_CADENCES:
            fields["cadence"] = "Choose a supported cadence."
        if not name or len(name) > 80:
            fields["name"] = "Enter a name up to 80 characters."
        if not prompt or len(prompt) > 2000:
            fields["prompt"] = "Enter instructions up to 2,000 characters."
        if fields:
            return JSONResponse(
                {"error": "invalid_routine", "fields": fields}, status_code=400
            )
        job = app.state.store.add_job(
            SUPPORTED_CADENCES[cadence],
            prompt,
            next_run_at=next_monday_0900(now_iso()),
            name=name,
            template_id=template_id,
            cadence=cadence,
        )
        return {"job": job}

    @app.post("/v1/schedule/{job_id}/run")
    def schedule_run(job_id: int):
        try:
            run = app.state.scheduler.run_job(job_id)
        except KeyError:
            return JSONResponse({"error": "not found"}, status_code=404)
        except RuntimeError:
            return JSONResponse(
                {
                    "error": "already_running",
                    "message": "This routine is already running. Wait for its current receipt.",
                },
                status_code=409,
            )
        return {"run": run}

    @app.get("/v1/schedule")
    def schedule():
        return {**app.state.store.list_schedule(), "templates": list(ROUTINE_TEMPLATES)}

    @app.get("/v1/connectors")
    def connectors():
        profile = load_google(app.state.secrets)
        email = profile.get("email")
        refresh = bool(profile.get("refresh_token"))
        granola = app.state.secrets.get("mcp-oauth:granola") or {}

        def normalized_connector(
            *,
            connector_id: str,
            title: str,
            description: str,
            status: str,
            catalog_group: str,
            connector_email: str | None,
            required_scopes: list[str],
            missing_scopes: list[str],
            supported_actions: list[str],
            available_actions: list[str],
            authorization_group: str | None = None,
            manual_configuration: bool = False,
        ) -> dict[str, Any]:
            if status == "connected":
                health = {
                    "category": "healthy",
                    "label": "Configured" if manual_configuration else "Ready",
                    "message": "This connection is ready to use.",
                }
                recovery = None
            elif status == "missing_scopes":
                health = {
                    "category": "attention",
                    "label": "Missing permissions",
                    "message": "Additional permission is required before every action can run.",
                }
                recovery = {
                    "category": "grant_scopes",
                    "action_label": "Finish setup",
                    "message": "Reconnect and approve the listed permissions.",
                }
            else:
                health = {
                    "category": "setup_required",
                    "label": "Available",
                    "message": "This connection has not been set up yet.",
                }
                recovery = {
                    "category": "configure" if manual_configuration else "connect",
                    "action_label": "View setup guide" if manual_configuration else "Connect",
                    "message": (
                        "Set APOLLO_API_KEY in the local Sourcecado environment, then restart Sourcecado."
                        if manual_configuration
                        else "Start authorization, then return to this detail page."
                    ),
                }
            return {
                "id": connector_id,
                "title": title,
                "description": description,
                "status": status,
                "catalog_group": catalog_group,
                "email": connector_email,
                "required_scopes": required_scopes,
                "missing_scopes": missing_scopes,
                "health": health,
                "recovery": recovery,
                "supported_actions": supported_actions,
                "available_actions": available_actions,
                "repair_route": f"#/connections/{connector_id}",
                "authorization_group": authorization_group,
            }

        gmail_required = ["Read Gmail messages", "Create Gmail drafts"]
        gmail_missing = list(gmail_required) if not refresh else []
        if refresh and profile.get("scopes"):
            if not has_scope(profile, READ_SCOPE):
                gmail_missing.append("Read Gmail messages")
            if not has_scope(profile, COMPOSE_SCOPE):
                gmail_missing.append("Create Gmail drafts")
        gmail_status = (
            "available" if not refresh else "missing_scopes" if gmail_missing else "connected"
        )
        drive_missing = [] if refresh and has_scope(profile, DRIVE_SCOPE) else ["Read Drive files"]
        drive_status = (
            "available" if not refresh else "missing_scopes" if drive_missing else "connected"
        )
        calendar_missing = (
            [] if refresh and has_calendar_access(profile) else ["View and update calendar events"]
        )
        calendar_status = (
            "available"
            if not refresh
            else "missing_scopes"
            if calendar_missing
            else "connected"
        )
        granola_connected = bool(granola.get("access_token") or granola.get("refresh_token"))
        apollo_configured = bool(app.state.apollo_key)
        return {
            "connectors": [
                normalized_connector(
                    connector_id="gmail",
                    title="Gmail",
                    description="Search email and create review-only drafts.",
                    status=gmail_status,
                    catalog_group="connected" if refresh else "available",
                    connector_email=email if refresh else None,
                    required_scopes=gmail_required,
                    missing_scopes=gmail_missing,
                    supported_actions=["Search and read email", "Create drafts for review"],
                    available_actions=(
                        ["connect"]
                        if not refresh
                        else ["reconnect", "disconnect"]
                        if gmail_status == "missing_scopes"
                        else ["disconnect"]
                    ),
                    authorization_group="google",
                ),
                normalized_connector(
                    connector_id="drive",
                    title="Google Drive",
                    description="Search and read files from the connected Google account.",
                    status=drive_status,
                    catalog_group="connected" if refresh else "available",
                    connector_email=email if refresh else None,
                    required_scopes=["Read Drive files"],
                    missing_scopes=drive_missing if refresh else ["Read Drive files"],
                    supported_actions=["Search files", "List folders", "Read files"],
                    available_actions=(
                        ["connect"]
                        if not refresh
                        else ["reconnect", "disconnect"]
                        if drive_status == "missing_scopes"
                        else ["disconnect"]
                    ),
                    authorization_group="google",
                ),
                normalized_connector(
                    connector_id="calendar",
                    title="Google Calendar",
                    description="Review, create, and update calendar events.",
                    status=calendar_status,
                    catalog_group="connected" if refresh else "available",
                    connector_email=email if refresh else None,
                    required_scopes=["View and update calendar events"],
                    missing_scopes=(
                        calendar_missing if refresh else ["View and update calendar events"]
                    ),
                    supported_actions=["List events", "Create and update events"],
                    available_actions=(
                        ["connect"]
                        if not refresh
                        else ["reconnect", "disconnect"]
                        if calendar_status == "missing_scopes"
                        else ["disconnect"]
                    ),
                    authorization_group="google",
                ),
                normalized_connector(
                    connector_id="apollo",
                    title="Apollo",
                    description="Search people and enrich sourcing records.",
                    status="connected" if apollo_configured else "available",
                    catalog_group="connected" if apollo_configured else "available",
                    connector_email=None,
                    required_scopes=[],
                    missing_scopes=[],
                    supported_actions=["Search people", "Enrich contacts"],
                    available_actions=["view_guidance"],
                    manual_configuration=True,
                ),
                normalized_connector(
                    connector_id="granola",
                    title="Granola",
                    description="Search and read meeting context.",
                    status="connected" if granola_connected else "available",
                    catalog_group="connected" if granola_connected else "available",
                    connector_email=None,
                    required_scopes=[],
                    missing_scopes=[],
                    supported_actions=["Search meeting notes", "Read meeting context"],
                    available_actions=["disconnect"] if granola_connected else ["connect"],
                    authorization_group="granola",
                ),
            ]
        }

    def _google_extra_connect(extra: str) -> dict[str, Any]:
        client_id, client_secret = google_client_credentials()
        if not client_id or not client_secret:
            return JSONResponse(
                {
                    "error": "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are not set in ~/.config/club/.env"
                },
                status_code=400,
            )
        state = secrets.token_urlsafe(16)
        app.state.oauth_state = state
        redirect = f"{app.state.public_url.rstrip('/')}/v1/gmail/callback"
        url = authorization_url(
            client_id=client_id, redirect_uri=redirect, state=state, extra_scopes=(extra,)
        )
        opened = bool(app.state.browser_opener(url))
        return {"url": url, "opened": opened, "redirect_uri": redirect}

    @app.post("/v1/connectors/drive/connect")
    def drive_connect():
        return _google_extra_connect(DRIVE_SCOPE)

    @app.post("/v1/connectors/calendar/connect")
    def calendar_connect():
        return _google_extra_connect(CALENDAR_SCOPE)

    @app.post("/v1/connectors/granola/connect")
    def granola_connect():
        try:
            return app.state.mcp_oauth.start("granola")
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/v1/connectors/granola/disconnect")
    def granola_disconnect():
        app.state.secrets.delete("mcp-oauth:granola")
        return {"connected": False, "disconnected": ["granola"]}

    @app.get("/v1/mcp/oauth/callback")
    def mcp_oauth_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(
                f"<p>Granola connect failed: {html.escape(error)}</p>", status_code=400
            )
        if not code or not state:
            return HTMLResponse("<p>Granola connect failed: missing code.</p>", status_code=400)
        try:
            app.state.mcp_oauth.finish(code=code, state=state)
        except Exception as exc:
            return HTMLResponse(
                f"<p>Granola connect failed: {html.escape(str(exc))}</p>", status_code=400
            )
        return HTMLResponse("<p>Granola connected. You can close this tab.</p>")

    @app.get("/v1/gmail")
    def gmail_status():
        profile = load_google(app.state.secrets)
        return {
            "connected": bool(profile.get("refresh_token")),
            "email": profile.get("email"),
        }

    @app.post("/v1/gmail/connect")
    def gmail_connect():
        client_id, client_secret = google_client_credentials()
        if not client_id or not client_secret:
            return JSONResponse(
                {
                    "error": "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are not set in ~/.config/club/.env"
                },
                status_code=400,
            )
        state = secrets.token_urlsafe(16)
        app.state.oauth_state = state
        redirect = f"{app.state.public_url.rstrip('/')}/v1/gmail/callback"
        url = authorization_url(client_id=client_id, redirect_uri=redirect, state=state)
        opened = bool(app.state.browser_opener(url))
        return {"url": url, "opened": opened, "redirect_uri": redirect}

    @app.post("/v1/gmail/disconnect")
    def gmail_disconnect():
        app.state.secrets.delete(GMAIL_KEY)
        app.state.secrets.delete(GOOGLE_KEY)
        app.state.gmail = gmail_from_secrets(app.state.secrets, http=app.state.http)
        app.state.drive = drive_from_secrets(app.state.secrets, http=app.state.http)
        app.state.calendar = calendar_from_secrets(app.state.secrets, http=app.state.http)
        return {
            "connected": False,
            "email": None,
            "disconnected": ["gmail", "drive", "calendar"],
        }

    @app.get("/v1/gmail/callback")
    def gmail_callback(code: str = "", state: str = "", error: str = ""):
        if error:
            return HTMLResponse(
                f"<p>Gmail connect failed: {html.escape(error)}</p>", status_code=400
            )
        if not code or state != app.state.oauth_state or not app.state.oauth_state:
            return HTMLResponse("<p>Gmail connect failed: bad state.</p>", status_code=400)
        client_id, client_secret = google_client_credentials()
        redirect = f"{app.state.public_url.rstrip('/')}/v1/gmail/callback"
        try:
            from coworker.apollo import LiveHttp

            http = app.state.http or LiveHttp()
            tokens = exchange_code(
                http,
                client_id=client_id,
                client_secret=client_secret,
                code=code,
                redirect_uri=redirect,
            )
            access = str(tokens.get("access_token") or "")
            refresh = str(tokens.get("refresh_token") or "")
            email = None
            if access:
                try:
                    info = http.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access}"},
                    )
                    email = (info or {}).get("email") if isinstance(info, dict) else None
                except Exception:
                    email = None
            if not refresh:
                return HTMLResponse("<p>Gmail connect failed: no refresh token.</p>", status_code=400)
            existing = load_google(app.state.secrets)
            save_google(
                app.state.secrets,
                {
                    "refresh_token": refresh,
                    "access_token": access,
                    "email": email,
                    "scopes": merge_scopes(existing.get("scopes"), tokens.get("scope")),
                },
            )
            app.state.gmail = gmail_from_secrets(app.state.secrets, http=app.state.http)
            app.state.drive = drive_from_secrets(app.state.secrets, http=app.state.http)
            app.state.calendar = calendar_from_secrets(app.state.secrets, http=app.state.http)
            app.state.oauth_state = ""
        except Exception as exc:
            return HTMLResponse(
                f"<p>Gmail connect failed: {html.escape(str(exc))}</p>", status_code=400
            )
        return HTMLResponse("<p>Gmail connected. You can close this tab and go back to Sourcecado.</p>")

    @app.get("/v1/board")
    def board_get():
        return app.state.people.list_board()

    @app.post("/v1/replies/refresh")
    def replies_refresh():
        """Read inbound Gmail since the cursor and file what it can attribute.

        Read and filing only. The reader handed to the refresh exposes four
        Gmail read calls, so nothing on this path can enrich, draft, or send.
        """
        result = refresh_replies(app.state.people, InboundReader(app.state.gmail))
        return {"refresh": result, "board": app.state.people.list_board()}

    @app.post("/v1/apollo/curate")
    async def apollo_curate(request: Request):
        payload = await request.json()
        session_id = str(payload.get("session_id") or "").strip()
        if not valid_session_id(session_id) or app.state.store.index(session_id) is None:
            return JSONResponse({"error": "session not found"}, status_code=404)
        rows = payload.get("people")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return JSONResponse({"error": "people must be a list of rows"}, status_code=400)
        try:
            result = curate_apollo_candidates(
                app.state.people,
                rows,
                target=str(payload.get("target") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        binding_reason = (
            "multiple_selection"
            if result["selected_row_count"] > 1
            else "unbound"
        )
        if (
            payload.get("bind_original") is True
            and result["selected_row_count"] == 1
            and len(result["kept"]) == 1
        ):
            person_id = str(result["kept"][0]["person_id"])
            try:
                app.state.people.bind_session(session_id, person_id)
            except ValueError:
                binding_reason = "existing_person_chat"
            else:
                binding_reason = "single_selection"

        kept = []
        for item in result["kept"]:
            person_session = app.state.people.session_for_person(item["person_id"])
            kept.append(
                {
                    **item,
                    "sourcing_chat": (
                        {"session_id": person_session}
                        if person_session is not None
                        else None
                    ),
                }
            )
        bound_person_id = app.state.people.person_for_session(session_id)
        if bound_person_id is not None and binding_reason in {
            "multiple_selection",
            "unbound",
        }:
            binding_reason = "already_bound"
        result["kept"] = kept
        result["original_session"] = {
            "session_id": session_id,
            "bound_person_id": bound_person_id,
            "reason": binding_reason,
        }
        return result

    @app.post("/v1/people/{person_id}/sequence")
    async def people_sequence(person_id: str, request: Request):
        payload = await request.json()
        state = str(payload.get("state") or "")
        actor = str(payload.get("actor") or "director")
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            person = app.state.people.set_sequence(person_id, state, actor=actor)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"person": person}

    @app.get("/v1/people/{person_id}")
    def people_get(person_id: str):
        person = app.state.people.get(person_id, expand_sources=True)
        if person is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        timeline = [_public_event(event) for event in app.state.people.timeline(person_id)]
        try:
            session_id = app.state.people.session_for_person(person_id)
        except ValueError:
            return JSONResponse(
                {
                    "error": "person has multiple bound sourcing sessions",
                    "code": "person_chat_conflict",
                },
                status_code=409,
            )
        return {
            "person": person,
            "brief": build_brief(person, timeline),
            "timeline": timeline,
            "versions": app.state.people.versions(person_id),
            "meeting_evidence": app.state.meeting_evidence.for_person(person_id),
            "sourcing_chat": (
                {"session_id": session_id, "person_id": person_id}
                if session_id is not None
                else None
            ),
        }

    def _granola_meetings() -> dict[str, Any]:
        if app.state.mcp is None or not app.state.mcp.has(
            "mcp__granola__list_meetings"
        ):
            raise RuntimeError("Granola is unavailable")
        result = app.state.mcp.call("mcp__granola__list_meetings", {})
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError("Granola is unavailable")
        if isinstance(result.get("meetings"), list):
            return {"meetings": result["meetings"]}
        nested = result.get("result")
        if isinstance(nested, dict) and isinstance(nested.get("meetings"), list):
            return {"meetings": nested["meetings"]}
        if isinstance(nested, str):
            try:
                decoded = json.loads(nested)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Granola returned malformed meetings") from exc
            if isinstance(decoded, dict) and isinstance(decoded.get("meetings"), list):
                return {"meetings": decoded["meetings"]}
        raise RuntimeError("Granola returned malformed meetings")

    def _meeting_view(person_id: str) -> dict[str, Any]:
        person = app.state.people.get(person_id)
        assert person is not None
        timeline = app.state.people.timeline(person_id)
        return {
            "meeting_evidence": app.state.meeting_evidence.for_person(person_id),
            "brief": build_brief(person, timeline),
        }

    @app.post("/v1/people/{person_id}/meetings/refresh")
    def people_meetings_refresh(person_id: str):
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)

        def calendar_fetch():
            if app.state.calendar is None:
                raise RuntimeError("Calendar is unavailable")
            return app.state.calendar.list_events(max_results=100)

        result = app.state.meeting_evidence.refresh(
            calendar_fetch=calendar_fetch,
            granola_fetch=_granola_meetings,
        )
        return {**result, **_meeting_view(person_id)}

    @app.post("/v1/people/{person_id}/meetings/{evidence_id}/attach")
    def people_meetings_attach(person_id: str, evidence_id: str):
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            meeting = app.state.meeting_evidence.attach(evidence_id, person_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"meeting": meeting, **_meeting_view(person_id)}

    @app.post("/v1/people/{person_id}/meetings/{evidence_id}/reject")
    def people_meetings_reject(person_id: str, evidence_id: str):
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            meeting = app.state.meeting_evidence.reject(evidence_id, person_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"meeting": meeting, **_meeting_view(person_id)}

    @app.post("/v1/people/{person_id}/drive-evidence/search")
    async def people_drive_evidence_search(person_id: str, request: Request):
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if app.state.drive is None:
            return JSONResponse({"error": "Drive is not connected"}, status_code=400)
        body = await request.json()
        query = str(body.get("query") or "").strip()
        if not query:
            return JSONResponse({"error": "query is required"}, status_code=400)
        try:
            result = app.state.drive.search(
                query, max_results=int(body.get("max_results") or 10)
            )
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return result

    @app.post("/v1/people/{person_id}/drive-evidence/attach")
    async def people_drive_evidence_attach(person_id: str, request: Request):
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if app.state.drive is None:
            return JSONResponse({"error": "Drive is not connected"}, status_code=400)
        body = await request.json()
        kind = str(body.get("kind") or "")
        file_id = str(body.get("file_id") or "").strip()
        folder_id = str(body.get("folder_id") or "").strip() or None
        if not file_id:
            return JSONResponse({"error": "file_id is required"}, status_code=400)
        try:
            raw = app.state.drive.read(file_id)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        try:
            source = attach_drive_evidence(
                app.state.people,
                person_id,
                kind=kind,
                raw=raw,
                folder_id=folder_id,
                actor="director",
                rationale_summary="Director attached Drive evidence.",
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {
            "source": source,
            "person": app.state.people.get(person_id, expand_sources=True),
        }

    @app.post("/v1/people/{person_id}/sourcing-chat")
    async def people_sourcing_chat(person_id: str, request: Request):
        person = app.state.people.get(person_id)
        if person is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        payload = await request.json()
        expected_version = payload.get("expected_person_version")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            return JSONResponse(
                {"error": "expected_person_version is required"}, status_code=400
            )
        if int(person["version"]) != expected_version:
            return JSONResponse({"error": "stale person version"}, status_code=409)
        requested_session_id = str(payload.get("session_id") or "").strip()
        if requested_session_id:
            owner = app.state.people.person_for_session(requested_session_id)
            if owner != person_id:
                return JSONResponse(
                    {
                        "error": (
                            "session is already bound to another person"
                            if owner is not None
                            else "session is not bound to this person"
                        )
                    },
                    status_code=409,
                )
        brief = build_brief(person, app.state.people.timeline(person_id))
        label = str(brief["who"] or "Person")
        try:
            existing_session_id = app.state.people.session_for_person(person_id)
        except ValueError:
            return JSONResponse(
                {
                    "error": "person has multiple bound sourcing sessions",
                    "code": "person_chat_conflict",
                },
                status_code=409,
            )
        if existing_session_id is not None:
            existing = app.state.store.index(existing_session_id)
            if existing is None:
                return JSONResponse(
                    {"error": "bound sourcing session is unavailable"},
                    status_code=409,
                )
            app.state.store.set_open_session(existing_session_id)
            return {
                "created": False,
                "session": {
                    "id": existing_session_id,
                    "title": existing["title"],
                    "n_msgs": existing["n_msgs"],
                },
                "active_person": {
                    "person_id": person_id,
                    "version": int(person["version"]),
                    "label": label,
                },
            }
        row = app.state.store.create_session()
        session_id = str(row["session_id"])
        try:
            app.state.people.bind_session(
                session_id,
                person_id,
                expected_person_version=expected_version,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        titled = app.state.store.rename_session(session_id, f"Sourcing · {label}")
        app.state.store.set_open_session(session_id)
        return JSONResponse(
            {
                "created": True,
                "session": {
                    "id": session_id,
                    "title": titled["title"] if titled is not None else None,
                    "n_msgs": 0,
                },
                "active_person": {
                    "person_id": person_id,
                    "version": int(person["version"]),
                    "label": label,
                },
            },
            status_code=201,
        )

    def _bound_sourcing_session(
        person_id: str, payload: dict[str, Any]
    ) -> tuple[str | None, JSONResponse | None]:
        """A costly step starts from this person's own sourcing chat, or not at all."""
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return None, JSONResponse(
                {"error": "session_id is required", "code": "unbound_session"},
                status_code=400,
            )
        owner = app.state.people.person_for_session(session_id)
        if owner != person_id:
            return None, JSONResponse(
                {
                    "error": (
                        "This chat is bound to a different person."
                        if owner is not None
                        else "This chat is not bound to this person."
                    ),
                    "code": "unbound_session",
                },
                status_code=409,
            )
        return session_id, None

    @app.post("/v1/people/{person_id}/outreach/draft", status_code=201)
    async def people_outreach_draft(person_id: str, request: Request):
        """Draft outreach for a bound person. The recipient is never guessed.

        The address comes from the person file, not from the request and not
        from the model. Creating a draft sends nothing.
        """
        person = app.state.people.get(person_id)
        if person is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        payload = await request.json()
        session_id, refusal = _bound_sourcing_session(person_id, payload)
        if refusal is not None:
            return refusal
        to = str(person.get("email") or "").strip()
        if not to:
            return JSONResponse(
                {
                    "error": "This person file has no email address yet.",
                    "code": "no_recipient",
                },
                status_code=409,
            )
        subject = str(payload.get("subject") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not subject or not body:
            return JSONResponse(
                {"error": "subject and body are required"}, status_code=400
            )
        try:
            created = app.state.gmail.create_draft(to=to, subject=subject, body=body)
        except GmailError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        draft_id = str(created.get("id") or "")
        try:
            snapshot = draft_snapshot(app.state.gmail, draft_id=draft_id)
        except SendAuthorityError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return {
            "draft": {
                "id": draft_id,
                "to": snapshot["to"],
                "subject": snapshot["subject"],
                "body": snapshot["body"],
                "body_digest": snapshot["body_digest"],
                "account": snapshot["account"],
                "sent": False,
            },
            "person_id": person_id,
            "session_id": session_id,
        }

    @app.get("/v1/people/{person_id}/outreach/draft/{draft_id}")
    def people_outreach_draft_read(person_id: str, draft_id: str):
        """Re-read the live draft so the director reviews what Gmail holds now."""
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            snapshot = draft_snapshot(app.state.gmail, draft_id=draft_id)
        except SendAuthorityError as exc:
            return JSONResponse(
                {"error": str(exc), "code": exc.code}, status_code=404
            )
        return {
            "draft": {
                "id": snapshot["draft_id"],
                "to": snapshot["to"],
                "subject": snapshot["subject"],
                "body": snapshot["body"],
                "body_digest": snapshot["body_digest"],
                "account": snapshot["account"],
                # Reaching this line means Gmail still holds the draft, and
                # Gmail only holds drafts that have not been sent.
                "sent": False,
            }
        }

    @app.post("/v1/people/{person_id}/outreach/send-approval", status_code=201)
    async def people_outreach_send_approval(person_id: str, request: Request):
        """Park one send approval bound to one reviewed draft version.

        Nothing is sent here. This records what the director is being asked to
        authorize: the account, the draft, the recipient, the subject, and the
        digest of the body version they read. POST /v1/inbox/{id} decides it.
        """
        person = app.state.people.get(person_id)
        if person is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        payload = await request.json()
        session_id, refusal = _bound_sourcing_session(person_id, payload)
        if refusal is not None:
            return refusal
        draft_id = str(payload.get("draft_id") or "").strip()
        reviewed = str(payload.get("reviewed_body_digest") or "").strip()
        if not draft_id or not reviewed:
            return JSONResponse(
                {"error": "draft_id and reviewed_body_digest are required"},
                status_code=400,
            )
        approval_id = f"send_{secrets.token_hex(8)}"
        try:
            authority = authority_for_draft(
                app.state.gmail,
                approval_id=approval_id,
                person_id=person_id,
                draft_id=draft_id,
                reviewed_body_digest=reviewed,
            )
        except SendAuthorityError as exc:
            return JSONResponse(
                {"error": str(exc), "code": exc.code}, status_code=409
            )
        expected = str(person.get("email") or "").strip().casefold()
        if authority.to.strip().casefold() != expected:
            return JSONResponse(
                {
                    "error": "This draft is addressed to someone other than this person.",
                    "code": "recipient_not_bound",
                },
                status_code=409,
            )
        parked = app.state.inbox.park(
            "gmail_send",
            {"draft_id": draft_id},
            item_id=approval_id,
            reason="Sending this email reaches a real person. Allow or Deny.",
            session_id=session_id,
            run_id=str(payload.get("run_id") or "").strip() or None,
            resource=authority.as_resource(),
        )
        return {"item": parked}

    @app.post("/v1/people/{person_id}/enrich-approval", status_code=201)
    async def people_enrich_approval(person_id: str, request: Request):
        """Park one Apollo enrichment approval naming the person and the spend."""
        person = app.state.people.get(person_id)
        if person is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        payload = await request.json()
        session_id, refusal = _bound_sourcing_session(person_id, payload)
        if refusal is not None:
            return refusal
        if not app.state.apollo_key:
            return JSONResponse({"error": APOLLO_MISSING_KEY}, status_code=409)
        match = enrichment_match(person)
        if match is None:
            return JSONResponse(
                {
                    "error": "This person file has no email and no full name to match on.",
                    "code": "no_match_key",
                },
                status_code=409,
            )
        resource = enrichment_resource(person, match)
        parked = app.state.inbox.park(
            "apollo_enrich_contact",
            {"person_id": person_id},
            item_id=f"enrich_{secrets.token_hex(8)}",
            reason=str(resource["reason"]),
            session_id=session_id,
            run_id=str(payload.get("run_id") or "").strip() or None,
            resource=resource,
        )
        return {"item": parked}

    @app.post("/v1/people/{person_id}/revert")
    async def people_revert(person_id: str, request: Request):
        payload = await request.json()
        if app.state.people.get(person_id) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            person = app.state.people.revert(
                person_id,
                to_version=int(payload.get("to_version") or 0),
                expected_version=int(payload.get("expected_version") or 0),
                actor="director",
                rationale_summary=str(payload.get("rationale_summary") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"person": person}

    @app.get("/v1/sessions")
    def sessions_list():
        store = app.state.store
        return {
            "sessions": store.list_sessions(),
            "open_id": store.open_session_id(),
            "last_destination": store.get_setting("last_destination"),
        }

    @app.patch("/v1/navigation")
    async def navigation_patch(request: Request):
        destination = str((await request.json()).get("destination") or "")
        app.state.store.set_setting("last_destination", destination)
        return {"destination": destination}

    @app.post("/v1/sessions")
    def sessions_create():
        store = app.state.store
        row = store.create_session()
        store.set_open_session(row["session_id"])
        return {"id": row["session_id"], "title": row["title"], "n_msgs": row["n_msgs"]}

    @app.get("/v1/sessions/{sid}")
    async def sessions_get(sid: str, request: Request):
        store = app.state.store
        row = store.index(sid)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        bound_person_id = app.state.people.person_for_session(sid)
        expected_person_id = str(
            request.query_params.get("expected_person_id") or ""
        ).strip()
        if expected_person_id and bound_person_id != expected_person_id:
            return JSONResponse(
                {
                    "error": "conversation is bound to a different person",
                    "code": "person_binding_mismatch",
                },
                status_code=409,
            )
        bound_person = (
            app.state.people.get(bound_person_id) if bound_person_id is not None else None
        )
        if bound_person_id is not None and bound_person is None:
            return JSONResponse(
                {
                    "error": "bound person file is unavailable",
                    "code": "bound_person_unavailable",
                },
                status_code=409,
            )
        # A restored thread must not show a live-looking card for an
        # already-expired approval: reap and persist receipts first.
        await _reap_and_publish_expired()
        if not sid.startswith("sched-"):
            store.set_open_session(sid)
        active_person = None
        if bound_person is not None:
            bound_brief = build_brief(
                bound_person, app.state.people.timeline(bound_person_id)
            )
            active_person = {
                "person_id": bound_person_id,
                "version": int(bound_person["version"]),
                "label": str(bound_brief["who"] or "Person"),
            }
        return {
            "id": sid,
            "title": row["title"],
            "messages": store.load(sid),
            "events": store.load_events(sid),
            "queue": store.list_queue(sid),
            "queue_paused": store.queue_paused(sid),
            "active_person": active_person,
        }

    @app.get("/v1/sessions/{sid}/telemetry/current")
    def session_current_telemetry(sid: str):
        if app.state.store.index(sid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        query = getattr(app.state.telemetry_adapter, "current_session_metrics", None)
        metrics = (
            query(session_id=sid, now_ns=time.monotonic_ns())
            if callable(query)
            else None
        )
        current_run = None
        if metrics is not None:
            current_run = {
                "run_id": metrics.run_id,
                "status": metrics.status,
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
                "total_tokens": metrics.total_tokens,
                "cache_hit_input_tokens": metrics.cache_hit_input_tokens,
                "cache_miss_input_tokens": metrics.cache_miss_input_tokens,
                "cache_write_input_tokens": metrics.cache_write_input_tokens,
                "reasoning_tokens": metrics.reasoning_tokens,
                "current_context_tokens": metrics.current_context_tokens,
                "context_window_tokens": metrics.context_window_tokens,
                "context_use_ratio": metrics.context_use_ratio,
                "elapsed_ms": metrics.elapsed_ms,
                "estimated_cost_usd": metrics.estimated_cost_usd,
                "retry_count": metrics.retry_count,
                "compaction_count": metrics.compaction_count,
            }
        return {
            "version": 1,
            "session_id": sid,
            "current_run": current_run,
        }

    @app.get("/v1/sessions/{sid}/prompt/current")
    def session_current_prompt_diagnostics(sid: str):
        if app.state.store.index(sid) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        assembled = system_prompt_assembly(
            app.state.store,
            app.state.persona,
            app.state.skills,
            people=app.state.people,
            session_id=sid,
        )
        return {
            "version": 1,
            "session_id": sid,
            **asdict(assembled.diagnostics),
            "effective_tools": list(_effective_tool_catalog().diagnostics()),
        }

    @app.patch("/v1/sessions/{sid}")
    async def sessions_patch(sid: str, request: Request):
        payload = await request.json()
        row = app.state.store.index(sid)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        if "title" in payload:
            title = str(payload.get("title") or "")
            row = app.state.store.rename_session(sid, title)
            if row is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            app.state.store.set_open_session(sid)
        if "pinned" in payload:
            row = app.state.store.set_session_pinned(sid, bool(payload["pinned"]))
        return {"id": sid, "title": row["title"], "pinned": row["pinned"]}

    @app.get("/v1/conversation")
    def conversation():
        sid = app.state.store.open_session_id() or ""
        if not valid_session_id(sid):
            return {"id": sid, "title": None, "messages": []}
        row = app.state.store.index(sid)
        return {
            "id": sid,
            "title": None if row is None else row["title"],
            "messages": app.state.store.load(sid),
        }

    @app.websocket("/ws/chat")
    async def ws_chat(ws: WebSocket) -> None:
        if not _ws_token_ok(ws, app.state.token) or not _origin_allowed(
            ws.headers.get("origin")
        ):
            await ws.close(code=1008)
            return
        await ws.accept(subprotocol="club")
        store: ConversationStore = app.state.store
        coordinator: RunCoordinator = app.state.run_coordinator
        send_lock = asyncio.Lock()
        tasks: set[asyncio.Task[Any]] = set()
        socket_live = True

        async def _send_locked(event: dict[str, Any]) -> None:
            nonlocal socket_live
            if not socket_live:
                return
            try:
                await ws.send_json(event)
            except Exception:
                # The window went away mid-run. A dead transport must never
                # drive durable state: stop live updates, keep the run going.
                socket_live = False

        async def _send(event: dict[str, Any]) -> None:
            async with send_lock:
                await _send_locked(event)

        async def _broadcast(event: dict[str, Any]) -> None:
            """Deliver one event to every attached socket, not just this one."""
            await _publish_live_events([event])

        def _socket_alive() -> bool:
            return socket_live

        sender_registration = (asyncio.get_running_loop(), _send, _socket_alive)
        app.state.live_event_senders.add(sender_registration)

        def _queue_snapshot(
            session_id: str, *, command_id: str, status: str
        ) -> dict[str, Any]:
            return {
                "version": 2,
                "type": "queue_snapshot",
                "session_id": session_id,
                "command_id": command_id,
                "status": status,
                "paused": store.queue_paused(session_id),
                "items": store.list_queue(session_id),
            }

        def _recovery_event_for_command(
            session_id: str, command_id: str
        ) -> dict[str, Any] | None:
            return next(
                (
                    event
                    for event in reversed(store.load_events(session_id))
                    if event.get("type") == "tool_recovery"
                    and event.get("command_id") == command_id
                ),
                None,
            )

        def _failed_step(
            session_id: str, run_id: str, call_id: str
        ) -> tuple[dict[str, Any], dict[str, Any]] | None:
            events = store.load_events(session_id)
            failed = next(
                (
                    event
                    for event in reversed(events)
                    if event.get("type") == "tool_finished"
                    and event.get("run_id") == run_id
                    and event.get("id") == call_id
                    and event.get("ok") is False
                    and isinstance(event.get("failure"), dict)
                ),
                None,
            )
            started = next(
                (
                    event
                    for event in reversed(events)
                    if event.get("type") in {"tool_started", "permission_required"}
                    and event.get("run_id") == run_id
                    and event.get("id") == call_id
                    and isinstance(event.get("arguments"), dict)
                ),
                None,
            )
            if failed is None or started is None:
                return None
            return failed, started

        async def _persist_and_send(event: dict[str, Any]) -> None:
            store.append_event(str(event["session_id"]), event)
            await _broadcast(event)

        async def _retry_failed_step(command: dict[str, Any]) -> None:
            session_id = str(command.get("session_id") or "")
            run_id = str(command.get("run_id") or "")
            call_id = str(command.get("call_id") or "")
            command_id = str(command.get("command_id") or "")
            if not all((session_id, run_id, call_id, command_id)):
                return
            existing = _recovery_event_for_command(session_id, command_id)
            if existing is not None:
                await _send(existing)
                return
            if not store.claim_recovery_command(session_id, command_id):
                # Another socket owns this command (S3); its outcome will
                # broadcast to every window when the work finishes.
                return
            context = _failed_step(session_id, run_id, call_id)
            if context is None:
                return
            failed, started = context
            failure = dict(failed["failure"])
            identity = TurnIdentity(
                session_id=session_id,
                run_id=run_id,
                message_id=str(failed["message_id"]),
                part_id=str(failed["part_id"]),
            )
            name = str(failed.get("name") or started.get("name") or "tool")
            arguments = dict(started["arguments"])
            if not failure.get("retry_safe"):
                approval_id = f"recovery_{secrets.token_hex(8)}"
                raw_arguments = arguments
                if app.state.workspace_runtime.owns_tool(name):
                    try:
                        raw_arguments = (
                            app.state.workspace_runtime.restore_parked_arguments(
                                call_id, arguments
                            )
                        )
                        arguments = app.state.workspace_runtime.park_arguments(
                            approval_id, name, raw_arguments
                        )
                    except WorkspacePathError as exc:
                        recovery = build_event(
                            identity,
                            "tool_recovery",
                            event_id=f"event_{secrets.token_hex(16)}",
                            command_id=command_id,
                            call_id=call_id,
                            name=name,
                            action="retry",
                            status="failed",
                            outcome=str(exc),
                            failure=failure,
                        )
                        await _persist_and_send(recovery)
                        return
                resource = turn_runtime.approval_resource(
                    name,
                    raw_arguments,
                    app.state.gmail,
                    app.state.workspace_runtime,
                )
                parked = app.state.inbox.park(
                    name,
                    arguments,
                    item_id=approval_id,
                    reason="Retrying this action can create another external change.",
                    session_id=session_id,
                    run_id=run_id,
                    message_id=identity.message_id,
                    part_id=identity.part_id,
                    kind="recovery_approval",
                    recovery_command_id=command_id,
                    original_call_id=call_id,
                    resource=(
                        app.state.workspace_runtime.sanitize_resource(resource)
                        if app.state.workspace_runtime.owns_tool(name)
                        else resource
                    ),
                )
                permission = build_event(
                    identity,
                    "permission_required",
                    event_id=f"event_{secrets.token_hex(16)}",
                    id=approval_id,
                    name=name,
                    arguments=arguments,
                    reason=str(parked.get("reason") or "Retry requires approval."),
                    requested_at=parked["requested_at"],
                    scope=parked["scope"],
                    recovery_command_id=command_id,
                    original_call_id=call_id,
                    failure=failure,
                    **({"resource": resource} if resource else {}),
                )
                await _persist_and_send(permission)
                recovery = build_event(
                    identity,
                    "tool_recovery",
                    event_id=f"event_{secrets.token_hex(16)}",
                    command_id=command_id,
                    call_id=call_id,
                    name=name,
                    action="retry",
                    status="approval_required",
                    outcome="Unsafe retry requires a fresh approval.",
                    failure=failure,
                    approval_id=approval_id,
                )
                await _persist_and_send(recovery)
                return
            recovery_span = app.state.telemetry_recorder.start_span(
                AgentTurnSpan(operation="agent.recovery"),
                TraceContext(session_id=session_id, run_id=run_id),
            )
            recovery_span.record(
                RetryEvent(
                    operation="tool.execute",
                    retry_count=1,
                    reason=RetryReason.TRANSIENT_TOOL,
                )
            )
            try:
                tool_schema = ToolSpan(tool_name=name, operation="tool.execute")
            except ValueError:
                tool_schema = ToolSpan(tool_name="unknown", operation="tool.execute")
            retry_tool_span = recovery_span.child(tool_schema)
            try:
                ok, result = await asyncio.to_thread(
                    turn_runtime.execute,
                    name,
                    arguments,
                    store=store,
                    gmail=app.state.gmail,
                    drive=app.state.drive,
                    drive_ingestions=app.state.drive_ingestions,
                    calendar=app.state.calendar,
                    http=app.state.http,
                    apollo_key=app.state.apollo_key,
                    tavily_key=app.state.tavily_key,
                    skills=app.state.skills,
                    mcp=app.state.mcp,
                    people=app.state.people,
                    workspace_runtime=app.state.workspace_runtime,
                    session_id=session_id,
                )
            except asyncio.CancelledError:
                retry_tool_span.cancel()
                recovery_span.cancel()
                raise
            except Exception as exc:
                # The claim is held: this command must still record an outcome.
                ok, result = False, {"error": str(exc)}
            if ok:
                retry_tool_span.finish(retry_count=1)
                recovery_span.finish(retry_count=1)
            else:
                retry_tool_span.partial(ErrorKind.TOOL, retry_count=1)
                recovery_span.partial(ErrorKind.TOOL, retry_count=1)
            result = dict(result)
            retried_failure = (
                _tool_failure(
                    ToolCall(id=call_id, name=name, arguments=arguments),
                    result,
                    identity,
                )
                if not ok
                else None
            )
            finished = build_event(
                identity,
                "tool_finished",
                event_id=f"event_{secrets.token_hex(16)}",
                id=call_id,
                name=name,
                ok=ok,
                result=result,
                finished_at=datetime.now(UTC).isoformat(),
                recovery_command_id=command_id,
                **({"failure": retried_failure} if retried_failure else {}),
            )
            await _persist_and_send(finished)
            recovery = build_event(
                identity,
                "tool_recovery",
                event_id=f"event_{secrets.token_hex(16)}",
                command_id=command_id,
                call_id=call_id,
                name=name,
                action="retry",
                status="succeeded" if ok else "failed",
                outcome=(
                    "Failed step recovered."
                    if ok
                    else "Retry failed; successful prior work was preserved."
                ),
                failure=retried_failure or failure,
            )
            await _persist_and_send(recovery)

        async def _record_recovery_choice(
            command: dict[str, Any], *, action: str
        ) -> None:
            session_id = str(command.get("session_id") or "")
            run_id = str(command.get("run_id") or "")
            call_id = str(command.get("call_id") or "")
            command_id = str(command.get("command_id") or "")
            if not all((session_id, run_id, call_id, command_id)):
                return
            existing = _recovery_event_for_command(session_id, command_id)
            if existing is not None:
                await _send(existing)
                return
            if not store.claim_recovery_command(session_id, command_id):
                return
            context = _failed_step(session_id, run_id, call_id)
            if context is None:
                return
            failed, _started = context
            failure = dict(failed["failure"])
            identity = TurnIdentity(
                session_id=session_id,
                run_id=run_id,
                message_id=str(failed["message_id"]),
                part_id=str(failed["part_id"]),
            )
            source = str(failure.get("source") or "this source")
            if action == "repair":
                status = "awaiting_repair"
                outcome = f"Opened connection repair for {source}."
            else:
                status = "skipped"
                outcome = (
                    f"Continued without {source}; available work remains partial."
                )
            recovery = build_event(
                identity,
                "tool_recovery",
                event_id=f"event_{secrets.token_hex(16)}",
                command_id=command_id,
                call_id=call_id,
                name=str(failed.get("name") or "tool"),
                action=action,
                status=status,
                outcome=outcome,
                failure=failure,
                repair_route=failure.get("repair_route"),
            )
            await _persist_and_send(recovery)

        async def _launch(
            run_text: str,
            run_sid: str,
            *,
            queue_item_id: str | None = None,
        ) -> RunControl | None:
            identity = new_turn_identity(run_sid)
            control = RunControl(identity)
            if not coordinator.register(control):
                # Another run already owns this session (S2): never fork it.
                return None

            async def _run() -> None:
                async def _wait(call_id: str) -> str:
                    return await _await_permission(
                        call_id, app.state.inbox, control
                    )

                result = await run_turn(
                    text=run_text,
                    sid=run_sid,
                    store=store,
                    provider=app.state.provider,
                    failover_providers=app.state.provider_failovers,
                    persona=app.state.persona,
                    skills=app.state.skills,
                    inbox=app.state.inbox,
                    openai_tools=list(_effective_tool_catalog().schemas),
                    execute_kwargs={
                        "store": store,
                        "gmail": app.state.gmail,
                        "drive": app.state.drive,
                        "drive_ingestions": app.state.drive_ingestions,
                        "calendar": app.state.calendar,
                        "http": app.state.http,
                        "apollo_key": app.state.apollo_key,
                        "tavily_key": app.state.tavily_key,
                        "skills": app.state.skills,
                        "mcp": app.state.mcp,
                        "people": app.state.people,
                        "workspace_runtime": app.state.workspace_runtime,
                    },
                    emit=_broadcast,
                    wait_permission=_wait,
                    system_prompt_fn=system_prompt,
                    identity=control.identity,
                    control=control,
                    telemetry=app.state.telemetry_recorder,
                )
                status = str(result.get("status") or "error")
                if queue_item_id is not None:
                    if status in {"ok", "partial"}:
                        store.finish_queue_item(
                            run_sid, queue_item_id, state="complete"
                        )
                    elif status == "stopped":
                        store.finish_queue_item(
                            run_sid,
                            queue_item_id,
                            state="interrupted",
                            error="Run cancelled before completion.",
                        )
                    else:
                        store.finish_queue_item(
                            run_sid,
                            queue_item_id,
                            state="failed",
                            error="Run failed before completion.",
                        )
                if status == "stopped":
                    store.set_queue_paused(run_sid, True)
                    if store.list_queue(run_sid):
                        await _broadcast(
                            _queue_snapshot(
                                run_sid,
                                command_id=f"terminal:{control.identity.run_id}",
                                status="paused",
                            )
                        )
                    return
                if not any(
                    alive()
                    for _, _, alive in tuple(app.state.live_event_senders)
                ):
                    # No window is watching any more. Park undelivered items
                    # instead of consuming them headless.
                    if store.mark_queue_offline(run_sid):
                        await _publish_live_events(
                            [
                                _queue_snapshot(
                                    run_sid,
                                    command_id=(
                                        f"offline:{control.identity.run_id}"
                                    ),
                                    status="offline",
                                )
                            ]
                        )
                    return
                claimed = store.claim_next_queue(run_sid)
                launched: RunControl | None = None
                if claimed is not None:
                    # Launch before any await so no chat frame can slip into
                    # the claim->launch window and fork the session (S2).
                    launched = await _launch(
                        str(claimed["text"]),
                        run_sid,
                        queue_item_id=str(claimed["id"]),
                    )
                    if launched is None:
                        # Another run won the session between claim and
                        # launch; hand the item back to the queue.
                        store.finish_queue_item(
                            run_sid, str(claimed["id"]), state="waiting"
                        )
                await _broadcast(
                    _queue_snapshot(
                        run_sid,
                        command_id=f"terminal:{control.identity.run_id}",
                        status="draining" if launched is not None else "idle",
                    )
                )

            task = asyncio.create_task(_run())
            tasks.add(task)

            def _finalize(done: asyncio.Task[Any]) -> None:
                tasks.discard(done)
                # A cancelled or crashed task never sent a terminal; mark the
                # control dead so nothing claims execution on its behalf.
                control.abandon()
                if not done.cancelled() and done.exception() is not None:
                    logging.getLogger("coworker.server").error(
                        "run task for %s crashed outside run_turn",
                        control.identity.run_id,
                        exc_info=done.exception(),
                    )

            task.add_done_callback(_finalize)
            return control

        async def _kick_drain(sid: str) -> None:
            """Claim and launch the next queued item when nothing is running."""
            if coordinator.active_for(sid) is not None:
                return
            claimed = store.claim_next_queue(sid)
            if claimed is None:
                return
            # Launch before any await so no other frame can fork the session.
            launched = await _launch(
                str(claimed["text"]), sid, queue_item_id=str(claimed["id"])
            )
            if launched is None:
                store.finish_queue_item(sid, str(claimed["id"]), state="waiting")
                return
            await _broadcast(
                _queue_snapshot(
                    sid,
                    command_id=f"drain-{secrets.token_hex(8)}",
                    status="draining",
                )
            )

        # Reconnect contract: a new socket first hears, per session with a
        # run started by this process, either the live run's ORIGINAL
        # turn_start or the ended run's ORIGINAL terminal event (same
        # event_id, so clients dedupe by identity), then one authoritative
        # queue snapshot per session holding items. Collection is fully
        # synchronous, so nothing published after registration is missed.
        replay_frames: list[dict[str, Any]] = []
        for replay_control in coordinator.latest_per_session():
            run_events = store.load_events(replay_control.identity.session_id)
            replay_run_id = replay_control.identity.run_id
            if not replay_control.terminal:
                frame = next(
                    (
                        event
                        for event in run_events
                        if event.get("type") == "turn_start"
                        and event.get("run_id") == replay_run_id
                    ),
                    None,
                )
            else:
                frame = next(
                    (
                        event
                        for event in reversed(run_events)
                        if event.get("type")
                        in ("turn_end", "turn_stopped", "error")
                        and event.get("run_id") == replay_run_id
                    ),
                    None,
                )
            if frame is not None:
                replay_frames.append(frame)
        for queue_sid in store.sessions_with_queue():
            replay_frames.append(
                _queue_snapshot(
                    queue_sid,
                    command_id=f"connection-{secrets.token_hex(8)}",
                    status="connection",
                )
            )

        try:
            async with send_lock:
                # Hold the send lock across the replay: a live event published
                # mid-replay queues behind it and arrives after, in order.
                for frame in replay_frames:
                    await _send_locked(frame)
            while True:
                if not socket_live:
                    break
                try:
                    incoming = await ws.receive_json()
                except WebSocketDisconnect:
                    raise
                except Exception:
                    await _send({"type": "error", "message": "malformed frame"})
                    continue
                if not isinstance(incoming, dict):
                    await _send(
                        {"type": "error", "message": "command must be an object"}
                    )
                    continue
                command_type = incoming.get("type")
                if command_type == "permission":
                    call_id = str(incoming.get("id") or "")
                    decision = str(incoming.get("decision") or "").strip().lower()
                    actor = str(incoming.get("actor") or "operator").strip() or "operator"
                    scope = str(incoming.get("scope") or "once").strip() or "once"
                    if call_id and decision in {"allow", "deny"}:
                        pending_item = app.state.inbox.get(call_id)
                        if pending_item is None:
                            # Never answer an operator action with silence (S5).
                            await _send(
                                {
                                    "type": "error",
                                    "message": f"approval {call_id} not found",
                                }
                            )
                        elif pending_item.get("kind") == "recovery_approval":
                            await _resolve_recovery_approval(
                                call_id,
                                decision,
                                actor=actor,
                                scope=scope,
                                claimant=f"recovery-ws:{secrets.token_hex(16)}",
                            )
                        else:
                            item_run_id = str(pending_item.get("run_id") or "")
                            item_sid = str(
                                pending_item.get("session_id") or ""
                            )
                            turn_control = coordinator.get(
                                item_sid, item_run_id
                            )
                            turn_alive = (
                                turn_control is not None
                                and not turn_control.terminal
                            )
                            # Claim for the turn only when that turn is live;
                            # otherwise claim independently and execute here,
                            # exactly as the HTTP path does (B1).
                            claimant = (
                                f"turn:{item_run_id or call_id}"
                                if turn_alive
                                else f"ws:{secrets.token_hex(16)}"
                            )
                            claim = app.state.inbox.decide_and_claim(
                                call_id,
                                decision,
                                actor=actor,
                                scope=scope,
                                claimant=claimant,
                            )
                            if claim is not None:
                                if (
                                    claim.item.get("execution_status")
                                    == "interrupted"
                                ):
                                    approval = _persist_approval_receipt(
                                        claim.item
                                    )
                                    if approval is not None:
                                        await _send(approval)
                                elif not turn_alive:
                                    if decision == "allow" and (
                                        claim.claimed and claim.owned
                                    ):
                                        receipt = (
                                            await _execute_claimed_approval(
                                                claim.item,
                                                claimant=claimant,
                                            )
                                        )
                                        approval = _persist_approval_receipt(
                                            receipt
                                        )
                                        if approval is not None:
                                            await _publish_live_events(
                                                [approval]
                                            )
                                    elif (
                                        str(claim.item.get("execution_status"))
                                        in _TERMINAL_EXECUTION
                                    ):
                                        # Fresh deny, or an idempotent
                                        # re-click: answer with the honest
                                        # receipt, never silence (S5).
                                        approval = _persist_approval_receipt(
                                            claim.item
                                        )
                                        if approval is not None:
                                            await _publish_live_events(
                                                [approval]
                                            )
                                    else:
                                        # Resolved, execution still running
                                        # under another claimant: its receipt
                                        # broadcasts on completion.
                                        await _send(
                                            {
                                                "type": "error",
                                                "message": (
                                                    "approval resolved "
                                                    "elsewhere; outcome "
                                                    "pending"
                                                ),
                                            }
                                        )
                            else:
                                # Stale action — expired, cancelled, or
                                # resolved the other way (S5).
                                stale = app.state.inbox.get(call_id)
                                if stale is not None and (
                                    str(stale.get("execution_status"))
                                    in _TERMINAL_EXECUTION
                                ):
                                    approval = _persist_approval_receipt(stale)
                                    if approval is not None:
                                        await _publish_live_events([approval])
                                else:
                                    await _send(
                                        {
                                            "type": "error",
                                            "message": (
                                                "approval resolved elsewhere; "
                                                "outcome pending"
                                            ),
                                        }
                                    )
                    continue
                if command_type == "cancel":
                    sid = str(incoming.get("session_id") or "")
                    run_id = str(incoming.get("run_id") or "")
                    if sid and run_id:
                        if coordinator.get(sid, run_id) is None:
                            # A run this process no longer tracks: a stale
                            # Stop must not silently pause the drain (m1).
                            await _send(
                                {
                                    "type": "error",
                                    "message": f"run {run_id} not found",
                                }
                            )
                            continue
                        # Pause before cancelling so a run that just finished
                        # cannot drain the next item past the operator's Stop.
                        store.set_queue_paused(sid, True)
                        await coordinator.cancel(sid, run_id)
                        if store.list_queue(sid):
                            await _send(
                                _queue_snapshot(
                                    sid,
                                    command_id=f"cancel:{run_id}",
                                    status="paused",
                                )
                            )
                    continue
                if command_type == "retry_failed_step":
                    await _retry_failed_step(incoming)
                    continue
                if command_type == "repair_connection":
                    await _record_recovery_choice(incoming, action="repair")
                    continue
                if command_type == "continue_without_source":
                    await _record_recovery_choice(incoming, action="continue")
                    continue
                if command_type in {
                    "queue_add",
                    "queue_edit",
                    "queue_move",
                    "queue_remove",
                    "queue_retry",
                    "queue_resume",
                }:
                    sid = str(incoming.get("session_id") or "")
                    if sid.startswith("sched-"):
                        # Scheduled threads belong to the scheduler; a queued
                        # chat run would race its transcript writes.
                        await _send(
                            {
                                "type": "error",
                                "message": "scheduled sessions are read-only",
                            }
                        )
                        continue
                    if sid and valid_session_id(sid):
                        try:
                            acknowledgement = store.apply_queue_command(
                                sid, incoming
                            )
                        except ValueError as exc:
                            await _send({"type": "error", "message": str(exc)})
                            continue
                        await _send(acknowledgement)
                        if command_type in {"queue_resume", "queue_retry"}:
                            await _kick_drain(sid)
                    continue
                if command_type != "chat":
                    continue
                text = str(incoming.get("text") or "").strip()
                if not text:
                    continue
                sid = str(incoming.get("session_id") or store.open_session_id() or "")
                if sid and not valid_session_id(sid):
                    await ws.send_json({"type": "error", "message": "invalid session"})
                    continue
                if sid.startswith("sched-"):
                    await _send(
                        {
                            "type": "error",
                            "message": "scheduled sessions are read-only",
                        }
                    )
                    continue
                if not sid:
                    sid = store.create_session()["session_id"]
                bound_person_id = app.state.people.person_for_session(sid)
                if (
                    bound_person_id is not None
                    and app.state.people.get(bound_person_id) is None
                ):
                    await _send(
                        {
                            "type": "error",
                            "message": (
                                "This conversation's bound person file is unavailable."
                            ),
                        }
                    )
                    continue
                store.set_open_session(sid)
                queue_busy = not store.queue_paused(sid) and any(
                    item["state"] in ("waiting", "retrying", "reconnecting")
                    for item in store.list_queue(sid)
                )
                launched: RunControl | None = None
                if coordinator.active_for(sid) is None and not queue_busy:
                    launched = await _launch(text, sid)
                if launched is None:
                    # A run is active or older items are draining ahead of
                    # this message: join the queue instead of forking the
                    # session or jumping the line (S2).
                    command_id = str(
                        incoming.get("command_id") or f"command_{secrets.token_hex(8)}"
                    )
                    item_id = str(
                        incoming.get("item_id") or f"queue_{secrets.token_hex(8)}"
                    )
                    await _send(
                        store.apply_queue_command(
                            sid,
                            {
                                "type": "queue_add",
                                "command_id": command_id,
                                "item_id": item_id,
                                "text": text,
                            },
                        )
                    )
                    await _kick_drain(sid)
        except WebSocketDisconnect:
            pass
        finally:
            socket_live = False
            app.state.live_event_senders.discard(sender_registration)

    return app
