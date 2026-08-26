"""Club sidecar: health + streamed chat + approval + disk + memory.

Copied from OpenWorker: origin gate, launch token, WS subprotocol auth,
jsonl conversations + sqlite memories. Slice 6: remember / update / forget.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import secrets
import threading
import coworker.turn as turn_runtime
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from coworker.automation.scheduler import (
    ROUTINE_TEMPLATES,
    SUPPORTED_CADENCES,
    Scheduler,
    next_monday_0900,
    now_iso,
)
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
from coworker.events import build_event, new_turn_identity, TurnIdentity
from coworker.gmail import gmail_from_secrets
from coworker.inbox import Inbox
from coworker.mcp import FakeMcp, LiveMcp, write_default_mcp_json
from coworker.mcp_oauth import McpOAuth
from coworker.brief import build_brief
from coworker.people import PersonStore
from coworker.persona import ManifestError, Persona, load_persona
from coworker.skills import BUILTIN_SKILLS, SkillLoader, catalog_text
from coworker.permissions import decide
from coworker.provider import ToolCall, provider_from_env
from coworker.secrets import SecretStore
from coworker.store import ConversationStore, valid_session_id
from coworker.sourcing_index import SourcingIndex
from coworker.tools import OPENAI_TOOLS, execute
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
KERNEL = (
    "Tools: now (America/Los_Angeles clock, auto), remember / memory_update / "
    "memory_forget (auto), load_skill (auto), gmail_search / gmail_read (auto), "
    "gmail_draft (ask), gmail_send (ask; sends a reviewed draft after Allow), "
    "drive_search / drive_list_folder / drive_read (auto, readonly), "
    "calendar_list (auto; upcoming from now unless a time range is given), "
    "calendar_create / calendar_update (ask, no delete), "
    "board_get / board_query (auto), board_upsert / board_mutate (auto, audited), "
    "board_delete (ask; destructive), "
    "apollo_search_people (no emails), people_keep (auto; file curated search "
    "rows after the director chooses, do not invent the target), "
    "apollo_enrich_contact "
    "(ask), web_search (auto). MCP tools are named mcp__server__tool. Do not invent the time, tool "
    "results, emails, or memories. Do not claim you sent or drafted unless the "
    "matching tool ran and was allowed. Do not send without Allow. Legal templates "
    "are source evidence, not ready-to-use agreements. If source_safety.ready_to_use "
    "is false, verify every named party, date, term, and approval status; record a "
    "knowledge gap and do not adapt or recommend the document as ready to use."
)


_PERSON_FILE_EVENT_CAP = 12


def system_prompt(
    store: ConversationStore,
    persona: Persona | None = None,
    skills: SkillLoader | None = None,
    *,
    people: PersonStore | None = None,
    session_id: str | None = None,
) -> str:
    identity = persona.body if persona is not None else KERNEL
    parts = [identity, KERNEL]
    items = store.list_memories()
    if items:
        lines = "\n".join(f"[#{item['id']}] {item['content']}" for item in items)
        if len(lines) > 4000:
            index_path = store.memory_dir / "MEMORY.md"
            if index_path.is_file():
                blob = index_path.read_text(encoding="utf-8")
                kept: list[str] = []
                size = 0
                for line in blob.splitlines():
                    extra = len(line) + 1
                    if size + extra > 4000 and kept:
                        break
                    kept.append(line)
                    size += extra
                parts.append("\n".join(kept))
            else:
                parts.append("Known memories:\n" + lines[:4000])
        else:
            parts.append("Known memories:\n" + lines)
    else:
        parts.append("No saved memories yet.")
    if skills is not None:
        catalog = catalog_text(skills)
        if catalog:
            parts.append(catalog)
    if people is not None and session_id:
        person_id = people.person_for_session(session_id)
        person = people.get(person_id) if person_id else None
        if person is not None:
            events = people.timeline(person_id)
            brief = build_brief(person, events)
            recent = events[-_PERSON_FILE_EVENT_CAP:]
            learned_lines = [str(event.get("summary") or "") for event in recent if event.get("summary")]
            parts.append(
                "Person file:\n"
                f"who: {brief['who']}\n"
                f"why: {brief['why']}\n"
                f"learned:\n" + ("\n".join(f"- {line}" for line in learned_lines) or "-") + "\n"
                f"missing: {', '.join(brief['missing'])}"
            )
    return "\n\n".join(parts)


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
) -> FastAPI:
    if not token:
        raise ValueError("sidecar token must be non-empty")

    app = FastAPI(title="Club sidecar", version="0.0.2")
    app.state.token = token
    app.state.provider = provider_from_env() if provider is _UNSET else provider
    root = state if state is not None else state_dir()
    app.state.store = ConversationStore(root)
    app.state.people = PersonStore(root)
    app.state.sourcing_index = SourcingIndex(root)
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
    app.state.openai_tools = list(OPENAI_TOOLS) + app.state.mcp.schemas()

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
                persona=app.state.persona,
                skills=app.state.skills,
                inbox=app.state.inbox,
                openai_tools=app.state.openai_tools,
                execute_kwargs={
                    "store": app.state.store,
                    "gmail": app.state.gmail,
                    "drive": app.state.drive,
                    "calendar": app.state.calendar,
                    "http": app.state.http,
                    "apollo_key": app.state.apollo_key,
                    "tavily_key": app.state.tavily_key,
                    "skills": app.state.skills,
                    "mcp": app.state.mcp,
                    "people": app.state.people,
                    "sourcing_index": app.state.sourcing_index,
                },
                emit=None,
                wait_permission=None,
                system_prompt_fn=system_prompt,
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
        existing = next(
            (
                event
                for event in store.load_events(sid)
                if event.get("type") == "approval_resolved"
                and event.get("id") == item.get("id")
                and event.get("resolved_at") == item.get("resolved_at")
            ),
            None,
        )
        if existing is not None:
            return existing
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
        store.append_event(sid, event)
        return event

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

    async def _execute_claimed_approval(
        item: dict[str, Any], *, claimant: str
    ) -> dict[str, Any] | None:
        """Run an allow-claimed approval server-side and persist its receipt."""
        try:
            ok, result = await asyncio.to_thread(
                execute,
                item["name"],
                item["arguments"],
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
                sourcing_index=app.state.sourcing_index,
                actor=str(item.get("actor") or "assistant"),
                session_id=str(item.get("session_id") or ""),
                run_id=str(item.get("run_id") or "") or None,
            )
        except Exception as exc:
            ok, result = False, {"error": str(exc)}
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
            try:
                ok, result = await asyncio.to_thread(
                    turn_runtime.execute,
                    item["name"],
                    item["arguments"],
                    store=store,
                    gmail=app.state.gmail,
                    drive=app.state.drive,
                    calendar=app.state.calendar,
                    http=app.state.http,
                    apollo_key=app.state.apollo_key,
                    tavily_key=app.state.tavily_key,
                    skills=app.state.skills,
                    mcp=app.state.mcp,
                    people=app.state.people,
                    sourcing_index=app.state.sourcing_index,
                    actor=str(item.get("actor") or "assistant"),
                    session_id=str(item.get("session_id") or ""),
                    run_id=str(item.get("run_id") or "") or None,
                )
                result = dict(result)
            except Exception as exc:
                ok, result = False, {"error": str(exc)}
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
        return {"id": p.id, "name": p.name, "tools": p.tools}

    @app.get("/v1/skills")
    def skills():
        return {"skills": app.state.skills.catalog()}

    @app.get("/v1/settings")
    def settings():
        p = app.state.provider
        profile = load_google(app.state.secrets)
        on_duty = app.state.persona
        return {
            "persona": {"id": on_duty.id, "name": on_duty.name},
            "model": None if p is None else p.model_id,
            "gmail": {
                "connected": bool(profile.get("refresh_token")),
                "email": profile.get("email"),
            },
            "apollo": {"configured": bool(app.state.apollo_key)},
        }

    @app.post("/v1/settings/persona")
    async def set_persona(request: Request):
        payload = await request.json()
        pid = str(payload.get("id") or "").strip()
        try:
            nxt = load_persona(pid)
        except ManifestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        app.state.store.set_setting("persona", nxt.id)
        app.state.persona = nxt
        return {"persona": {"id": nxt.id, "name": nxt.name}}

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
        app.state.openai_tools = list(OPENAI_TOOLS) + app.state.mcp.schemas()
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
            app.state.openai_tools = list(OPENAI_TOOLS) + app.state.mcp.schemas()
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

    @app.get("/v1/board/records")
    def board_records_list(
        record_type: str | None = None,
        stage: str | None = None,
        owner: str | None = None,
        due_date: str | None = None,
        name: str | None = None,
        expand_sources: bool = False,
    ):
        filters = {
            key: value
            for key, value in {
                "stage": stage,
                "owner": owner,
                "due_date": due_date,
                "name": name,
            }.items()
            if value is not None
        }
        try:
            records = app.state.sourcing_index.query(
                record_type=record_type,
                filters=filters,
                expand_sources=expand_sources,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"records": records, "count": len(records)}

    @app.get("/v1/board/records/{record_id}")
    def board_record_get(record_id: str):
        record = app.state.sourcing_index.get(record_id, expand_sources=True)
        if record is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {
            "record": record,
            "links": app.state.sourcing_index.links(record_id),
            "receipts": app.state.sourcing_index.receipts(record_id),
        }

    @app.post("/v1/board/records/{record_id}/revert")
    async def board_record_revert(record_id: str, request: Request):
        payload = await request.json()
        try:
            record = app.state.sourcing_index.revert(
                record_id,
                to_version=int(payload.get("to_version") or 0),
                expected_version=int(payload.get("expected_version") or 0),
                actor="director",
                rationale_summary=str(payload.get("rationale_summary") or ""),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"record": record}

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
        person = app.state.people.get(person_id)
        if person is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        timeline = [_public_event(event) for event in app.state.people.timeline(person_id)]
        return {
            "person": person,
            "brief": build_brief(person, timeline),
            "timeline": timeline,
        }

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
    async def sessions_get(sid: str):
        store = app.state.store
        row = store.index(sid)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        # A restored thread must not show a live-looking card for an
        # already-expired approval: reap and persist receipts first.
        await _reap_and_publish_expired()
        if not sid.startswith("sched-"):
            store.set_open_session(sid)
        return {
            "id": sid,
            "title": row["title"],
            "messages": store.load(sid),
            "events": store.load_events(sid),
            "queue": store.list_queue(sid),
            "queue_paused": store.queue_paused(sid),
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
                resource = turn_runtime.approval_resource(
                    name, arguments, app.state.gmail
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
                    resource=resource,
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
            try:
                ok, result = await asyncio.to_thread(
                    turn_runtime.execute,
                    name,
                    arguments,
                    store=store,
                    gmail=app.state.gmail,
                    drive=app.state.drive,
                    calendar=app.state.calendar,
                    http=app.state.http,
                    apollo_key=app.state.apollo_key,
                    tavily_key=app.state.tavily_key,
                    skills=app.state.skills,
                    mcp=app.state.mcp,
                    people=app.state.people,
                    sourcing_index=app.state.sourcing_index,
                    session_id=session_id,
                    run_id=run_id,
                )
            except Exception as exc:
                # The claim is held: this command must still record an outcome.
                ok, result = False, {"error": str(exc)}
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
                    persona=app.state.persona,
                    skills=app.state.skills,
                    inbox=app.state.inbox,
                    openai_tools=app.state.openai_tools,
                    execute_kwargs={
                        "store": store,
                        "gmail": app.state.gmail,
                        "drive": app.state.drive,
                        "calendar": app.state.calendar,
                        "http": app.state.http,
                        "apollo_key": app.state.apollo_key,
                        "tavily_key": app.state.tavily_key,
                        "skills": app.state.skills,
                        "mcp": app.state.mcp,
                        "people": app.state.people,
                        "sourcing_index": app.state.sourcing_index,
                    },
                    emit=_broadcast,
                    wait_permission=_wait,
                    system_prompt_fn=system_prompt,
                    identity=control.identity,
                    control=control,
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
