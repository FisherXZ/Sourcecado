import threading

import httpx
import pytest

from coworker.apollo import HttpError
from coworker.calendar import CalendarApi
from coworker.connectors.google_oauth import (
    CALENDAR_SCOPE,
    DRIVE_SCOPE,
    TOKEN_URL,
    GoogleTokens,
    load_google,
    save_google,
)
from coworker.connectors.token_renewal import CredentialRejected, RenewalUnavailable
from coworker.drive import DriveApi
from coworker.gmail import GmailError
from coworker.mcp import LiveMcp
from coworker.mcp_oauth import PRM_URL, RESOURCE, McpOAuth
from coworker.secrets import SecretStore

REFRESH = "rt-renewal-secret"
GRANOLA_KEY = "mcp-oauth:granola"
GRANOLA_TOKEN_URL = "https://auth.granola.test/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
FILES_URL = "https://www.googleapis.com/drive/v3/files"


class TokenHttp:
    """A token endpoint plus any number of API endpoints keyed by bearer token."""

    def __init__(self, token_replies, api=None):
        self.token_replies = list(token_replies)
        self.api = api or {}
        self.calls = []

    def _reply(self, url, headers):
        auth = (headers or {}).get("Authorization", "")
        reply = self.api[url](auth)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def post(self, url, *, headers=None, json=None, data=None, params=None):
        self.calls.append(("POST", url, dict(data or {})))
        if url in (TOKEN_URL, GRANOLA_TOKEN_URL):
            reply = self.token_replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return self._reply(url, headers)

    def get(self, url, *, headers=None, params=None):
        self.calls.append(("GET", url, {}))
        return self._reply(url, headers)

    def patch(self, url, *, headers=None, json=None, data=None, params=None):
        self.calls.append(("PATCH", url, dict(json or {})))
        return self._reply(url, headers)

    def token_posts(self):
        return [call for call in self.calls if call[1] in (TOKEN_URL, GRANOLA_TOKEN_URL)]


def google_secrets(tmp_path, *, access="stale", scopes=(DRIVE_SCOPE, CALENDAR_SCOPE)):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(
        secrets,
        {"refresh_token": REFRESH, "access_token": access, "scopes": list(scopes)},
    )
    return secrets


def bearer_gate(ok_reply):
    """Refuse the stale token, accept any other."""

    def handler(auth):
        if auth in ("Bearer stale", "Bearer "):
            return HttpError(401, "api")
        return ok_reply

    return handler


# Google ---------------------------------------------------------------------


def tokens_for(secrets, http):
    return GoogleTokens(secrets, http, client_id="cid", client_secret="client-secret-value")


def test_google_renewal_saves_the_new_access_token_and_a_rotated_refresh_token(tmp_path):
    secrets = google_secrets(tmp_path)
    http = TokenHttp([{"access_token": "fresh", "refresh_token": "rt-rotated"}])

    assert tokens_for(secrets, http).renew() == "fresh"

    saved = load_google(secrets)
    assert saved["access_token"] == "fresh"
    assert saved["refresh_token"] == "rt-rotated"
    assert http.calls[0][2]["refresh_token"] == REFRESH


def test_google_renewal_keeps_the_refresh_token_when_none_is_rotated(tmp_path):
    secrets = google_secrets(tmp_path)

    tokens_for(secrets, TokenHttp([{"access_token": "fresh"}])).renew()

    assert load_google(secrets)["refresh_token"] == REFRESH


def test_google_renewal_never_saves_an_empty_access_token(tmp_path):
    secrets = google_secrets(tmp_path)

    with pytest.raises(RenewalUnavailable):
        tokens_for(secrets, TokenHttp([{"access_token": ""}])).renew()

    assert load_google(secrets)["access_token"] == "stale"


@pytest.mark.parametrize(
    ("failure", "expected", "kind"),
    [
        (HttpError(400, TOKEN_URL, {"error": "invalid_grant"}), CredentialRejected, None),
        (HttpError(401, TOKEN_URL, {"error": "invalid_client"}), RenewalUnavailable, "setup"),
        (HttpError(503, TOKEN_URL, "busy"), RenewalUnavailable, "unreachable"),
        (httpx.ConnectError(f"refresh_token={REFRESH}"), RenewalUnavailable, "unreachable"),
    ],
)
def test_google_renewal_failures_carry_no_secret(tmp_path, failure, expected, kind):
    secrets = google_secrets(tmp_path)

    with pytest.raises(expected) as raised:
        tokens_for(secrets, TokenHttp([failure])).renew()

    assert getattr(raised.value, "kind", None) == kind
    assert REFRESH not in str(raised.value)
    assert "client-secret-value" not in str(raised.value)
    assert raised.value.__cause__ is None
    assert load_google(secrets)["access_token"] == "stale"


def test_google_renewal_reuses_a_token_another_caller_already_replaced(tmp_path):
    secrets = google_secrets(tmp_path, access="already-fresh")
    http = TokenHttp([])

    assert tokens_for(secrets, http).renew(rejected="stale") == "already-fresh"
    assert http.token_posts() == []


def test_concurrent_google_renewals_exchange_the_refresh_token_once(tmp_path):
    secrets = google_secrets(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class SlowTokenHttp(TokenHttp):
        def post(self, url, **kwargs):
            entered.set()
            release.wait(2)
            return super().post(url, **kwargs)

    http = SlowTokenHttp([{"access_token": "fresh"}, {"access_token": "second"}])
    results = []

    def renew():
        results.append(tokens_for(secrets, http).renew(rejected="stale"))

    threads = [threading.Thread(target=renew) for _ in range(2)]
    for thread in threads:
        thread.start()
    entered.wait(2)
    release.set()
    for thread in threads:
        thread.join(5)

    assert results == ["fresh", "fresh"]
    assert len(http.token_posts()) == 1


def test_drive_renews_once_after_a_refused_token(tmp_path):
    secrets = google_secrets(tmp_path)
    http = TokenHttp([{"access_token": "fresh"}], api={FILES_URL: bearer_gate({"files": []})})
    drive = DriveApi(secrets, http=http, client_id="cid", client_secret="sec")

    drive.search("brief")

    assert len(http.token_posts()) == 1
    assert load_google(secrets)["access_token"] == "fresh"


def test_drive_reports_a_dead_sign_in_without_raw_errors_or_secrets(tmp_path):
    secrets = google_secrets(tmp_path)
    http = TokenHttp(
        [HttpError(400, TOKEN_URL, {"error": "invalid_grant", "refresh_token": REFRESH})],
        api={FILES_URL: bearer_gate({"files": []})},
    )
    drive = DriveApi(secrets, http=http, client_id="cid", client_secret="sec")

    with pytest.raises(GmailError) as raised:
        drive.search("brief")

    assert "Reconnect" in str(raised.value)
    assert REFRESH not in str(raised.value)


def test_drive_does_not_loop_when_the_renewed_token_is_also_refused(tmp_path):
    secrets = google_secrets(tmp_path)
    http = TokenHttp(
        [{"access_token": "fresh"}, {"access_token": "never-used"}],
        api={FILES_URL: lambda auth: HttpError(401, "api")},
    )
    drive = DriveApi(secrets, http=http, client_id="cid", client_secret="sec")

    with pytest.raises(GmailError, match="HTTP 401"):
        drive.search("brief")

    assert len(http.token_posts()) == 1
    assert [call[1] for call in http.calls].count(FILES_URL) == 2


def test_calendar_list_and_update_renew_after_a_refused_token(tmp_path):
    secrets = google_secrets(tmp_path)
    http = TokenHttp(
        [{"access_token": "fresh"}],
        api={
            EVENTS_URL: bearer_gate({"items": []}),
            f"{EVENTS_URL}/evt-1": bearer_gate({"id": "evt-1", "summary": "Coffee"}),
        },
    )
    calendar = CalendarApi(secrets, http=http, client_id="cid", client_secret="sec")

    calendar.list_events()
    updated = calendar.update(event_id="evt-1", summary="Coffee")

    assert updated == {"id": "evt-1", "summary": "Coffee"}
    assert len(http.token_posts()) == 1


def test_calendar_with_no_tokens_reads_not_connected(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"scopes": [CALENDAR_SCOPE]})
    calendar = CalendarApi(secrets, http=TokenHttp([]), client_id="cid", client_secret="sec")

    with pytest.raises(GmailError, match="Calendar is not connected"):
        calendar.list_events()


# Granola ----------------------------------------------------------------------


def granola_secrets(tmp_path, **saved):
    secrets = SecretStore(tmp_path / "secrets.json")
    record = {
        "access_token": "granola-stale",
        "refresh_token": REFRESH,
        "client_id": "granola-client",
        "server": "granola",
        "token_url": GRANOLA_TOKEN_URL,
    }
    record.update(saved)
    secrets.put(GRANOLA_KEY, {key: value for key, value in record.items() if value is not None})
    return secrets


def oauth_for(secrets, http):
    return McpOAuth(secrets, "http://127.0.0.1:8765", http=http, browser_opener=lambda url: True)


def test_granola_renewal_uses_the_saved_token_address_and_keeps_rotation(tmp_path):
    secrets = granola_secrets(tmp_path)
    http = TokenHttp([{"access_token": "granola-fresh", "refresh_token": "rt-granola-rotated"}])

    assert oauth_for(secrets, http).renew() == "granola-fresh"

    method, url, data = http.calls[0]
    assert (method, url) == ("POST", GRANOLA_TOKEN_URL)
    assert data == {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH,
        "client_id": "granola-client",
        "resource": RESOURCE,
    }
    saved = secrets.get(GRANOLA_KEY)
    assert saved["access_token"] == "granola-fresh"
    assert saved["refresh_token"] == "rt-granola-rotated"


def test_granola_grants_saved_before_this_change_rediscover_the_token_address(tmp_path):
    secrets = granola_secrets(tmp_path, token_url=None)
    issuer = "https://auth.granola.test"
    http = TokenHttp(
        [{"access_token": "granola-fresh"}],
        api={
            PRM_URL: lambda auth: {"authorization_servers": [issuer]},
            f"{issuer}/.well-known/oauth-authorization-server": lambda auth: {
                "token_endpoint": GRANOLA_TOKEN_URL
            },
        },
    )

    assert oauth_for(secrets, http).renew() == "granola-fresh"

    assert secrets.get(GRANOLA_KEY)["token_url"] == GRANOLA_TOKEN_URL


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (HttpError(400, GRANOLA_TOKEN_URL, {"error": "invalid_grant"}), CredentialRejected),
        # A refused public client is fixed by signing in again, which re-registers it.
        (HttpError(401, GRANOLA_TOKEN_URL, {"error": "invalid_client"}), CredentialRejected),
        (HttpError(503, GRANOLA_TOKEN_URL, "busy"), RenewalUnavailable),
        (httpx.ConnectError(f"refresh_token={REFRESH}"), RenewalUnavailable),
    ],
)
def test_granola_renewal_failures_carry_no_secret(tmp_path, failure, expected):
    secrets = granola_secrets(tmp_path)

    with pytest.raises(expected) as raised:
        oauth_for(secrets, TokenHttp([failure])).renew()

    assert REFRESH not in str(raised.value)
    assert raised.value.__cause__ is None
    assert secrets.get(GRANOLA_KEY)["access_token"] == "granola-stale"


def test_granola_without_a_refresh_token_cannot_renew(tmp_path):
    secrets = granola_secrets(tmp_path, refresh_token=None)
    http = TokenHttp([])

    with pytest.raises(CredentialRejected):
        oauth_for(secrets, http).renew()

    assert http.calls == []


def test_granola_sign_in_saves_the_token_address(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    oauth = oauth_for(secrets, TokenHttp([{"access_token": "at", "refresh_token": "rt"}]))
    oauth._pending = {
        "server": "granola",
        "state": "s",
        "verifier": "v",
        "client_id": "granola-client",
        "token_url": GRANOLA_TOKEN_URL,
    }

    oauth.finish(code="code", state="s")

    assert secrets.get(GRANOLA_KEY)["token_url"] == GRANOLA_TOKEN_URL


def _unauthorized() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.granola.ai/mcp")
    return httpx.HTTPStatusError(
        "401", request=request, response=httpx.Response(401, request=request)
    )


def live_granola(tmp_path, monkeypatch, token_replies, accepted=("granola-fresh",)):
    """LiveMcp whose fake MCP server accepts only the listed access tokens."""
    secrets = granola_secrets(tmp_path)
    http = TokenHttp(token_replies)
    live = LiveMcp(
        secrets=secrets, config_path=tmp_path / "mcp.json", oauth=oauth_for(secrets, http)
    )
    seen = []

    async def fake_call(self, name, arguments):
        token = self._tokens().get("access_token")
        seen.append(token)
        if token not in accepted:
            raise ExceptionGroup("task group", [_unauthorized()])
        return {"ok": True, "meetings": []}

    monkeypatch.setattr(LiveMcp, "_async_call", fake_call)
    return live, http, seen


def test_granola_calls_renew_an_expired_token_and_retry_once(tmp_path, monkeypatch):
    live, http, seen = live_granola(tmp_path, monkeypatch, [{"access_token": "granola-fresh"}])

    result = live.call("mcp__granola__list_meetings", {})

    assert result == {"ok": True, "meetings": []}
    assert seen == ["granola-stale", "granola-fresh"]
    assert len(http.token_posts()) == 1


def test_granola_calls_stop_after_one_renewal(tmp_path, monkeypatch):
    live, http, seen = live_granola(
        tmp_path, monkeypatch, [{"access_token": "also-refused"}], accepted=()
    )

    result = live.call("mcp__granola__list_meetings", {})

    assert "error" in result
    assert seen == ["granola-stale", "also-refused"]
    assert len(http.token_posts()) == 1


def test_granola_calls_report_a_dead_sign_in_in_plain_words(tmp_path, monkeypatch):
    live, http, seen = live_granola(
        tmp_path, monkeypatch, [HttpError(400, GRANOLA_TOKEN_URL, {"error": "invalid_grant"})]
    )

    result = live.call("mcp__granola__list_meetings", {})

    assert result == {
        "error": "Granola no longer accepts the saved sign-in. Reconnect to restore access."
    }
    assert seen == ["granola-stale"]


def test_an_expired_but_working_granola_grant_checks_as_connected(tmp_path, monkeypatch):
    live, _, _ = live_granola(tmp_path, monkeypatch, [{"access_token": "granola-fresh"}])

    assert live.probe().outcome == "ok"


def test_a_dead_granola_grant_checks_as_reconnect_required(tmp_path, monkeypatch):
    live, _, _ = live_granola(
        tmp_path, monkeypatch, [HttpError(400, GRANOLA_TOKEN_URL, {"error": "invalid_grant"})]
    )

    assert live.probe().outcome == "rejected"


def test_drive_still_uses_a_saved_access_token_without_a_refresh_token(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"access_token": "working", "scopes": [DRIVE_SCOPE]})
    http = TokenHttp([], api={FILES_URL: lambda auth: {"files": []}})
    drive = DriveApi(secrets, http=http, client_id="cid", client_secret="sec")

    drive.search("brief")

    assert http.token_posts() == []
