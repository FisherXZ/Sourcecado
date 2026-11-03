import logging
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from coworker.apollo import HttpError
from coworker.connector_probe import (
    ConnectorProbes,
    ProbeResult,
    default_fingerprints,
    probe_google,
)
from coworker.connectors.google_oauth import COMPOSE_SCOPE, READ_SCOPE
from coworker.mcp import LiveMcp
from coworker.server import TOKEN_HEADER, create_app

TOKEN = "test-token-probes"
GOOGLE_REFRESH = "rt-dead-secret"
GRANOLA_ACCESS = "granola-at-secret"
OK = ProbeResult("ok", "This connection is ready to use.")
DEAD = ProbeResult("rejected", "Granola no longer accepts the saved sign-in. Reconnect to restore access.")
DOWN = ProbeResult("unreachable", "Sourcecado could not reach Granola.")


class CountingProbe:
    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> ProbeResult:
        self.calls += 1
        return self.result


def make_app(tmp_path, *, google=True, granola=True, apollo_key="sk-apollo-secret"):
    app = create_app(token=TOKEN, state=tmp_path, apollo_key=apollo_key)
    if google:
        app.state.secrets.put(
            "google",
            {
                "refresh_token": GOOGLE_REFRESH,
                "email": "fisher@example.com",
                "scopes": [READ_SCOPE, COMPOSE_SCOPE],
            },
        )
    if granola:
        app.state.secrets.put("mcp-oauth:granola", {"access_token": GRANOLA_ACCESS})
    return app


def install(app, *, google: CountingProbe, granola: CountingProbe, **kwargs) -> None:
    app.state.connector_probes = ConnectorProbes(
        google=google,
        granola=granola,
        fingerprints=default_fingerprints(app.state.secrets),
        **kwargs,
    )


def check(app, **params):
    response = TestClient(app).post(
        "/v1/connectors/check", headers={TOKEN_HEADER: TOKEN}, params=params
    )
    assert response.status_code == 200
    return {item["id"]: item for item in response.json()["connectors"]}


def catalog(app):
    body = TestClient(app).get("/v1/connectors", headers={TOKEN_HEADER: TOKEN}).json()
    return {item["id"]: item for item in body["connectors"]}


def test_no_credentials_are_never_probed(tmp_path):
    app = make_app(tmp_path, google=False, granola=False, apollo_key=None)
    google, granola = CountingProbe(OK), CountingProbe(OK)
    install(app, google=google, granola=granola)

    by_id = check(app)

    assert google.calls == 0 and granola.calls == 0
    assert {item["status"] for item in by_id.values()} == {"available"}
    assert not any(item["verified"] for item in by_id.values())


def test_saved_but_dead_granola_token_reads_reconnect_required(tmp_path):
    app = make_app(tmp_path)
    install(app, google=CountingProbe(OK), granola=CountingProbe(DEAD))

    granola = check(app)["granola"]

    assert granola["status"] == "reconnect_required"
    assert granola["verified"] is False
    assert granola["catalog_group"] == "connected"
    assert granola["health"] == {
        "category": "error",
        "label": "Reconnect required",
        "message": DEAD.detail,
    }
    assert granola["recovery"]["action_label"] == "Reconnect"
    assert granola["available_actions"] == ["reconnect", "disconnect"]


def test_unreachable_granola_reads_connection_failed(tmp_path):
    app = make_app(tmp_path)
    install(app, google=CountingProbe(OK), granola=CountingProbe(DOWN))

    granola = check(app)["granola"]

    assert granola["status"] == "failed"
    assert granola["health"]["label"] == "Connection failed"
    assert granola["health"]["message"] == "Sourcecado could not reach Granola."
    assert granola["verified"] is False


def test_working_grants_stay_connected_and_verified(tmp_path):
    app = make_app(tmp_path)
    install(app, google=CountingProbe(OK), granola=CountingProbe(OK))

    by_id = check(app)

    for connector_id in ("gmail", "granola"):
        assert by_id[connector_id]["status"] == "connected"
        assert by_id[connector_id]["verified"] is True
    # Drive and Calendar scopes were never granted; a working token does not hide that.
    assert by_id["drive"]["status"] == "missing_scopes"
    assert by_id["calendar"]["status"] == "missing_scopes"


def test_dead_google_token_marks_every_google_connector(tmp_path):
    app = make_app(tmp_path)
    install(
        app,
        google=CountingProbe(ProbeResult("rejected", "Google no longer accepts the saved sign-in.")),
        granola=CountingProbe(OK),
    )

    by_id = check(app)

    for connector_id in ("gmail", "drive", "calendar"):
        assert by_id[connector_id]["status"] == "reconnect_required"
        assert by_id[connector_id]["verified"] is False


def test_apollo_is_never_probed_and_stays_configured(tmp_path):
    app = make_app(tmp_path)
    install(app, google=CountingProbe(OK), granola=CountingProbe(OK))

    apollo = check(app)["apollo"]

    assert apollo["status"] == "connected"
    assert apollo["verified"] is False
    assert apollo["health"]["label"] == "Configured"


def test_catalog_reads_the_last_check_for_the_current_credential(tmp_path):
    app = make_app(tmp_path)
    install(app, google=CountingProbe(OK), granola=CountingProbe(DEAD))

    assert catalog(app)["granola"]["status"] == "connected"  # nothing checked yet
    check(app)
    assert catalog(app)["granola"]["status"] == "reconnect_required"

    # A new grant is a different credential, so the old failure no longer applies.
    app.state.secrets.put("mcp-oauth:granola", {"access_token": "granola-new-secret"})
    assert catalog(app)["granola"]["status"] == "connected"

    TestClient(app).post("/v1/connectors/granola/disconnect", headers={TOKEN_HEADER: TOKEN})
    assert catalog(app)["granola"]["status"] == "available"


def test_checks_are_cached_until_they_expire_or_the_credential_changes(tmp_path):
    app = make_app(tmp_path)
    clock = [100.0]
    google, granola = CountingProbe(OK), CountingProbe(OK)
    install(app, google=google, granola=granola, clock=lambda: clock[0], cache_seconds=60)

    check(app)
    check(app)
    assert (google.calls, granola.calls) == (1, 1)

    clock[0] += 61
    check(app)
    assert (google.calls, granola.calls) == (2, 2)

    app.state.secrets.put("mcp-oauth:granola", {"access_token": "granola-new-secret"})
    check(app)
    assert (google.calls, granola.calls) == (2, 3)


def test_a_probe_that_renews_its_token_keeps_its_result(tmp_path):
    app = make_app(tmp_path)
    calls = []

    def renew_then_fail() -> ProbeResult:
        calls.append(1)
        app.state.secrets.put("mcp-oauth:granola", {"access_token": "granola-renewed-secret"})
        return DOWN

    install(app, google=CountingProbe(OK), granola=renew_then_fail)

    check(app)
    assert catalog(app)["granola"]["status"] == "failed"
    check(app)
    assert len(calls) == 1  # still fresh for the renewed credential


def test_a_hung_probe_times_out_without_holding_the_request(tmp_path):
    app = make_app(tmp_path)
    release = threading.Event()

    def hang() -> ProbeResult:
        release.wait(5)
        return OK

    install(app, google=CountingProbe(OK), granola=hang, timeout=0.3)
    started = time.monotonic()
    granola = check(app)["granola"]
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 2
    assert granola["status"] == "failed"
    assert granola["health"]["message"] == "The connection check timed out."


def test_probe_exceptions_never_reach_the_response_or_logs(tmp_path, caplog):
    app = make_app(tmp_path)

    def explode() -> ProbeResult:
        raise RuntimeError(f"Authorization: Bearer {GRANOLA_ACCESS} rejected")

    install(app, google=CountingProbe(OK), granola=explode)
    with caplog.at_level(logging.DEBUG):
        response = TestClient(app).post("/v1/connectors/check", headers={TOKEN_HEADER: TOKEN})

    assert GRANOLA_ACCESS not in response.text
    assert "Bearer" not in response.text
    assert GRANOLA_ACCESS not in caplog.text
    granola = {item["id"]: item for item in response.json()["connectors"]}["granola"]
    assert granola["health"]["message"] == "The connection check failed."


class FakeTokenHttp:
    def __init__(self, outcome):
        self.outcome = outcome
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append(url)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def google_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret-value")


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"access_token": "fresh"}, "ok"),
        ({}, "unreachable"),  # a malformed reply is not a dead sign-in
        (HttpError(400, "https://oauth2.googleapis.com/token", {"error": "invalid_grant"}), "rejected"),
        (HttpError(401, "https://oauth2.googleapis.com/token", {"error": "invalid_client"}), "unreachable"),
        (HttpError(503, "https://oauth2.googleapis.com/token", "busy"), "unreachable"),
        (httpx.ConnectError(f"refresh_token={GOOGLE_REFRESH} failed"), "unreachable"),
    ],
)
def test_google_probe_classifies_the_token_endpoint(tmp_path, google_client, outcome, expected):
    app = make_app(tmp_path, granola=False)
    http = FakeTokenHttp(outcome)

    result = probe_google(app.state.secrets, http)

    assert result.outcome == expected
    assert http.posts == ["https://oauth2.googleapis.com/token"]
    assert GOOGLE_REFRESH not in result.detail
    assert "client-secret-value" not in result.detail


def test_google_probe_without_client_settings_makes_no_request(tmp_path, monkeypatch):
    for name in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    app = make_app(tmp_path, granola=False)
    http = FakeTokenHttp({"access_token": "fresh"})

    result = probe_google(app.state.secrets, http)

    assert result.outcome == "unreachable"
    assert http.posts == []


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.granola.ai/mcp")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status} for Bearer {GRANOLA_ACCESS}", request=request, response=response)


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [
        ("ok", "ok"),
        ("sdk_missing", "unreachable"),
        ("unauthorized_in_group", "rejected"),
        ("forbidden_as_cause", "rejected"),
        ("server_error", "unreachable"),
        ("connect_error", "unreachable"),
    ],
)
def test_granola_probe_classifies_mcp_failures(tmp_path, monkeypatch, behaviour, expected):
    app = make_app(tmp_path, google=False)
    live = LiveMcp(secrets=app.state.secrets, config_path=tmp_path / "mcp.json")

    async def fake_call(self, name, arguments):
        assert name == "mcp__granola__list_meetings"
        if behaviour == "ok":
            return {"ok": True, "meetings": []}
        if behaviour == "sdk_missing":
            return {"error": "mcp SDK is not installed"}
        if behaviour == "unauthorized_in_group":
            raise ExceptionGroup("task group", [_status_error(401)])
        if behaviour == "forbidden_as_cause":
            try:
                raise _status_error(403)
            except httpx.HTTPStatusError as exc:
                raise RuntimeError("session failed") from exc
        if behaviour == "server_error":
            raise ExceptionGroup("task group", [_status_error(502)])
        raise httpx.ConnectError(f"Bearer {GRANOLA_ACCESS} unreachable")

    monkeypatch.setattr(LiveMcp, "_async_call", fake_call)

    result = live.probe()

    assert result.outcome == expected
    assert GRANOLA_ACCESS not in result.detail


def test_granola_probe_without_a_token_does_not_call_out(tmp_path, monkeypatch):
    app = make_app(tmp_path, google=False, granola=False)
    live = LiveMcp(secrets=app.state.secrets, config_path=tmp_path / "mcp.json")

    async def fail(self, name, arguments):
        raise AssertionError("probe must not call Granola without a token")

    monkeypatch.setattr(LiveMcp, "_async_call", fail)

    assert live.probe().outcome == "unreachable"


def test_granola_probe_times_out(tmp_path, monkeypatch):
    app = make_app(tmp_path, google=False)
    live = LiveMcp(secrets=app.state.secrets, config_path=tmp_path / "mcp.json")

    async def slow(self, name, arguments):
        import asyncio

        await asyncio.sleep(2)
        return {"ok": True}

    monkeypatch.setattr(LiveMcp, "_async_call", slow)

    result = live.probe(timeout=0.2)

    assert result == ProbeResult("unreachable", "Granola did not respond in time.")
