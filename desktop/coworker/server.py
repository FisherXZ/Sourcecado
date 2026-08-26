"""Club sidecar: health + streamed chat + approval + disk + memory.

Copied from OpenWorker: origin gate, launch token, WS subprotocol auth,
jsonl conversations + sqlite memories. Slice 6: remember / update / forget.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from coworker.automation.scheduler import Scheduler
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
from coworker.tools import OPENAI_TOOLS, execute
from coworker.turn import close_open_tool_calls, run_turn

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
    "gmail_draft (ask; creates a draft and never sends), drive_search / drive_read (auto, readonly), "
    "calendar_list (auto; upcoming from now unless a time range is given), "
    "calendar_create / calendar_update (ask, no delete), "
    "apollo_search_people (no emails), people_keep (auto; file curated search "
    "rows after the director chooses, do not invent the target), "
    "apollo_enrich_contact "
    "(ask). MCP tools are named mcp__server__tool. Do not invent the time, tool "
    "results, emails, or memories. Do not claim you sent or drafted unless the "
    "matching tool ran and was allowed."
)


def system_prompt(
    store: ConversationStore,
    persona: Persona | None = None,
    skills: SkillLoader | None = None,
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
    return "\n\n".join(parts)


def state_dir() -> Path:
    override = os.environ.get("CLUB_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "club"


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


async def _await_permission(ws: WebSocket, call_id: str, inbox: Inbox) -> str:
    while True:
        item = inbox.get(call_id)
        if item and item.get("state") == "resolved" and item.get("decision") in ("allow", "deny"):
            return str(item["decision"])
        try:
            incoming = await asyncio.wait_for(ws.receive_json(), timeout=0.2)
        except TimeoutError:
            continue
        except Exception:
            await asyncio.sleep(0.2)
            continue
        if incoming.get("type") != "permission":
            continue
        if str(incoming.get("id") or "") != call_id:
            continue
        decision = str(incoming.get("decision") or "").strip().lower()
        if decision in ("allow", "deny"):
            inbox.resolve(call_id, decision)
            return decision


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
) -> FastAPI:
    if not token:
        raise ValueError("sidecar token must be non-empty")

    app = FastAPI(title="Club sidecar", version="0.0.2")
    app.state.token = token
    app.state.provider = provider_from_env() if provider is _UNSET else provider
    root = state if state is not None else state_dir()
    app.state.store = ConversationStore(root)
    app.state.people = PersonStore(root)
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
    app.state.public_url = public_url or "http://127.0.0.1:8765"
    app.state.oauth_state = ""
    app.state.skills = SkillLoader([BUILTIN_SKILLS, Path(root) / "skills"])
    write_default_mcp_json(Path(root) / "mcp.json")
    app.state.mcp_oauth = McpOAuth(app.state.secrets, app.state.public_url, http=http)
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
                    "skills": app.state.skills,
                    "mcp": app.state.mcp,
                    "people": app.state.people,
                },
                emit=None,
                wait_permission=None,
                system_prompt_fn=system_prompt,
            )
        )

    app.state.scheduler.job_runner = _default_job_runner
    app.state.tool_results: dict[str, tuple[bool, dict[str, Any]]] = {}

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

    @app.get("/v1/inbox")
    def inbox_list():
        return {"items": app.state.inbox.pending()}

    @app.post("/v1/inbox/{item_id}")
    async def inbox_resolve(item_id: str, request: Request):
        payload = await request.json()
        decision = str(payload.get("decision") or "").strip().lower()
        item = app.state.inbox.get(item_id)
        if item is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        resolved = app.state.inbox.resolve(item_id, decision)
        if resolved is None:
            return JSONResponse({"error": "already resolved"}, status_code=409)
        if decision == "deny":
            app.state.tool_results[item_id] = (False, {"error": "denied by user"})
            return {"ok": False, "item": resolved, "result": {"error": "denied by user"}}
        ok, result = execute(
            item["name"],
            item["arguments"],
            store=app.state.store,
            gmail=app.state.gmail,
            drive=app.state.drive,
            calendar=app.state.calendar,
            http=app.state.http,
            apollo_key=app.state.apollo_key,
            skills=app.state.skills,
            mcp=app.state.mcp,
            people=app.state.people,
        )
        app.state.tool_results[item_id] = (ok, result)
        return {"ok": ok, "item": resolved, "result": result}

    @app.post("/v1/schedule/tick")
    def schedule_tick():
        runs = app.state.scheduler.tick()
        return {"runs": runs}

    @app.post("/v1/schedule/{job_id}/run")
    def schedule_run(job_id: int):
        try:
            run = app.state.scheduler.run_job(job_id)
        except KeyError:
            return JSONResponse({"error": "not found"}, status_code=404)
        except RuntimeError:
            return JSONResponse({"error": "already running"}, status_code=409)
        return {"run": run}

    @app.get("/v1/schedule")
    def schedule():
        return app.state.store.list_schedule()

    @app.get("/v1/connectors")
    def connectors():
        profile = load_google(app.state.secrets)
        email = profile.get("email")
        refresh = bool(profile.get("refresh_token"))
        gmail_ok = refresh and (
            (has_scope(profile, COMPOSE_SCOPE) and has_scope(profile, READ_SCOPE))
            or (not profile.get("scopes") and refresh)
        )
        granola = app.state.secrets.get("mcp-oauth:granola") or {}
        return {
            "connectors": [
                {
                    "id": "gmail",
                    "title": "Gmail",
                    "status": "connected" if gmail_ok else "missing",
                    "email": email if gmail_ok else None,
                },
                {
                    "id": "drive",
                    "title": "Drive",
                    "status": "connected" if refresh and has_scope(profile, DRIVE_SCOPE) else "missing",
                    "email": email if has_scope(profile, DRIVE_SCOPE) else None,
                },
                {
                    "id": "calendar",
                    "title": "Calendar",
                    "status": "connected" if refresh and has_calendar_access(profile) else "missing",
                    "email": email if has_calendar_access(profile) else None,
                },
                {
                    "id": "apollo",
                    "title": "Apollo",
                    "status": "configured" if app.state.apollo_key else "missing",
                    "email": None,
                },
                {
                    "id": "granola",
                    "title": "Granola",
                    "status": "connected" if granola.get("access_token") or granola.get("refresh_token") else "missing",
                    "email": None,
                },
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
        opened = False
        try:
            import subprocess

            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except Exception:
            opened = False
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
        return {"connected": False}

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
        opened = False
        try:
            import subprocess

            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            opened = True
        except Exception:
            opened = False
        return {"url": url, "opened": opened, "redirect_uri": redirect}

    @app.post("/v1/gmail/disconnect")
    def gmail_disconnect():
        app.state.secrets.delete(GMAIL_KEY)
        app.state.secrets.delete(GOOGLE_KEY)
        app.state.gmail = gmail_from_secrets(app.state.secrets, http=app.state.http)
        return {"connected": False, "email": None}

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
        return {"sessions": store.list_sessions(), "open_id": store.open_session_id()}

    @app.post("/v1/sessions")
    def sessions_create():
        store = app.state.store
        row = store.create_session()
        store.set_open_session(row["session_id"])
        return {"id": row["session_id"], "title": row["title"], "n_msgs": row["n_msgs"]}

    @app.get("/v1/sessions/{sid}")
    def sessions_get(sid: str):
        store = app.state.store
        row = store.index(sid)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return {"id": sid, "title": row["title"], "messages": store.load(sid)}

    @app.patch("/v1/sessions/{sid}")
    async def sessions_patch(sid: str, request: Request):
        payload = await request.json()
        title = str(payload.get("title") or "")
        row = app.state.store.rename_session(sid, title)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        app.state.store.set_open_session(sid)
        return {"id": sid, "title": row["title"]}

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
        try:
            while True:
                incoming = await ws.receive_json()
                if incoming.get("type") != "chat":
                    continue
                text = str(incoming.get("text") or "").strip()
                if not text:
                    continue
                sid = str(incoming.get("session_id") or store.open_session_id() or "")
                if sid and not valid_session_id(sid):
                    await ws.send_json({"type": "error", "message": "invalid session"})
                    continue
                if not sid:
                    sid = store.create_session()["session_id"]
                store.set_open_session(sid)

                async def _wait(call_id: str, _sid: str = sid) -> str:
                    return await _await_permission(ws, call_id, app.state.inbox)

                await run_turn(
                    text=text,
                    sid=sid,
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
                        "skills": app.state.skills,
                        "mcp": app.state.mcp,
                        "people": app.state.people,
                        "_tool_results": app.state.tool_results,
                    },
                    emit=ws.send_json,
                    wait_permission=_wait,
                    system_prompt_fn=system_prompt,
                )
        except WebSocketDisconnect:
            return

    return app
