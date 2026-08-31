# Pass 3 — sessions, connector suite, scheduler turn — Implementation Plan

> Steps use checkbox (`- [ ]`) syntax for tracking. Work task-by-task. Pause after each slice unless told to keep going.
>
> Second review folded in: honor `Decision.allowed`, LiveHttp text, Drive `unquote`, Granola sync-bridge, scheduler `job_runner` wiring, existing `main` tests, CSS `.connector-strip`, `openChat(sessionId)`, health assertions in both test files, and Task 12 code consolidation.

**Goal:** The Club window can open more than one chat, show a five-connector status panel, search/read Gmail plus Drive and Calendar, talk to Granola through MCP OAuth, and let a due scheduler job run a real turn that parks asks in the inbox.

**Architecture:** Keep the three Club layers. The backend stays the only brain. Copy OpenWorker *jobs* (session list, incremental Google scopes, fake-HTTP connectors, Granola MCP OAuth, scheduler runner) into owned files under `desktop/coworker/`. Do not import OpenWorker, OpenClaw, or grok-bot as a package. The window stays Warm Operator: 232px session rail + transcript + connector/inbox strip.

**Tech Stack:** Python 3 backend (FastAPI, httpx, pytest), Tauri + React window, sqlite + jsonl under `~/.config/club/`, secrets.json 0600. New dep only for Granola: `mcp>=1.28.1,<2`.

**Spec:** `docs/superpowers/plans/2026-08-24-pass-3-openworker-gap.md`
**Design:** `~/.gstack/projects/sourcecado/fisher-fix-loop-truncated-partial-answer-design-20260824-desktop-buddy.md`
**Tokens:** `DESIGN.md` Warm Operator.

## Global Constraints

- Work in `desktop/` only. The old Next.js stack is archived under `archive/hosted-web/`; do not wire Pass 3 through it.
- Disk stays `~/.config/club/`. Tests use `tmp_path`, never the live config dir.
- Gmail drafts and read. No `gmail_send`. Drive/Calendar/Granola have no destructive writes in v1.
- One Google login. Extra scopes are incremental on the same refresh token. Tokens never appear in REST payloads, WS events, or the system prompt.
- Tool names follow the gap doc, not OpenWorker: `gmail_search`, `gmail_read`, `gmail_draft`, `drive_search`, `drive_read`, `calendar_list`, `calendar_create`, `calendar_update`. Granola tools stay `mcp__granola__*`.
- Permissions: search/read/list auto. `gmail_draft`, `calendar_create`, `calendar_update`, `apollo_enrich_contact` ask. MCP read tools stay auto. MCP names whose last segment matches `write|create|delete|update` deny. Unknown tools deny. The turn loop **must** honor `Decision.allowed`: if `not allowed and not needs_user`, emit `tool_finished` error and skip `execute`. Do not rely on `LiveMcp.call` alone.
- Session naming: sqlite and `list_sessions` rows use `session_id`. REST POST/GET/PATCH bodies use `id` for that same value. List rows keep `session_id`. Window `createSession` returns `id`; rail rows use `session_id`.
- Google helpers live in `connectors/google_oauth.py` (`load_google` / `save_google` / `has_scope`). Do not add `google.py`. `mcp.json` helpers live in `mcp.py`. Do not add `mcp_config.py`.
- One Google loopback: `/v1/gmail/callback`. One `app.state.oauth_state`. The callback is scope-agnostic: it merges `tokens.get("scope")` into the existing profile. Never write a hardcoded `BASE_SCOPES` list over granted scopes.
- Fake HTTP in pytest. Live Google/Granola/Apollo only as optional skip-gated smokes.
- `LiveHttp.get` accepts `params=` and returns text when the response is not JSON (Drive export). `FakeHttp` already returns `str` route values as-is.
- Each task: pytest at the public seam, then one click in the window. Pause after each slice unless told to keep going.
- Commit per task. Prefix `feat(pass3):` / `test(pass3):` / `fix(club):` for the consolidation task.
- `SLICE` in `server.py` is the last landed gap-doc slice. Every bump updates **both** `tests/test_chat.py::test_health_reports_slice_and_model` and `tests/test_health.py::test_health_ok_with_token` (`assert body["slice"] == N`). Layout (gap slice 28) is folded into Tasks 4–5, so health never needs a fake 28. Consolidation does not bump SLICE. After MEMORY.md it is 29.

## Slice review (read this before coding)

Slices 1–18 already persist jsonl keyed by id, but `SESSION_ID = "main"` is hardcoded. Gmail compose OAuth, inbox, scheduler stub, FakeMcp, Apollo search/enrich, and `{id}.md` memory files already exist. Pass 3 is the gap: expose sessions in the window, finish Gmail, add Drive/Calendar/Granola, make the scheduler run a turn.

### Copy vs skip

| Job | Copy from | Skip |
|---|---|---|
| Session list/new/open/rename | OpenWorker `GET /v1/sessions` + `PATCH` title; Club `POST /v1/sessions` (OW mints ids in the GUI) | OW pin/archive/workspace/team/board |
| Last-open restore | grok-bot persist-open-id. OpenWorker boot uses latest `updated_at`, which drops an empty new chat | grok roster; OW sort-by-updated |
| Auto-title | Club `title_from` already: first user line, 60 chars. `COALESCE` keeps a rename | OW `auto_title`/`renamed` columns |
| Gmail search/read | OW `gmail_search_messages` / `gmail_get_message` HTTP, Club names | OW send, hidden-label filters, multi-account prefixes |
| Drive | OW `drive_search_files` / `drive_read_file` | list-folder, writes |
| Calendar | OW `gcal_list_events` / `gcal_create_event` / `gcal_update_event` | delete, freebusy, Outlook |
| Google identity | One `secrets.json` profile, incremental scopes | OW Cloud/Auth0 broker, per-connector account keys |
| Granola | OW granola MCP URL + one pending OAuth slot + `mcp__granola__*` | stdio MCP, 25-app dump, OW `mcp/oauth.py` paste, Granola API key |
| Scheduler turn | OW runner injection + skip-on-overlap; grok-bot "fire into a session" job | grok cloud poller |
| MEMORY.md | OpenClaw *index* job: one `MEMORY.md` beside `memory/{id}.md` | dreaming, USER.md, daily notes, QMD |
| Layout | DESIGN.md 232px left rail, cream `#FAF8F3`, avocado `#5B8C2A`, General Sans. Lands in Tasks 4–5 | OW Sidebar chrome, spreadsheet shell, a second layout task |

### Locked choices (X over Y because Z)

- REST session CRUD over OW's client-minted UUID, because the gap doc named `POST /v1/sessions` and Club's sqlite index already exists.
- One `google` secrets profile (migrate existing `gmail` key) over OW `gmail:account:` prefixes, because Club is one operator and one Google login.
- `load_google` / `save_google` in `google_oauth.py` over a new `google.py`, because they are two functions next to the scopes they describe.
- Request `gmail.readonly` together with compose in slice 19 over a second consent in slice 22, because Fisher should not re-auth for search.
- Callback merges `tokens["scope"]` over writing `BASE_SCOPES`, because Drive/Calendar reuse the same callback and a hardcoded list would drop extra scopes.
- Extract `run_turn` in slice 27 over duplicating the WS loop, because the scheduler and inbox already need the same ask/allow path.
- Dedicated session `sched-{job_id}` over appending to the open chat, because a weekly job must not hijack a live transcript.
- Official `mcp` SDK for Granola over hand-rolled streamable HTTP, because OW already paid the OAuth 2.1 + PKCE + DCR cost (`mcp>=1.28.1,<2`).
- Sync MCP calls via a **dedicated worker thread** (`anyio.from_thread.run` / `asyncio.run` on that thread only) over `asyncio.run` inside the uvicorn loop, because `execute()` is sync and WS already has a running loop.
- Owned `McpOAuth` (one pending slot) over pasting OW `mcp/oauth.py`. Read OpenWorker `coworker/mcp/oauth.py` as reference only. Do not paste it.
- MCP OAuth only over a `GRANOLA_API_KEY` maybe-path, because that path was specified as skip-if-undocumented.
- Regenerated `MEMORY.md` from sqlite over OpenClaw dreaming, because sqlite is already the source of truth.
- Warm Operator shell in the session-rail task over a later slice-28 rewrite, because the rail already has to drop the 720px column.

### Human gate (slice 19)

Gmail connect is unblocked only after the Google **Web** client has redirect:

`http://127.0.0.1:8765/v1/gmail/callback`

Keep the existing client-secret code path. Do not switch to a Desktop client unless the Web client cannot add that URI.

---

## File structure

| File | Responsibility |
|---|---|
| `desktop/coworker/store.py` | sessions list/create/rename/open; MEMORY.md rebuild |
| `desktop/coworker/connectors/google_oauth.py` | scopes, incremental auth URL, token exchange/refresh, `load_google` / `save_google` / `has_scope` |
| `desktop/coworker/gmail.py` | drafts + search + read; no send; 401 retry via `load_google` |
| `desktop/coworker/drive.py` | **new** — search/read, readonly |
| `desktop/coworker/calendar.py` | **new** — list auto, create/update ask |
| `desktop/coworker/mcp.py` | FakeMcp + LiveMcp + default `mcp.json` |
| `desktop/coworker/mcp_oauth.py` | **new** — one pending OAuth slot (PKCE/DCR/loopback) |
| `desktop/coworker/turn.py` | **new** — extracted turn loop (slice 27) |
| `desktop/coworker/tools.py` | schemas + `execute()` |
| `desktop/coworker/permissions.py` | AUTO / ASK / MCP-write deny |
| `desktop/coworker/server.py` | HTTP/WS seams |
| `desktop/coworker/automation/scheduler.py` | due job → `run_turn` |
| `desktop/surfaces/gui/src/api.ts` | session + connector clients |
| `desktop/surfaces/gui/src/App.tsx` | rail, transcript, connector/inbox strip |
| `desktop/surfaces/gui/src/styles.css` | Warm Operator shell |

Do not create `desktop/coworker/google.py` or `desktop/coworker/mcp_config.py`.

---

### Task 0: Baseline green

**Files:** none created.

**Interfaces:**
- Consumes: current `desktop/` slice 18.
- Produces: known pytest count, ready to bump SLICE.

- [ ] **Step 1: Run the desktop suite**

```bash
cd desktop
.venv/bin/pytest -q
```

Expected: pass (checkpoint said 76). If red, stop and fix before Pass 3.

- [ ] **Step 2: Confirm health slice is 18**

```bash
.venv/bin/pytest tests/test_chat.py::test_health_reports_slice_and_model -q
```

Expected: PASS, body slice 18.

---

### Task 1: Slice 19 — Gmail connect actually completes

**Files:**
- Modify: `desktop/coworker/connectors/google_oauth.py` (scopes, `load_google` / `save_google` / `has_scope`, extra_scopes on auth URL)
- Modify: `desktop/coworker/gmail.py` (`_access_token` uses `load_google`; 401 retry)
- Modify: `desktop/coworker/apollo.py` (`HttpError`; `LiveHttp` raises it; `FakeHttp` path-prefix + string bodies)
- Modify: `desktop/coworker/server.py` (callback merges granted scopes; connect URL includes readonly; status/disconnect use `load_google`)
- Test: `desktop/tests/test_gmail.py`

**Interfaces:**
- Consumes: existing `authorization_url` / `exchange_code` / `GMAIL_KEY` profile.
- Produces:
  - `GOOGLE_KEY = "google"`
  - `READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"`
  - `DRIVE_SCOPE` / `CALENDAR_SCOPE` constants (used later; fine to land now)
  - `authorization_url(..., extra_scopes: tuple[str, ...] = ())` also sends `include_granted_scopes=true`
  - `load_google(secrets) -> dict` (reads `google` or legacy `gmail`)
  - `save_google(secrets, profile)` writes `google` and mirrors `gmail`
  - `has_scope(profile, scope) -> bool`
  - GmailApi retries once on `HttpError` status 401 after refresh
  - Callback persists `scopes` from `tokens.get("scope")` merged with any existing profile scopes. Never `scopes = list(BASE_SCOPES)`.
  - Disconnect deletes both `google` and `gmail` keys
  - `HttpError(status, url="", body=None)` on `apollo.py`

Google identity over a second secrets key because Drive/Calendar in later tasks share this profile. Mirror `gmail` so slice-18 readers keep working until this task switches them.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gmail.py` (keep existing tests). Full imports:

```python
from urllib.parse import unquote

from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp, HttpError
from coworker.connectors.google_oauth import (
    COMPOSE_SCOPE,
    DRIVE_SCOPE,
    READ_SCOPE,
    authorization_url,
    load_google,
    save_google,
)
from coworker.gmail import DRAFTS_URL, GmailApi, gmail_from_secrets
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-gmail"


def test_auth_url_includes_readonly_and_compose():
    url = authorization_url(
        client_id="cid",
        redirect_uri="http://127.0.0.1:8765/v1/gmail/callback",
        state="st",
    )
    decoded = unquote(url)
    assert COMPOSE_SCOPE in decoded
    assert READ_SCOPE in decoded
    assert "gmail.send" not in decoded
    assert "include_granted_scopes=true" in url


def test_callback_stores_email_and_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    http = FakeHttp(
        {
            "https://oauth2.googleapis.com/token": {
                "access_token": "at",
                "refresh_token": "rt",
                "scope": f"{COMPOSE_SCOPE} {READ_SCOPE} https://www.googleapis.com/auth/userinfo.email",
            },
            "https://www.googleapis.com/oauth2/v2/userinfo": {
                "email": "fisher@example.com"
            },
        }
    )
    app = create_app(token=TOKEN, state=tmp_path, http=http)
    app.state.oauth_state = "st"
    res = TestClient(app).get("/v1/gmail/callback?code=abc&state=st")
    assert res.status_code == 200
    assert b"Gmail connected" in res.content
    profile = load_google(app.state.secrets)
    assert profile["email"] == "fisher@example.com"
    assert profile["refresh_token"] == "rt"
    assert READ_SCOPE in profile["scopes"]
    assert COMPOSE_SCOPE in profile["scopes"]
    status = TestClient(app).get("/v1/gmail", headers={TOKEN_HEADER: TOKEN}).json()
    assert status == {"connected": True, "email": "fisher@example.com"}
    assert "rt" not in str(status)
    assert "at" not in str(status)


def test_callback_merges_drive_scope_into_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    http = FakeHttp(
        {
            "https://oauth2.googleapis.com/token": {
                "access_token": "at2",
                "refresh_token": "rt",
                "scope": f"{COMPOSE_SCOPE} {READ_SCOPE} {DRIVE_SCOPE} https://www.googleapis.com/auth/userinfo.email",
            },
            "https://www.googleapis.com/oauth2/v2/userinfo": {
                "email": "fisher@example.com"
            },
        }
    )
    app = create_app(token=TOKEN, state=tmp_path, http=http)
    save_google(
        app.state.secrets,
        {
            "refresh_token": "rt",
            "access_token": "old",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, READ_SCOPE],
        },
    )
    app.state.oauth_state = "st"
    res = TestClient(app).get("/v1/gmail/callback?code=abc&state=st")
    assert res.status_code == 200
    profile = load_google(app.state.secrets)
    assert COMPOSE_SCOPE in profile["scopes"]
    assert READ_SCOPE in profile["scopes"]
    assert DRIVE_SCOPE in profile["scopes"]


def test_disconnect_deletes_google_and_gmail_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    app = create_app(token=TOKEN, state=tmp_path)
    save_google(
        app.state.secrets,
        {"refresh_token": "rt", "email": "fisher@example.com", "scopes": [COMPOSE_SCOPE]},
    )
    TestClient(app).post("/v1/gmail/disconnect", headers={TOKEN_HEADER: TOKEN})
    assert load_google(app.state.secrets) == {}


def test_fake_http_get_matches_path_without_query():
    http = FakeHttp({"https://example.test/v1/items": {"ok": True}})
    assert http.get("https://example.test/v1/items?q=hi&maxResults=5") == {"ok": True}


def test_gmail_draft_refreshes_on_401(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(
        secrets,
        {
            "refresh_token": "rt",
            "access_token": "stale",
            "email": "fisher@example.com",
            "scopes": [COMPOSE_SCOPE, READ_SCOPE],
        },
    )

    class Once401(FakeHttp):
        def post(self, url, **kwargs):
            self.calls.append({"url": url, **kwargs})
            if url == DRAFTS_URL and not any(
                c["url"] == "https://oauth2.googleapis.com/token" for c in self.calls[:-1]
            ):
                raise HttpError(401, url)
            if url == "https://oauth2.googleapis.com/token":
                return {"access_token": "fresh"}
            if url == DRAFTS_URL:
                return {"id": "r-new"}
            raise RuntimeError(url)

    api = GmailApi(secrets, http=Once401(), client_id="cid", client_secret="sec")
    result = api.create_draft(to="a@b.c", subject="s", body="b")
    assert result["id"] == "r-new"
    assert load_google(secrets)["access_token"] == "fresh"
```

`HttpError` on `coworker.apollo` next to `LiveHttp`:

```python
class HttpError(RuntimeError):
    def __init__(self, status: int, url: str = "", body: object | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.url = url
        self.body = body
```

`LiveHttp.get` / `post`: accept `params: dict | None = None`. On `res.status_code >= 400` raise `HttpError(status, url, body)` instead of `raise_for_status()`. If the response `content-type` is JSON (or the body starts with `{` / `[`), return `res.json()`; otherwise return `res.text`. Drive export is `text/plain`. Existing Gmail `_from_http_error` must handle `HttpError` first (message from `body` or `HTTP {status}`), then the current `.response` path so older HTTPStatusError fixtures still work.

Add next to `test_fake_http_get_matches_path_without_query`:

```python
def test_fake_http_get_returns_string_body():
    http = FakeHttp({"https://example.test/export": "plain text body"})
    assert http.get("https://example.test/export?alt=media") == "plain text body"
```

`FakeHttp.get` and `post`: if `url` misses `self.routes`, retry `url.split("?")[0]`. If the route value is a `str`, return it as-is. If it is an `Exception`, raise it.

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_gmail.py::test_auth_url_includes_readonly_and_compose tests/test_gmail.py::test_callback_stores_email_and_scopes tests/test_gmail.py::test_callback_merges_drive_scope_into_existing_profile tests/test_gmail.py::test_disconnect_deletes_google_and_gmail_keys tests/test_gmail.py::test_fake_http_get_matches_path_without_query tests/test_gmail.py::test_gmail_draft_refreshes_on_401 -v
```

Expected: FAIL (`READ_SCOPE` / `load_google` / retry / merge missing).

- [ ] **Step 3: Minimal implementation**

`google_oauth.py` — add:

```python
GOOGLE_KEY = "google"
GMAIL_KEY = "gmail"  # legacy mirror
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
BASE_SCOPES = (COMPOSE_SCOPE, READ_SCOPE, EMAIL_SCOPE)

def authorization_url(*, client_id: str, redirect_uri: str, state: str, extra_scopes: tuple[str, ...] = ()) -> str:
    scopes = " ".join(dict.fromkeys([*BASE_SCOPES, *extra_scopes]))
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scopes,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })
    return f"{AUTH_URL}?{query}"

def load_google(secrets: SecretStore) -> dict:
    return secrets.get(GOOGLE_KEY) or secrets.get(GMAIL_KEY) or {}

def save_google(secrets: SecretStore, profile: dict) -> None:
    secrets.put(GOOGLE_KEY, profile)
    secrets.put(GMAIL_KEY, profile)

def has_scope(profile: dict, scope: str) -> bool:
    return scope in (profile.get("scopes") or [])

def merge_scopes(existing: list[str] | None, granted: str | None) -> list[str]:
    out: list[str] = []
    for item in [*(existing or []), *(granted or "").split()]:
        if item and item not in out:
            out.append(item)
    return out
```

Keep `COMPOSE_SCOPE` / `EMAIL_SCOPE` / `exchange_code` / `refresh_access_token` as they are.

`gmail.py`: `_access_token` and `gmail_from_secrets` use `load_google` / `save_google`. If a cached token is used and the next request raises `HttpError` with status 401, clear `access_token`, refresh, retry once. Do not invent a send method.

`server.py` callback: after exchange, `save_google` with `scopes = merge_scopes(load_google(...).get("scopes"), tokens.get("scope"))`. Keep `/v1/gmail/callback` as the only Google loopback. One `app.state.oauth_state`. Status still never returns tokens. Disconnect deletes `GOOGLE_KEY` and `GMAIL_KEY`.

Bump `SLICE = 19`. Update both health tests to `== 19`.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_gmail.py tests/test_settings.py tests/test_chat.py::test_health_reports_slice_and_model -q
```

Expected: PASS. Health slice 19.

- [ ] **Step 5: Click in the window**

Relaunch backend so `.env` is loaded. Connect Gmail. After Fisher adds the redirect URI, strip shows the Gmail address. Secrets.json stays 0600; the strip never prints the refresh token.

- [ ] **Step 6: Commit**

```bash
git add desktop/coworker/gmail.py desktop/coworker/connectors/google_oauth.py desktop/coworker/apollo.py desktop/coworker/server.py desktop/tests/test_gmail.py desktop/tests/test_chat.py
git commit -m "feat(pass3): Gmail connect stores email and readonly+compose scopes"
```

Pause here unless told to continue.

---

### Task 2: Slice 20 — session store

**Files:**
- Modify: `desktop/coworker/store.py`
- Test: `desktop/tests/test_store.py`

**Interfaces:**
- Consumes: existing `append` / `load` / `index` keyed by `sid`.
- Produces:

```python
def list_sessions(self) -> list[dict[str, Any]]:
    # {session_id, title, n_msgs, updated_at} newest updated_at first
    # Omit ids that start with "sched-" (scheduler run sessions, slice 27)

def create_session(self, sid: str | None = None) -> dict[str, Any]:
    # uuid4 hex if sid is None; title None; n_msgs 0; empty jsonl created

def rename_session(self, sid: str, title: str) -> dict[str, Any] | None:
    # sets title; later appends must not overwrite (COALESCE already does this)

def open_session_id(self) -> str | None:
    return self.get_setting("open_session_id")

def set_open_session(self, sid: str) -> None:
    self.set_setting("open_session_id", sid)
```

- [ ] **Step 1: Write the failing tests**

```python
def test_list_sessions_newest_first(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("a", {"role": "user", "content": "first chat"})
    store.append("b", {"role": "user", "content": "second chat"})
    ids = [row["session_id"] for row in store.list_sessions()]
    assert ids == ["b", "a"]
    assert store.list_sessions()[0]["title"] == "second chat"


def test_create_session_is_empty_and_open(tmp_path):
    store = ConversationStore(tmp_path)
    row = store.create_session()
    assert row["n_msgs"] == 0
    assert row["title"] is None
    assert store.load(row["session_id"]) == []
    store.set_open_session(row["session_id"])
    assert store.open_session_id() == row["session_id"]


def test_rename_survives_later_append(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("s1", {"role": "user", "content": "original title here"})
    store.rename_session("s1", "Alyssa outreach")
    store.append("s1", {"role": "user", "content": "later"})
    assert store.index("s1")["title"] == "Alyssa outreach"


def test_list_sessions_hides_scheduler_runs(tmp_path):
    store = ConversationStore(tmp_path)
    store.append("chat", {"role": "user", "content": "hello"})
    store.create_session("sched-1")
    ids = [row["session_id"] for row in store.list_sessions()]
    assert ids == ["chat"]
    assert store.load("sched-1") == []
```

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_store.py -v
```

Expected: FAIL on missing methods.

- [ ] **Step 3: Implement**

`list_sessions`: `SELECT session_id, title, n_msgs, updated_at FROM sessions WHERE session_id NOT LIKE 'sched-%' ORDER BY updated_at DESC, rowid DESC`. (`rowid` so two appends in the same SQLite second stay newest-first.)

`create_session`: `sid = sid or uuid.uuid4().hex`; insert row; `self._file(sid).touch()`; return index row with title None.

`rename_session`: `UPDATE sessions SET title = ? WHERE session_id = ?`. Empty title rejected (return None).

Keep `COALESCE(sessions.title, excluded.title)` in `append` so rename sticks.

Do not add pin/archive/workspace columns.

- [ ] **Step 4: Tests pass**

```bash
.venv/bin/pytest tests/test_store.py -q
```

- [ ] **Step 5: Commit**

```bash
git add desktop/coworker/store.py desktop/tests/test_store.py
git commit -m "feat(pass3): list, create, rename, and remember the open session"
```

---

### Task 3: Slice 20 — session HTTP + WS bind

**Files:**
- Modify: `desktop/coworker/server.py` (drop `SESSION_ID = "main"`)
- Test: `desktop/tests/test_sessions.py` (create)
- Modify: `desktop/tests/test_chat.py` (health slice 20; existing WS still works)

**Interfaces:**
- Consumes: Task 2 store methods.
- Produces:

```
GET    /v1/sessions              {sessions, open_id}
POST   /v1/sessions              {id, title, n_msgs}  and sets open_id
GET    /v1/sessions/{id}         {id, title, messages}  or 404
PATCH  /v1/sessions/{id}         body {title} → {id, title}; sets open_id
GET    /v1/conversation          alias of GET current session (keep for the window)
```

WS `{type:"chat", text, session_id?}`: if `session_id` present, use it; else `store.open_session_id()` or create a uuid session (not `"main"` unless that file already exists).

On **every** chat message, rebuild `history` from disk for that sid plus the system prompt. Do not keep a single `history` or `sid` from connect time. After a user message, `store.set_open_session(sid)`.

Boot: if `open_session_id` missing and `conversations/main.jsonl` exists, set open to `"main"` so Fisher's current transcript is not orphaned.

- [ ] **Step 1: Write the failing tests** (`tests/test_sessions.py`)

```python
from fastapi.testclient import TestClient
from coworker.provider import FakeProvider
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-sessions"


def client(tmp_path):
    return TestClient(create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path))


def test_post_session_then_list(tmp_path):
    c = client(tmp_path)
    created = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    assert created["n_msgs"] == 0
    listing = c.get("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    assert listing["open_id"] == created["id"]
    assert listing["sessions"][0]["session_id"] == created["id"]


def test_patch_title(tmp_path):
    c = client(tmp_path)
    sid = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    res = c.patch(f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN}, json={"title": "Alyssa"})
    assert res.json()["title"] == "Alyssa"
    listed = c.get("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    assert listed["sessions"][0]["title"] == "Alyssa"


def test_get_unknown_session_404(tmp_path):
    c = client(tmp_path)
    res = c.get("/v1/sessions/does-not-exist", headers={TOKEN_HEADER: TOKEN})
    assert res.status_code == 404


def test_ws_chat_uses_named_session(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(deltas=("ok",)), state=tmp_path)
    c = TestClient(app)
    sid = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    with c.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "hello there", "session_id": sid})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert any(e["type"] == "turn_end" for e in events)
    body = c.get(f"/v1/sessions/{sid}", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["messages"][0]["content"] == "hello there"
    assert body["title"] == "hello there"


def test_new_session_does_not_read_old_transcript(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(), state=tmp_path)
    app.state.store.append("old", {"role": "user", "content": "secret old chat"})
    c = TestClient(app)
    fresh = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()
    body = c.get(f"/v1/sessions/{fresh['id']}", headers={TOKEN_HEADER: TOKEN}).json()
    assert body["messages"] == []


def test_ws_two_sessions_do_not_leak_history(tmp_path):
    app = create_app(token=TOKEN, provider=FakeProvider(deltas=("ok",)), state=tmp_path)
    c = TestClient(app)
    a = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    b = c.post("/v1/sessions", headers={TOKEN_HEADER: TOKEN}).json()["id"]
    with c.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "alpha secret", "session_id": a})
        while True:
            if ws.receive_json()["type"] in ("turn_end", "error"):
                break
        ws.send_json({"type": "chat", "text": "bravo only", "session_id": b})
        while True:
            if ws.receive_json()["type"] in ("turn_end", "error"):
                break
    msgs_a = c.get(f"/v1/sessions/{a}", headers={TOKEN_HEADER: TOKEN}).json()["messages"]
    msgs_b = c.get(f"/v1/sessions/{b}", headers={TOKEN_HEADER: TOKEN}).json()["messages"]
    assert any("alpha secret" in str(m.get("content")) for m in msgs_a)
    assert not any("alpha secret" in str(m.get("content")) for m in msgs_b)
    assert any("bravo only" in str(m.get("content")) for m in msgs_b)
```

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_sessions.py -v
```

- [ ] **Step 3: Implement routes and bind WS**

In `create_app`, replace `app.state.session_id = SESSION_ID` with:

```python
store = app.state.store
if store.open_session_id() is None:
    if (store.conv_dir / "main.jsonl").exists() or store.index("main"):
        store.set_open_session("main")
    else:
        store.set_open_session(store.create_session()["session_id"])
```

WS chat, every message:

```python
sid = incoming.get("session_id") or store.open_session_id()
raw = store.load(sid)
loaded = _close_open_tool_calls(raw)
if loaded != raw:
    store.replace_all(sid, loaded)
history = [{"role": "system", "content": system_prompt(...)}] + loaded
```

Then append the user message and run the existing step loop against **that** `history` / `sid`. After the user message, `store.set_open_session(sid)`.

Keep `/v1/conversation` as GET of the open session so the current window boot still works until Task 4.

**Rewrite existing tests that assume `main`.** After this task, a fresh `tmp_path` has no `main.jsonl`, so boot mints a uuid. In `tests/test_chat.py`:

- `test_conversation_empty_before_chat`: `body["id"] == app.state.store.open_session_id()` (not `"main"`). Messages still `[]`.
- `test_ws_persists_messages_to_disk`: jsonl path is `conversations / f"{open_session_id()}.jsonl"`, not `main.jsonl`.
- `test_ws_heals_orphaned_tool_call_before_model`: `sid = built.state.store.open_session_id()` — **do not** use `built.state.session_id` (that attribute is gone).
- `test_new_backend_reloads_history` and any other `main.jsonl` / `session_id == "main"` assertions in this file: same rewrite.

Bump `SLICE = 20`. Update both health tests to `== 20`.

- [ ] **Step 4: Tests pass**

```bash
.venv/bin/pytest tests/test_sessions.py tests/test_chat.py tests/test_store.py -q
```

- [ ] **Step 5: Commit**

```bash
git add desktop/coworker/server.py desktop/tests/test_sessions.py desktop/tests/test_chat.py
git commit -m "feat(pass3): session list, create, rename, and per-id chat"
```

Pause. One click: not yet. That is Task 4.

---

### Task 4: Slice 20 — session rail + Warm Operator shell

**Files:**
- Modify: `desktop/surfaces/gui/src/api.ts`
- Modify: `desktop/surfaces/gui/src/App.tsx`
- Modify: `desktop/surfaces/gui/src/styles.css`
- Test: `desktop/tests/test_design_tokens.py`

Gap-doc slice 28 (layout) lands here. Do not add a later layout task. Health stays 20.

**Interfaces:**
- Consumes: Task 3 HTTP.
- Produces: `getSessions`, `createSession`, `getSession`, `renameSession` in `api.ts`. Chat `send(text)` includes `session_id`.

Window jobs (copy grok-bot *jobs*, not chrome):

1. Left rail ~232px: session titles, New chat, active row.
2. New chat → POST /v1/sessions → blank transcript.
3. Click row → GET /v1/sessions/{id} → `itemsFromMessages`.
4. Title = first user line unless PATCH renamed. Double-click a rail row to rename (PATCH). Empty title is rejected.
5. Last open id is whatever the backend stored; boot uses `open_id`.
6. Shell:

```
┌────────232px────────┬─────────────── flex 1 ────────────────┐
│ Sourcecado          │ eyebrow · model                       │
│ New chat            │ transcript                            │
│ session list        │ composer                              │
│                     ├───────────────────────────────────────┤
│                     │ connector / inbox strip (filled Task 5)│
└─────────────────────┴───────────────────────────────────────┘
```

Do not copy OW Sidebar.tsx (personas, cloud, automations). Do not copy grok 280px sand sidebar. Not a spreadsheet. Hairline `#E7E3DA` borders. Accent only on active session + Allow.

- [ ] **Step 1: Write the failing CSS tests**

Do not assert `".strip" in css` — `.strip-btn` already matches that substring today, so the test would not fail.

```python
def test_session_rail_width():
    css = CSS.read_text(encoding="utf-8")
    assert ".rail {" in css
    assert "232px" in css
    assert "#FAF8F3" in css
    assert "#5B8C2A" in css


def test_warm_operator_shell():
    css = CSS.read_text(encoding="utf-8")
    assert "--canvas: #FAF8F3" in css
    assert "--accent: #5B8C2A" in css
    assert ".rail {" in css and "232px" in css
    assert ".connector-strip" in css
    assert "#FFFFFF" not in css
    assert "Inter" not in css
```

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_design_tokens.py::test_session_rail_width tests/test_design_tokens.py::test_warm_operator_shell -v
```

- [ ] **Step 3: Implement API + rail + shell**

`api.ts`:

```ts
export type SessionRow = { session_id: string; title: string | null; n_msgs: number; updated_at: string };

export async function getSessions(): Promise<{ sessions: SessionRow[]; open_id: string | null }> {
  const res = await get("/v1/sessions");
  if (!res.ok) throw new Error(`sessions ${res.status}`);
  return res.json();
}

export async function createSession(): Promise<{ id: string; title: string | null; n_msgs: number }> {
  const res = await fetch(`${httpBase()}/v1/sessions`, {
    method: "POST",
    headers: { "X-Club-Token": apiToken() },
  });
  if (!res.ok) throw new Error(`sessions ${res.status}`);
  return res.json();
}

export async function getSession(id: string): Promise<Conversation> {
  const res = await get(`/v1/sessions/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(`session ${res.status}`);
  const body = await res.json();
  return { id: body.id, title: body.title, messages: body.messages };
}

export async function renameSession(id: string, title: string): Promise<{ id: string; title: string }> {
  const res = await fetch(`${httpBase()}/v1/sessions/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { "X-Club-Token": apiToken(), "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`rename ${res.status}`);
  return res.json();
}
```

Change `openChat` so every chat includes the open session:

```ts
export function openChat(onEvent: (event: ChatEvent) => void): {
  send: (text: string, sessionId: string) => void;
  approve: (id: string, decision: "allow" | "deny") => void;
  close: () => void;
} {
  // ...
  return {
    send(text: string, sessionId: string) {
      push({ type: "chat", text, session_id: sessionId });
    },
    approve(id, decision) {
      push({ type: "permission", id, decision });
    },
    close() {
      ws.close();
    },
  };
}
```

`App` keeps `sessionId` in state. New chat, row click, boot, and **Allow/Deny** all use that same id. `send(draft, sessionId)` on submit.

On New chat: `createSession()`, `setItems([])`, `setSessionId(id)`. On row click: `getSession(id)` then `itemsFromMessages`. Boot: `getSessions()` then `getSession(open_id)`.

CSS: `.app` becomes a row: `.rail` 232px / `.main` flex 1. Drop `max-width: 720px` centered column. Canvas stays `#FAF8F3`. Active rail row uses accent tint `#EBF1DF`, not a cold blue. Add `.connector-strip` for the connector/inbox row; Task 5 fills it.

- [ ] **Step 4: Tests pass**

```bash
.venv/bin/pytest tests/test_design_tokens.py tests/test_sessions.py -q
```

- [ ] **Step 5: Click**

New chat blanks the transcript. Old chat reopens. Quit/relaunch restores the last open id. At 1280px and ~800px the rail stays readable.

- [ ] **Step 6: Commit**

```bash
git add desktop/surfaces/gui/src/api.ts desktop/surfaces/gui/src/App.tsx desktop/surfaces/gui/src/styles.css desktop/tests/test_design_tokens.py
git commit -m "feat(pass3): session rail with new chat and last-open restore"
```

Pause.

---

### Task 5: Slice 21 — connector panel

**Files:**
- Modify: `desktop/coworker/server.py` (`GET /v1/connectors`)
- Modify: `desktop/coworker/connectors/google_oauth.py` (`has_scope` already in Task 1)
- Modify: `desktop/surfaces/gui/src/api.ts`, `App.tsx`
- Test: `desktop/tests/test_connectors.py` (create)
- Modify: `desktop/tests/test_settings.py` (settings still never leak secrets)

**Interfaces:**
- Consumes: Google profile scopes; `APOLLO_API_KEY`; Granola tokens (missing until Task 10).
- Produces:

```python
# GET /v1/connectors
{
  "connectors": [
    {"id": "gmail", "title": "Gmail", "status": "connected"|"missing", "email": str|None},
    {"id": "drive", "title": "Drive", "status": "connected"|"missing", "email": str|None},
    {"id": "calendar", "title": "Calendar", "status": "connected"|"missing", "email": str|None},
    {"id": "apollo", "title": "Apollo", "status": "configured"|"missing", "email": None},
    {"id": "granola", "title": "Granola", "status": "connected"|"missing", "email": None},
  ]
}
```

Gmail connected if refresh_token and compose+readonly in scopes (or legacy gmail key with a refresh token). Drive connected if `DRIVE_SCOPE` in scopes. Calendar if `CALENDAR_SCOPE`. Apollo `configured` if key present — never the key. Granola connected if `secrets.get("mcp-oauth:granola")` has tokens (empty until Task 10).

Connect Gmail remains the Google identity. This task is **status-only chips** for Drive/Calendar/Granola (label + connected/missing). Do not add disabled "coming" buttons and do not 501. Connect buttons land in Tasks 7, 8, and 10 with the real routes.

- [ ] **Step 1: Write the failing tests**

```python
def test_connectors_never_include_secrets(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path, apollo_key="sk-secret")
    app.state.secrets.put(
        "google",
        {"refresh_token": "rt-secret", "email": "fisher@example.com", "scopes": [
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/userinfo.email",
        ]},
    )
    body = TestClient(app).get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    blob = str(body)
    assert "rt-secret" not in blob
    assert "sk-secret" not in blob
    by_id = {c["id"]: c for c in body["connectors"]}
    assert by_id["gmail"]["status"] == "connected"
    assert by_id["gmail"]["email"] == "fisher@example.com"
    assert by_id["drive"]["status"] == "missing"
    assert by_id["calendar"]["status"] == "missing"
    assert by_id["apollo"]["status"] == "configured"
    assert by_id["granola"]["status"] == "missing"
```

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_connectors.py -v
```

- [ ] **Step 3: Implement GET /v1/connectors** and fill `.strip` with the five statuses. Reuse the existing Gmail connect/disconnect buttons.

Bump `SLICE = 21`. Update both health tests to `== 21`.

- [ ] **Step 4: Tests pass + click**

Strip shows Gmail email, Apollo on/off, Drive/Calendar/Granola missing. No secrets in the DOM.

- [ ] **Step 5: Commit**

```bash
git add desktop/coworker/server.py desktop/coworker/connectors/google_oauth.py desktop/surfaces/gui/src desktop/tests/test_connectors.py desktop/tests/test_chat.py
git commit -m "feat(pass3): connector panel status without secrets"
```

Pause.

---

### Task 6: Slice 22 — Gmail search + read

**Files:**
- Modify: `desktop/coworker/gmail.py` (`search`, `read`)
- Modify: `desktop/coworker/tools.py`
- Modify: `desktop/coworker/permissions.py`
- Test: `desktop/tests/test_gmail.py`, `desktop/tests/test_permissions.py`, `desktop/tests/test_tools.py`

`FakeHttp` path-prefix matching and `HttpError` already landed in Task 1. `LiveHttp.get/post` may need a `params=` argument here if Gmail search passes query dicts instead of a prebuilt URL.

**Interfaces:**
- Consumes: Google profile with `gmail.readonly`.
- Produces:

```
gmail_search(query: str, max_results: int = 10) -> {messages: [{id, threadId, from, subject, date}]}
gmail_read(message_id: str) -> {id, from, to, subject, date, snippet, body}
```

HTTP (copy OW, Club names):

- Search: `GET https://gmail.googleapis.com/gmail/v1/users/me/messages?q=&maxResults=` then metadata GET per id (`format=metadata`, headers From, Subject, Date). Cap 10.
- Read: `GET .../messages/{id}?format=full`. Flatten `payload.parts` to text. Cap body at 8000 chars.

AUTO: `gmail_search`, `gmail_read`. ASK: `gmail_draft` unchanged. No `gmail_send`. FakeGmail gains `search`/`read` lists so execute tests never hit the network.

- [ ] **Step 1: Write the failing tests**

```python
MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

def test_gmail_search_is_auto_and_draft_still_asks():
    assert decide("gmail_search").needs_user is False
    assert decide("gmail_read").needs_user is False
    assert decide("gmail_draft").needs_user is True
    assert decide("gmail_send").allowed is False


def test_gmail_search_execute_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    http = FakeHttp({
        MESSAGES_URL: {"messages": [{"id": "m1", "threadId": "t1"}]},
        f"{MESSAGES_URL}/m1": {
            "id": "m1",
            "threadId": "t1",
            "payload": {"headers": [
                {"name": "From", "value": "Alyssa <a@berkeley.edu>"},
                {"name": "Subject", "value": "hi"},
                {"name": "Date", "value": "Mon, 24 Aug 2026 09:00:00 -0700"},
            ]},
        },
    })
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    out = api.search(query="from:alyssa", max_results=5)
    assert out["messages"][0]["subject"] == "hi"
    assert out["messages"][0]["from"] == "Alyssa <a@berkeley.edu>"
    assert "send" not in str(http.calls)


def test_gmail_read_execute_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    http = FakeHttp({
        f"{MESSAGES_URL}/m1": {
            "id": "m1",
            "snippet": "hello",
            "payload": {
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"hello body").decode().rstrip("=")},
                "headers": [{"name": "Subject", "value": "hi"}],
            },
        }
    })
    api = GmailApi(secrets, http=http, client_id="cid", client_secret="sec")
    out = api.read(message_id="m1")
    assert "hello body" in out["body"]
    assert out["sent"] is not True


def test_ws_gmail_search_does_not_ask(tmp_path):
    http = FakeHttp({
        MESSAGES_URL: {"messages": [{"id": "m1", "threadId": "t1"}]},
        f"{MESSAGES_URL}/m1": {
            "id": "m1",
            "payload": {"headers": [{"name": "Subject", "value": "hi"}]},
        },
    })
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="gmail_search", arguments={"query": "alyssa"})]},
            {"deltas": ("found one",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [READ_SCOPE]})
    app.state.gmail = gmail_from_secrets(app.state.secrets, http=http)
    client = TestClient(app)
    with client.websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "search mail for Alyssa"})
        events = _drain(ws)
    assert "permission_required" not in [e["type"] for e in events]
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is True
    assert finished["result"]["messages"][0]["subject"] == "hi"
```

Imports: `decide` from permissions, `save_google` / `READ_SCOPE` from `google_oauth`, `FakeHttp` from apollo, `gmail_from_secrets` from gmail, `base64` as needed.

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_gmail.py tests/test_permissions.py -q
```

- [ ] **Step 3: Implement search/read, schemas, AUTO set, execute() branches.** Kernel prompt: mention `gmail_search` / `gmail_read` auto, `gmail_draft` asks, never send.

Bump `SLICE = 22`. Update both health tests to `== 22`.

- [ ] **Step 4: Tests pass**

```bash
.venv/bin/pytest tests/test_gmail.py tests/test_permissions.py tests/test_tools.py tests/test_chat.py -q
```

- [ ] **Step 5: Click** — "search my mail for Alyssa" shows a tool card with subjects, no Allow. Draft still asks.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(pass3): gmail_search and gmail_read, still no send"
```

Pause.

---

### Task 7: Slice 23 — Google Drive

**Files:**
- Create: `desktop/coworker/drive.py`
- Modify: `google_oauth.py` (DRIVE_SCOPE already in Task 1), `server.py` (`POST /v1/connectors/drive/connect` extra scopes), `tools.py`, `permissions.py`
- Modify: `desktop/surfaces/gui/src/api.ts`, `App.tsx` (Connect Drive button on the Drive chip)
- Test: `desktop/tests/test_drive.py`

**Interfaces:**
- Consumes: same Google refresh token.
- Produces:

```
drive_search(query: str, max_results: int = 10) -> {files: [{id, name, mimeType, modifiedTime}]}
drive_read(file_id: str, max_chars: int = 20000) -> {id, name, mimeType, content, truncated}
```

HTTP (OW `drive_search_files` / `drive_read_file`):

- Search: `GET https://www.googleapis.com/drive/v3/files` with `q=(name contains '...' or fullText contains '...') and trashed=false` and `fields=files(id,name,mimeType,modifiedTime,size,webViewLink)`.
- Read: GET metadata, then export Docs/Sheets/Slides (`document→text/plain`, `spreadsheet→text/csv`, `presentation→text/plain`) or `alt=media` for ordinary files. Cap `max_chars`.

Connect Drive: `authorization_url(..., extra_scopes=(DRIVE_SCOPE,))` using the same callback `/v1/gmail/callback` (one loopback, one `oauth_state`). After consent the Task 1 callback merges `tokens["scope"]`. Readonly only. Missing Drive scope → tool error `"Drive is not connected."`

AUTO both tools.

- [ ] **Step 1: Write the failing tests** (`tests/test_drive.py`)

```python
from fastapi.testclient import TestClient

from urllib.parse import unquote

from coworker.apollo import FakeHttp
from coworker.connectors.google_oauth import DRIVE_SCOPE, save_google
from coworker.drive import DriveApi
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-drive"
FILES_URL = "https://www.googleapis.com/drive/v3/files"


def test_drive_tools_are_auto():
    assert decide("drive_search").needs_user is False
    assert decide("drive_read").needs_user is False


def test_drive_search_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp({
        FILES_URL: {
            "files": [{"id": "f1", "name": "Q3 plan", "mimeType": "text/plain", "modifiedTime": "2026-08-01T00:00:00Z"}]
        }
    })
    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").search("Q3 plan")
    assert out["files"][0]["id"] == "f1"
    assert "/upload" not in str(http.calls)


def test_drive_read_exports_google_doc(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    http = FakeHttp({
        f"{FILES_URL}/f1": {"id": "f1", "name": "doc", "mimeType": "application/vnd.google-apps.document"},
        f"{FILES_URL}/f1/export": "plain text body",
    })
    out = DriveApi(secrets, http=http, client_id="cid", client_secret="sec").read("f1")
    assert out["content"] == "plain text body"
    assert out["truncated"] is False


def test_drive_connect_url_requests_readonly(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "sec")
    res = TestClient(create_app(token=TOKEN, state=tmp_path)).post(
        "/v1/connectors/drive/connect", headers={TOKEN_HEADER: TOKEN}
    )
    assert res.status_code == 200
    decoded = unquote(res.json()["url"])
    assert DRIVE_SCOPE in decoded
    assert "drive.file" not in decoded


def test_ws_drive_search_does_not_ask(tmp_path):
    http = FakeHttp({
        FILES_URL: {"files": [{"id": "f1", "name": "Q3 plan", "mimeType": "text/plain"}]}
    })
    fake = FakeProvider(
        steps=[
            {"tool_calls": [ToolCall(id="c1", name="drive_search", arguments={"query": "Q3"})]},
            {"deltas": ("found it",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [DRIVE_SCOPE]})
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "find Q3 plan"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert "permission_required" not in [e["type"] for e in events]
    assert next(e for e in events if e["type"] == "tool_finished")["ok"] is True
```

`FakeHttp.get` already returns a string body for `/export` from Task 1.

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_drive.py -v
```

- [ ] **Step 3: Implement DriveApi, schemas, AUTO, connect extra-scope URL.** Callback merge already exists from Task 1. Wire the Drive chip Connect button to `POST /v1/connectors/drive/connect` (same "open system browser" job as Gmail). Bump `SLICE = 23`. Update both health tests to `== 23`.

- [ ] **Step 4: Tests pass**

```bash
.venv/bin/pytest tests/test_drive.py tests/test_connectors.py tests/test_permissions.py tests/test_gmail.py::test_callback_merges_drive_scope_into_existing_profile -q
```

- [ ] **Step 5: Click** Connect Drive → extra Google consent → strip says Drive · email. Search a file name in chat.

- [ ] **Step 6: Commit**

```bash
git add desktop/coworker/drive.py desktop/coworker/tools.py desktop/coworker/permissions.py desktop/coworker/server.py desktop/tests/test_drive.py desktop/tests/test_chat.py
git commit -m "feat(pass3): drive_search and drive_read with extra Google scope"
```

Pause.

---

### Task 8: Slice 24 — Google Calendar

**Files:**
- Create: `desktop/coworker/calendar.py`
- Modify: `tools.py`, `permissions.py`, `server.py` (`POST /v1/connectors/calendar/connect`)
- Modify: `desktop/surfaces/gui/src/api.ts`, `App.tsx` (Connect Calendar button)
- Test: `desktop/tests/test_calendar.py`

**Interfaces:**

```
calendar_list(time_min?: str, time_max?: str, max_results: int = 10) -> {events: [{id, summary, start, end}]}
calendar_create(summary, start, end, timezone="America/Los_Angeles", description="") -> {id, summary, htmlLink}
calendar_update(event_id, summary?, start?, end?, description?) -> {id, summary}
```

HTTP (OW gcal_*):

- List: `GET https://www.googleapis.com/calendar/v3/calendars/primary/events?singleEvents=true&orderBy=startTime`
- Create: `POST .../events` with `{summary, start:{dateTime,timeZone}, end:{dateTime,timeZone}}`
- Update: `PATCH .../events/{id}` only provided fields

No delete method on the class. Scope `https://www.googleapis.com/auth/calendar.events` (read+create, not full calendar wipe). AUTO list. ASK create and update. Missing Calendar scope → tool error `"Calendar is not connected."`

Connect Calendar uses the same Gmail callback and `merge_scopes` as Drive.

- [ ] **Step 1: Write the failing tests** (`tests/test_calendar.py`)

```python
from fastapi.testclient import TestClient

from coworker.apollo import FakeHttp
from coworker.calendar import CalendarApi
from coworker.connectors.google_oauth import CALENDAR_SCOPE, save_google
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.secrets import SecretStore
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-cal"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


def test_calendar_permissions():
    assert decide("calendar_list").needs_user is False
    assert decide("calendar_create").needs_user is True
    assert decide("calendar_update").needs_user is True
    assert decide("calendar_delete").allowed is False


def test_calendar_list_fake_http(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp({
        EVENTS_URL: {"items": [{"id": "e1", "summary": "standup", "start": {"dateTime": "2026-08-24T09:00:00-07:00"}, "end": {"dateTime": "2026-08-24T09:30:00-07:00"}}]}
    })
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").list_events()
    assert out["events"][0]["summary"] == "standup"
    assert "delete" not in str(http.calls)


def test_calendar_create_posts_event(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp({EVENTS_URL: {"id": "e2", "summary": "Alyssa", "htmlLink": "https://cal"}})
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").create(
        summary="Alyssa",
        start="2026-08-25T10:00:00",
        end="2026-08-25T10:30:00",
    )
    assert out["id"] == "e2"
    posted = http.calls[0]["json"]
    assert posted["start"]["timeZone"] == "America/Los_Angeles"
    assert http.calls[0]["url"] == EVENTS_URL


def test_calendar_update_asks():
    assert decide("calendar_update").needs_user is True
    assert decide("calendar_update").allowed is False


def test_calendar_update_patches_only_provided_fields(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    http = FakeHttp({f"{EVENTS_URL}/e2": {"id": "e2", "summary": "Alyssa v2"}})
    out = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec").update(
        event_id="e2",
        summary="Alyssa v2",
    )
    assert out["id"] == "e2"
    assert http.calls[0]["json"] == {"summary": "Alyssa v2"}
    assert "start" not in http.calls[0]["json"]


def test_ws_calendar_create_asks_and_deny_writes_nothing(tmp_path):
    http = FakeHttp({EVENTS_URL: {"id": "e2", "summary": "Alyssa"}})
    fake = FakeProvider(
        steps=[
            {
                "tool_calls": [
                    ToolCall(
                        id="c1",
                        name="calendar_create",
                        arguments={
                            "summary": "Alyssa",
                            "start": "2026-08-25T10:00:00",
                            "end": "2026-08-25T10:30:00",
                        },
                    )
                ]
            },
            {"deltas": ("okay",)},
        ]
    )
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, http=http)
    save_google(app.state.secrets, {"refresh_token": "rt", "access_token": "at", "scopes": [CALENDAR_SCOPE]})
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "put Alyssa on the calendar"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "permission_required":
                ws.send_json({"type": "permission", "id": "c1", "decision": "deny"})
            if ev["type"] in ("turn_end", "error"):
                break
    assert http.calls == []
```

- [ ] **Step 2: Run to fail**

```bash
.venv/bin/pytest tests/test_calendar.py -v
```

- [ ] **Step 3: Implement CalendarApi, schemas, AUTO/ASK, connect extra-scope URL.** No delete method on the class. Wire the Calendar chip Connect button the same way as Drive. If you assert the connect URL, `unquote` it first (`CALENDAR_SCOPE` is percent-encoded). Bump `SLICE = 24`. Update both health tests to `== 24`.

- [ ] **Step 4: Tests pass**

```bash
.venv/bin/pytest tests/test_calendar.py tests/test_permissions.py -q
```

- [ ] **Step 5: Click** list this week, then create (Allow).

- [ ] **Step 6: Commit**

```bash
git add desktop/coworker/calendar.py desktop/coworker/tools.py desktop/coworker/permissions.py desktop/coworker/server.py desktop/tests/test_calendar.py desktop/tests/test_chat.py
git commit -m "feat(pass3): calendar_list auto and calendar_create asks"
```

Pause.

---

### Task 9: Slice 25 — Apollo live suite

**Files:**
- Modify: `desktop/tests/test_apollo.py` (add skip-gated live smoke)
- Modify: `desktop/coworker/server.py` (`SLICE = 25`)
- Connector panel already shows configured (Task 5)

**Interfaces:** Existing `search_people` / `enrich_contact` already hit real Apollo URLs with FakeHttp in unit tests. Do not rewrite `apollo.py`. Spend ceiling stays later.

- [ ] **Step 1: Write the live smoke (skipped by default)**

```python
import os
import pytest
from coworker.apollo import LiveHttp, search_people

@pytest.mark.skipif(not os.environ.get("CLUB_RUN_LIVE_SMOKE"), reason="live apollo")
def test_live_apollo_search_returns_no_emails():
    key = os.environ["APOLLO_API_KEY"]
    out = search_people(http=LiveHttp(), api_key=key, organization_name="Abridge", limit=3)
    assert "people" in out
    for person in out["people"]:
        assert "email" not in person or person.get("email") in (None, "")
```

- [ ] **Step 2: Default suite still skip-passes**

```bash
.venv/bin/pytest tests/test_apollo.py -q
```

- [ ] **Step 3: Optional live** — with `CLUB_RUN_LIVE_SMOKE=1` and key in `~/.config/club/.env`, run the smoke. Search still obfuscates last names. Enrich still asks.

Bump `SLICE = 25`. Update both health tests to `== 25`.

- [ ] **Step 4: Commit** `test(pass3): optional live Apollo search smoke`

Pause.

---

### Task 10: Slice 26 — Granola MCP OAuth

**Files:**
- Create: `desktop/coworker/mcp_oauth.py` (owned one-slot OAuth, not a paste of OW `mcp/oauth.py`)
- Modify: `desktop/coworker/mcp.py` (`default_mcp_config`, `load_mcp_config`, `write_default_mcp_json`, `LiveMcp`)
- Modify: `desktop/coworker/permissions.py` (MCP write-name deny)
- Modify: `desktop/requirements.txt` — add `mcp>=1.28.1,<2`
- Modify: `desktop/coworker/server.py` — `POST /v1/connectors/granola/connect`, `POST /v1/connectors/granola/disconnect`, `GET /v1/mcp/oauth/callback` (token-exempt like Gmail callback); origin gate exempts the MCP callback
- Modify: `desktop/surfaces/gui/src/api.ts`, `App.tsx` (Connect Granola button)
- Test: `desktop/tests/test_granola.py`

Do not create `mcp_config.py`. Do not add a `GRANOLA_API_KEY` client. Do not reverse-engineer private Granola HTTP.

**Interfaces:**
- Consumes: FakeMcp naming `mcp__server__tool`.
- Produces:

Default `~/.config/club/mcp.json` (create if missing, do not overwrite extra servers):

```json
{
  "mcpServers": {
    "granola": {
      "type": "http",
      "url": "https://mcp.granola.ai/mcp",
      "auth": "oauth"
    }
  }
}
```

```python
# mcp.py
def default_mcp_config() -> dict: ...
def load_mcp_config(path: Path) -> dict: ...
def write_default_mcp_json(path: Path) -> None: ...  # create if missing

class LiveMcp:
    def __init__(self, *, secrets: SecretStore, config_path: Path, oauth: McpOAuth | None = None): ...
    def schemas(self) -> list[dict]  # empty if no granola tokens
    def has(self, name: str) -> bool
    def call(self, name: str, arguments: dict) -> dict  # sync; refuses write names before HTTP

# mcp_oauth.py
class McpOAuth:
    def __init__(self, secrets: SecretStore, public_url: str): ...
    def start(self, server: str) -> dict  # {url, started}; opens browser; one pending (state, future)
    def finish(self, *, code: str, state: str) -> None  # mismatch does not consume; no waiter → reject
```

SDK pin: `mcp>=1.28.1,<2`. Use `mcp.client.streamable_http.streamable_http_client` + `mcp.ClientSession` + `mcp.client.auth.OAuthClientProvider`. Token storage adapter reads/writes `secrets["mcp-oauth:granola"]` (never mcp.json). Dynamic client registration against `https://mcp.granola.ai/mcp`.

**Sync bridge (required):** `execute()` and `LiveMcp.call` stay sync. WS already runs inside uvicorn's loop, so `asyncio.run()` inside `call` will raise. Run the async SDK on a **dedicated worker thread** (`threading` + `asyncio.run` on that thread, or `anyio.from_thread.run`). Never `asyncio.run` on the uvicorn thread. Tests inject `FakeMcp` and never start the worker.

`create_app`: `write_default_mcp_json(root / "mcp.json")`. If tests pass `mcp=`, use it. Else construct `LiveMcp` + `McpOAuth` and set `app.state.mcp` / `app.state.mcp_oauth`. After connect succeeds, refresh `app.state.openai_tools = list(OPENAI_TOOLS) + app.state.mcp.schemas()`.

Tokens in secrets profile `mcp-oauth:granola`, never in mcp.json. Tools appear as `mcp__granola__<tool>`. Until OAuth succeeds, do not advertise those tools on `openai_tools`. Connect opens the system browser; callback is `http://127.0.0.1:8765/v1/mcp/oauth/callback`. One pending slot: stray local hit cannot consume the flow.

Interactive connect only. Listing tools or running a turn must not open a browser. Missing tokens → tool error `"Granola is not connected."`

v1 Granola is read-only. Two fences **and** the loop must honor deny:

1. `decide()`: if name starts with `mcp__` and the last `__` segment matches `write|create|delete|update` (case-insensitive), return deny (not ask, not auto).
2. The turn loop (today `ws_chat`, later `run_turn`): if `not gate.allowed and not gate.needs_user`, emit `tool_finished` `{ok: False, result: {error}}` and **do not** call `execute` / `mcp.call`.
3. `LiveMcp.call` still refuses those names with `{"error": "granola writes are out of v1"}`.

Other `mcp__*` stay auto. Read OpenWorker `coworker/mcp/oauth.py` as reference only. Do not paste it.

- [ ] **Step 1: Failing tests** (`tests/test_granola.py`)

```python
import json

from fastapi.testclient import TestClient

from coworker.mcp import FakeMcp, LiveMcp
from coworker.permissions import decide
from coworker.provider import FakeProvider, ToolCall
from coworker.server import TOKEN_HEADER, create_app
from coworker.tools import execute

TOKEN = "test-token-granola"


def test_default_mcp_json_names_granola(tmp_path):
    app = create_app(token=TOKEN, state=tmp_path)
    path = tmp_path / "mcp.json"
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["mcpServers"]["granola"]["url"] == "https://mcp.granola.ai/mcp"
    assert data["mcpServers"]["granola"]["auth"] == "oauth"


def test_granola_status_needs_auth(tmp_path):
    body = TestClient(create_app(token=TOKEN, state=tmp_path)).get(
        "/v1/connectors", headers={TOKEN_HEADER: TOKEN}
    ).json()
    gran = next(c for c in body["connectors"] if c["id"] == "granola")
    assert gran["status"] == "missing"


def test_connect_starts_flow(tmp_path, monkeypatch):
    seen = {}
    def fake_start(server):
        seen["name"] = server
        return {"url": "https://example.test/auth", "started": True}
    app = create_app(token=TOKEN, state=tmp_path)
    monkeypatch.setattr(app.state.mcp_oauth, "start", fake_start)
    res = TestClient(app).post("/v1/connectors/granola/connect", headers={TOKEN_HEADER: TOKEN})
    assert res.json()["started"] is True
    assert seen["name"] == "granola"


def test_mcp_write_tools_are_denied():
    d = decide("mcp__granola__create_note")
    assert d.allowed is False
    assert d.needs_user is False
    assert decide("mcp__granola__list_meetings").needs_user is False
    assert decide("mcp__granola__list_meetings").allowed is True


def test_live_mcp_omits_schemas_until_oauth(tmp_path):
    from coworker.mcp import LiveMcp
    from coworker.secrets import SecretStore
    mcp = LiveMcp(secrets=SecretStore(tmp_path / "secrets.json"), config_path=tmp_path / "mcp.json")
    assert mcp.schemas() == []


def test_live_mcp_refuses_writes(tmp_path):
    from coworker.secrets import SecretStore
    mcp = LiveMcp(secrets=SecretStore(tmp_path / "secrets.json"), config_path=tmp_path / "mcp.json")
    result = mcp.call("mcp__granola__create_note", {})
    assert result.get("error")


def test_tool_name_prefix_read_ok():
    mcp = FakeMcp([{"name": "mcp__granola__list_meetings", "handler": lambda a: {"ok": True}}])
    ok, result = execute("mcp__granola__list_meetings", {}, mcp=mcp)
    assert ok is True


def test_ws_denied_mcp_write_does_not_call(tmp_path):
    called = []
    mcp = FakeMcp([{"name": "mcp__granola__create_note", "handler": lambda a: called.append(a) or {"ok": True}}])
    fake = FakeProvider(steps=[
        {"tool_calls": [ToolCall(id="c1", name="mcp__granola__create_note", arguments={})]},
        {"deltas": ("nope",)},
    ])
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, mcp=mcp)
    with TestClient(app).websocket_connect("/ws/chat", subprotocols=["club", TOKEN]) as ws:
        ws.send_json({"type": "chat", "text": "create a granola note"})
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] in ("turn_end", "error"):
                break
    assert called == []
    finished = next(e for e in events if e["type"] == "tool_finished")
    assert finished["ok"] is False


def test_oauth_finish_without_waiter_rejected(tmp_path):
    from coworker.mcp_oauth import McpOAuth
    from coworker.secrets import SecretStore
    oauth = McpOAuth(SecretStore(tmp_path / "secrets.json"), "http://127.0.0.1:8765")
    try:
        oauth.finish(code="abc", state="nope")
        raised = False
    except Exception:
        raised = True
    assert raised is True
    assert SecretStore(tmp_path / "secrets.json").get("mcp-oauth:granola") in (None, {})


def test_oauth_mismatched_state_does_not_consume(tmp_path):
    from coworker.mcp_oauth import McpOAuth
    from coworker.secrets import SecretStore
    oauth = McpOAuth(SecretStore(tmp_path / "secrets.json"), "http://127.0.0.1:8765")
    started = oauth.start("granola")
    assert started["started"] is True
    try:
        oauth.finish(code="abc", state="wrong")
    except Exception:
        pass
    # original pending state still accepted
    # (implement finish so a later correct state still works; assert that by
    # calling start again or inspecting oauth._pending)
    assert getattr(oauth, "_pending", None) is not None
```

Need `ToolCall` / `FakeProvider` imports in this file.

- [ ] **Step 2: Fail, implement owned oauth + default mcp.json, honor `gate.allowed` in the WS loop, SLICE = 26.** Update both health tests to `== 26`.

Do not copy OW stdio MCP, workspace mcp.json, or 25 servers. Do not paste OW `mcp/oauth.py`.

- [ ] **Step 3: Click** Connect Granola → browser → strip says Granola connected. Chat can list meetings. No Granola write.

- [ ] **Step 4: Commit** `feat(pass3): Granola MCP OAuth as mcp__granola__ tools`

Pause.

---

### Task 11: Slice 27 — scheduler runs a turn

**Files:**
- Create: `desktop/coworker/turn.py`
- Modify: `desktop/coworker/server.py` (WS calls `run_turn`)
- Modify: `desktop/coworker/automation/scheduler.py`
- Modify: `desktop/coworker/run.py` (tick thread already exists; do not change the 30s loop besides letting the new default runner run)
- Test: `desktop/tests/test_schedule.py`, `desktop/tests/test_turn.py`

**Interfaces:**
- Consumes: WS loop currently inline in `server.py`.
- Produces:

```python
async def run_turn(
    *,
    text: str,
    sid: str,
    store: ConversationStore,
    provider,
    persona,
    skills,
    inbox: Inbox,
    openai_tools: list,
    execute_kwargs: dict,
    emit: Callable[[dict], Awaitable[None]] | None = None,
    wait_permission: Callable[[str], Awaitable[str]] | None = None,
) -> dict[str, Any]:
    # {status: "ok"|"waiting"|"error"|"stopped", text: str}
```

When `decide(name).needs_user` and `wait_permission` is None: park inbox, return `status="waiting"`. Scheduler uses that path so a due job never blocks the tick thread.

When `not gate.allowed and not gate.needs_user`: same as Task 10 — skip `execute`, append a tool-error message, continue the turn.

Session: `sid = f"sched-{job_id}"`. `create_session(sid)` if missing. Title = job prompt (rename once). `list_sessions` already omits `sched-`. Inbox items still name the tool.

Overlap skip and weekly next_run stay as they are.

Asks → inbox (already tested). After Allow via inbox HTTP, existing `inbox_resolve` executes the tool. v1 does not auto-resume the model after Allow from a scheduled turn. Say that in the kernel: scheduled asks wait in inbox. Double-execute of HTTP Allow + live WS waiter is consolidation Task 12, not this task.

Tick stays **sync**. `run.py` already ticks on a daemon thread. Uvicorn has its own loop. Do not `create_task` onto the uvicorn loop from that thread.

`Scheduler.__init__` keeps `(store, inbox)` and still accepts `runner=` on `tick` (overlap test). Default runner is **not** stored as a half-wired `run_turn`. `create_app` sets it:

```python
def _default_job_runner(job: dict) -> dict:
    sid = f"sched-{job['id']}"
    if app.state.store.index(sid) is None:
        app.state.store.create_session(sid)
        app.state.store.rename_session(sid, str(job.get("prompt") or "scheduled"))
    return asyncio.run(
        run_turn(
            text=str(job["prompt"]),
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
                "http": app.state.http,
                "apollo_key": app.state.apollo_key,
                "skills": app.state.skills,
                "mcp": app.state.mcp,
            },
            emit=None,
            wait_permission=None,
        )
    )

app.state.scheduler = Scheduler(app.state.store, app.state.inbox)
app.state.job_runner = _default_job_runner
```

`Scheduler.tick` uses `runner or getattr(self, "job_runner", None)` — set `scheduler.job_runner = _default_job_runner` on the instance in `create_app`. If neither is set, keep today's stub `{"status": "ok", "result": "tick"}` so unit tests that construct `Scheduler(store, inbox)` still work.

`run.py` `_ticks` stays `app.state.scheduler.tick()` with no extra args. The instance `job_runner` is what fires a real turn. Consolidation Task 12 logs tick failures; do not swallow-fix here beyond wiring.

- [ ] **Step 1: Failing tests**

Keep `test_scheduler_skips_overlap`. Replace the stub-runner inbox test with:

```python
def test_scheduler_due_job_runs_turn_on_sched_session(tmp_path):
    fake = FakeProvider(deltas=("weekly ping",))
    app = create_app(token=TOKEN, provider=fake, state=tmp_path)
    store = app.state.store
    job = store.add_job("0 9 * * 1", "weekly sourcing check-in", next_run_at="2020-01-01T09:00:00")
    ran = app.state.scheduler.tick(now="2020-01-06T10:00:00")
    assert ran[0]["status"] == "ok"
    sid = f"sched-{job['id']}"
    msgs = store.load(sid)
    assert msgs[0]["role"] == "user"
    assert "weekly sourcing check-in" in msgs[0]["content"]
    assert any(m.get("role") == "assistant" for m in msgs)


def test_scheduler_ask_parks_inbox_no_draft(tmp_path):
    fake = FakeProvider(steps=[{
        "tool_calls": [ToolCall(id="c1", name="gmail_draft", arguments={
            "to": "alyssa@berkeley.edu", "subject": "hi", "body": "hello"
        })]
    }])
    gmail = FakeGmail()
    app = create_app(token=TOKEN, provider=fake, state=tmp_path, gmail=gmail)
    store = app.state.store
    store.add_job("0 9 * * 1", "draft Alyssa", next_run_at="2020-01-01T09:00:00")
    app.state.scheduler.tick(now="2020-01-01T09:00:00")
    assert app.state.inbox.pending()[0]["name"] == "gmail_draft"
    assert gmail.drafts == []
```

- [ ] **Step 2: Fail. Extract `run_turn` from `ws_chat`. WS emit = `ws.send_json`. Scheduler emit = no-op. Honor `gate.allowed`. Wire `job_runner` as above. SLICE = 27.** Update both health tests to `== 27`.

- [ ] **Step 3: Pytest green. Click:** add a due job (`next_run_at` in the past via sqlite if needed) or POST `/v1/schedule/tick`. Inbox shows the ask. Allow drafts.

- [ ] **Step 4: Commit** `feat(pass3): scheduler due job runs a real turn and parks asks`

Pause.

---

### Task 12: Code consolidation (no new product)

Not a gap-doc slice. Do **not** bump `SLICE`. Land this after `run_turn` exists so allow/deny has one home.

This slice is cleanup on the landed backend, not sessions/Drive/Calendar/Granola/MEMORY.md. Pass 3 product work stays in Tasks 1–11 and 13.

**Files:** `turn.py` / `server.py` / `inbox.py` / `run.py` / `scheduler.py` / `store.py` / `secrets.py` / `tools.py` / `permissions.py` / `persona.py` / `gmail.py` / `google_oauth.py` / matching tests.

Leave to Pass 3 (already scheduled; do not redo here): google secrets migration, HttpError/FakeHttp, Gmail search/read, Drive, Calendar, Granola OAuth, session rail, `run_turn` extract, MEMORY.md, per-task SLICE bumps.

- [ ] **C1 — Honor `Decision.allowed` in `run_turn`** (P1). If Task 10/11 already added `test_ws_denied_mcp_write_does_not_call` and it still passes, skip. Else: denied name never reaches `execute`. Commit: `fix(club): honor Decision.allowed in turn loop`

- [ ] **C2 — Inbox HTTP Allow + live WS waiter execute once** (P1).

Today HTTP `/v1/inbox/{id}` allow calls `execute`, and `_await_permission` then returns `"allow"` so `run_turn` executes again (`server.py` inbox_resolve + `_await_permission` resolved-item short-circuit).

Test: WS asks `gmail_draft`; HTTP allow; assert `len(gmail.drafts) == 1`; WS turn ends without a second draft.

Fix: HTTP resolve is decision-only when a live waiter exists (do not execute there), **or** `run_turn` consumes a stored result and skips `execute`. Pick the first: waiter owns execute. Scheduler path (`wait_permission is None`) still executes from inbox HTTP as today.

Commit: `fix(club): execute asked tools once across inbox and WS`

- [ ] **C3 — Surface scheduler tick failures** (P1).

`run.py` `_ticks` is `except Exception: pass`. A raising runner leaves `next_run_at` due and retries every 30s with no run row.

Test: inject a runner that raises; `tick` records `status="error"`, advances `next_run_at` (same WEEK rule), and an injectable logger/list captured the traceback. Tick thread logs instead of `pass`.

Commit: `fix(club): surface scheduler tick failures`

- [ ] **C4 — Retire `send_test`** (P2).

Rewrite ask/deny WS tests onto `gmail_draft` + FakeGmail. Remove schema, execute branch, ASK entry, KERNEL mention, buddy frontmatter. Keep deny coverage via `gmail_draft`.

Commit: `refactor(club): retire send_test scaffolding`

- [ ] **C5 — Align `persona.tools` or drop it** (P2).

`persona.tools` is parsed and returned on `/v1/persona` but never filters `openai_tools`. Sourcing.md lists hosted names (`search_memory`, `web_search`). Drop the field from the API and stop parsing it, **or** filter schemas to the frontmatter list and fix the markdown to real Club names. Prefer drop: one tool catalog (`OPENAI_TOOLS` + MCP).

Commit: `fix(club): drop unused persona.tools filter`

- [ ] **C6 — Lock schemas to AUTO∪ASK** (P2).

Test: every `OPENAI_TOOLS` function name is in AUTO or ASK; no ASK∩AUTO; KERNEL names are a subset. MCP names are the prefix exception.

Commit: `test(club): lock tool schemas to permission sets`

- [ ] **C7 — Expose `next_run_at` on `list_schedule`** (P2).

`SELECT` currently drops the column. Include it; `GET /v1/schedule` returns it; update the TS type if the strip still shows the job.

Commit: `fix(club): expose schedule next_run_at`

- [ ] **C8 — Lock `SecretStore` mutations** (P2).

`put`/`delete` load-mutate-write with no lock. Add `threading.RLock` like `ConversationStore`. Test: two threads putting different keys both survive.

Commit: `fix(club): lock SecretStore mutations`

- [ ] **C9 — Tighten `_await_permission` errors** (P2).

Bare `except Exception: sleep` in the waiter. Catch `TimeoutError` (already), `WebSocketDisconnect` (end the turn), JSON errors (same). Do not swallow unknowns.

Commit: `fix(club): tighten permission wait error handling`

- [ ] **C10 — Incremental session reindex on append** (P3).

`append` reloads the whole jsonl to recount. Reindex from prior `n_msgs + 1` and keep `COALESCE` title; `replace_all` still full-scans.

Commit: `perf(club): incremental session reindex on append`

- [ ] **C11 — One helper for Google OAuth client env** (P3).

`google_client_credentials()` in `google_oauth.py` returns the stripped id/secret pair. Call from `gmail_from_secrets` and the connect/callback routes (and Drive/Calendar connect).

Commit: `refactor(club): one helper for Google OAuth client env`

Skip: local `import json` in helpers, `ALTER TABLE` style, UI eyebrow “Slice 18” copy (dies with the shell), comment slice-number staleness.

- [ ] **Step last: full suite green. Commit per C* above, not one mega-commit.**

Pause.

---

### Task 13: Slice 29 — MEMORY.md index

**Files:**
- Modify: `desktop/coworker/store.py` (`_write_memory_index`)
- Modify: `desktop/coworker/server.py` `system_prompt` can keep sqlite list; MEMORY.md is the file the human reads
- Test: `desktop/tests/test_memory_files.py`

Gap-doc slice 28 already landed in Tasks 4–5. This task sets `SLICE = 29`.

**Interfaces:**
- Consumes: sqlite `memories` + `memory/{id}.md`.
- Produces: `memory/MEMORY.md` rebuilt on remember / update / forget.

OpenClaw files are canonical and sqlite is the search index. Club inverts that: sqlite remains source of truth; `MEMORY.md` is a generated index, not a second writable store. `{id}.md` stays the full note. The agent still uses `remember` / `memory_update` / `memory_forget`. Do not add a raw-write-MEMORY.md tool. Do not copy dreaming, USER.md, daily notes, or QMD.

When `list_memories()` exceeds 4000 characters, `system_prompt` injects the MEMORY.md index (whole lines, then stop) instead of dumping every row. Under the cap, keep the current `[#id] content` list.

Format:

```markdown
# Memory index

- [#1] likes matcha
- [#2] Alyssa is at Berkeley
```

Cap the file at the same list sqlite returns (all rows, newest last, matching `list_memories`). If over 4000 characters, keep whole lines and stop (same cap idea as hosted `buildMemoryIndexSection`).

- [ ] **Step 1: Failing tests**

```python
def test_remember_rebuilds_memory_md(tmp_path):
    store = ConversationStore(tmp_path)
    a = store.remember("likes matcha")
    b = store.remember("Alyssa is at Berkeley")
    text = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert f"[#{a['id']}] likes matcha" in text
    assert f"[#{b['id']}] Alyssa is at Berkeley" in text

def test_forget_removes_index_line(tmp_path):
    store = ConversationStore(tmp_path)
    a = store.remember("gone soon")
    store.memory_forget(a["id"])
    text = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "gone soon" not in text
    assert not (tmp_path / "memory" / f"{a['id']}.md").exists()


def test_system_prompt_uses_index_when_over_cap(tmp_path):
    from coworker.server import system_prompt
    store = ConversationStore(tmp_path)
    for i in range(80):
        store.remember("x" * 60 + f" {i}")
    prompt = system_prompt(store)
    assert "Memory index" in prompt or "[#1]" in prompt
    assert len(prompt) < 20000
```

- [ ] **Step 2: Implement `_write_memory_index` called from remember/update/forget (and from `_write_memory_md`). SLICE = 29.** Update both health tests to `== 29`.

- [ ] **Step 3: Pytest green. Click:** remember something, open `~/.config/club/memory/MEMORY.md`, see the line. Sqlite row still matches.

- [ ] **Step 4: Commit** `feat(pass3): MEMORY.md index generated from sqlite`

---

## Verify (every slice)

```bash
cd desktop
.venv/bin/pytest -q
```

Then one click in the window for that slice's job. Pause unless told to keep going.

## NOT in scope

- Gmail send
- Drive writes, Calendar delete, Granola writes
- Granola API-key fallback / private Granola HTTP
- OpenWorker Cloud / Auth0 broker
- Slack / WhatsApp / teams / board
- LinkedIn / Apify / Notion
- Dumping the OpenWorker tree
- OpenClaw dreaming, USER.md, daily notes
- grok-bot chrome, 280px rail, multi-bot roster
- Spend ceiling (still later)
- Renaming `~/.config/club/`
- A second layout rewrite (gap slice 28 is Tasks 4–5)
- `google.py` or `mcp_config.py`
- Auto-resume the model after inbox Allow on a scheduled turn

## What already exists (reuse, do not rebuild)

- `ConversationStore` jsonl + sqlite sessions table — add list/create/rename only
- `get_setting` / `set_setting`
- Gmail compose OAuth + `FakeGmail` + `gmail_draft` ask
- Inbox park/resolve
- Scheduler tick, overlap skip, weekly next_run (stub runner until Task 11)
- Daemon 30s tick thread in `run.py`
- FakeMcp `mcp__server__tool`
- Apollo search (no emails) + enrich ask + LiveHttp/FakeHttp
- `{id}.md` memory files
- Warm Operator CSS tokens (`--canvas`, `--accent`, General Sans)
- Launch token, origin gate, WS subprotocol

## Failure modes

| Path | Failure | Handling |
|---|---|---|
| Gmail callback bad state | HTML 400 | already |
| Drive consent then callback writes BASE_SCOPES | Drive tools 403, strip lies | Task 1 `merge_scopes(tokens.scope)` |
| Expired Google access token | 401 | Task 1 retry once |
| Two chats on one WS | messages land in the wrong jsonl | Task 3 reload history per turn |
| GET unknown session | silent empty transcript | Task 3 404 |
| Drive/Calendar without extra scope | tool error "not connected" | Connect button |
| Granola without OAuth | tool error, no browser hijack | `LiveMcp.schemas()` empty; call error |
| Granola advertises a write tool | would auto-run today | `decide()` deny + loop honors `allowed` + `LiveMcp.call` refuse |
| MCP SDK call inside uvicorn loop | `asyncio.run` RuntimeError | dedicated worker thread (Task 10) |
| Drive export live | `LiveHttp.get` JSON-parses text/plain | Task 1 return text when not JSON |
| Drive connect test | percent-encoded scope | `unquote` (Task 7) |
| Scheduler overlap | double fire | already skipped |
| Scheduler ask | tick thread must not block on WS | park inbox, `waiting` |
| Scheduler tick exception | silent 30s retry | Task 12 records error + log |
| HTTP Allow + live WS waiter | two `gmail_draft`s | Task 12 execute once |
| Secrets in connector JSON | token leak | Task 5 test |
| Disconnect leaves `google` key | Gmail looks connected after disconnect | Task 1 deletes both keys |
| Existing chat tests after Task 3 | still assert `main` | rewrite those tests in Task 3 |

## Parallelization

Sequential. Shared `server.py`, `tools.py`, `App.tsx`. Do not split across worktrees.

Order: 19 → 20 store → 20 HTTP → 20 UI+shell → 21 strip → 22 Gmail read → 23 Drive → 24 Calendar → 25 Apollo smoke → 26 Granola → 27 `run_turn` → consolidation (no SLICE bump) → 29 MEMORY.md.

Drive (23) and Calendar (24) could theoretically parallel after 21+22, but both touch `google_oauth.py` / `server.py` / `App.tsx`. Stay sequential.
