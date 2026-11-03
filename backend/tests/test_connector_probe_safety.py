"""Probe evidence belongs to a successful call and the credential it used."""

from contextlib import asynccontextmanager
import threading

import httpx
import pytest
from mcp.types import CallToolResult, TextContent

from coworker.connector_probe import ConnectorProbes, ProbeResult, default_fingerprints, probe_google
from coworker.connectors.google_oauth import GoogleTokens, load_google, save_google
from coworker.connectors.token_renewal import RenewalUnavailable
from coworker.mcp import LiveMcp
from coworker.mcp_oauth import McpOAuth
from coworker.secrets import SecretStore


@pytest.mark.parametrize("structured", [None, {"meetings": []}])
def test_mcp_tool_error_is_a_failed_probe_without_returning_its_content(
    tmp_path, monkeypatch, caplog, structured
):
    secret = "Authorization: Bearer private-provider-token"
    result = CallToolResult(
        isError=True,
        content=[TextContent(type="text", text=secret)],
        structuredContent=structured,
    )

    class Session:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            pass

        async def call_tool(self, name, arguments):
            assert name == "list_meetings"
            return result

    @asynccontextmanager
    async def transport(*_args, **_kwargs):
        yield None, None, None

    monkeypatch.setattr("coworker.mcp._import_mcp_sdk", lambda: (Session, transport))
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("mcp-oauth:granola", {"access_token": "saved-token"})
    live = LiveMcp(secrets=secrets, config_path=tmp_path / "mcp.json")

    probe = live.probe()
    call = live.call("mcp__granola__list_meetings", {})

    assert probe.outcome == "unreachable"
    assert "error" in call
    assert secret not in str(probe) + str(call) + caplog.text


@pytest.mark.parametrize("key", ["google", "granola"])
@pytest.mark.parametrize("old_outcome", ["ok", "rejected"])
def test_in_flight_probe_does_not_certify_a_replacement_grant(tmp_path, key, old_outcome):
    secrets = SecretStore(tmp_path / "secrets.json")
    store_key = "google" if key == "google" else "mcp-oauth:granola"
    secrets.put(store_key, {"access_token": "old-access", "refresh_token": "old-refresh"})
    entered, release = threading.Event(), threading.Event()
    calls = []

    def probe():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            return ProbeResult(old_outcome, "Old grant's result")
        return ProbeResult("unreachable", "Replacement's result")

    probes = ConnectorProbes(
        google=probe, granola=probe, fingerprints=default_fingerprints(secrets)
    )
    worker = threading.Thread(target=probes.check)
    worker.start()
    try:
        assert entered.wait(5)
        secrets.put(store_key, {"access_token": "new-access", "refresh_token": "new-refresh"})
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert probes.result(key) is None
    probes.check()
    assert len(calls) == 2
    assert probes.result(key).detail == "Replacement's result"


@pytest.mark.parametrize("provider", ["google", "granola"])
@pytest.mark.parametrize("replacement", [{"access_token": "new-access", "refresh_token": "new-refresh"}, None])
def test_an_outstanding_renewal_cannot_replace_a_new_grant_or_undo_disconnect(
    tmp_path, provider, replacement
):
    secrets = SecretStore(tmp_path / "secrets.json")
    key = "google" if provider == "google" else "mcp-oauth:granola"
    original = {
        "access_token": "old-access", "refresh_token": "old-refresh",
        "client_id": "client-id", "token_url": "https://auth.example.test/token",
    }
    secrets.put(key, original)

    class Http:
        def post(self, *_args, **_kwargs):
            if replacement is None:
                secrets.delete(key)
            else:
                secrets.put(key, replacement)
            return {"access_token": "renewed-old-access", "refresh_token": "rotated-old-refresh"}

    if provider == "google":
        tokens = GoogleTokens(secrets, Http(), client_id="client-id", client_secret="client-secret")
    else:
        tokens = McpOAuth(secrets, "http://localhost", http=Http())

    with pytest.raises(RenewalUnavailable, match="sign-in changed"):
        tokens.renew()

    assert secrets.get(key) == replacement


@pytest.mark.parametrize("outcome", ["ok", "unreachable"])
def test_granola_caches_the_credential_actually_retried_after_renewal(
    tmp_path, monkeypatch, outcome
):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("mcp-oauth:granola", {
        "access_token": "old-access", "refresh_token": "old-refresh",
        "client_id": "client-id", "token_url": "https://auth.example.test/token",
    })
    renewals, seen = [], []

    class Http:
        def post(self, *_args, **_kwargs):
            renewals.append(1)
            return {"access_token": "fresh-access", "refresh_token": "rotated-refresh"}

    async def call(self, name, arguments, *, tokens=None):
        seen.append(tokens["access_token"])
        if tokens["access_token"] == "old-access":
            request = httpx.Request("POST", "https://mcp.granola.ai/mcp")
            raise httpx.HTTPStatusError("401", request=request,
                                        response=httpx.Response(401, request=request))
        return {"meetings": []} if outcome == "ok" else {"error": "Tool failed"}

    monkeypatch.setattr(LiveMcp, "_async_call", call)
    oauth = McpOAuth(secrets, "http://localhost", http=Http())
    live = LiveMcp(secrets=secrets, config_path=tmp_path / "mcp.json", oauth=oauth)
    probes = ConnectorProbes(google=lambda: None, granola=live.probe,
                             fingerprints=default_fingerprints(secrets))

    probes.check()
    assert probes.result("granola").outcome == outcome
    probes.check()
    assert seen == ["old-access", "fresh-access"]
    assert renewals == [1]


def test_google_caches_the_rotated_refresh_credential(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "client-secret")
    secrets = SecretStore(tmp_path / "secrets.json")
    save_google(secrets, {"access_token": "old-access", "refresh_token": "old-refresh"})
    calls = []

    class Http:
        def post(self, *_args, **_kwargs):
            calls.append(1)
            return {"access_token": "fresh-access", "refresh_token": "rotated-refresh"}

    probes = ConnectorProbes(google=lambda: probe_google(secrets, Http()), granola=lambda: None,
                             fingerprints=default_fingerprints(secrets))

    probes.check()
    assert probes.result("google").outcome == "ok"
    assert load_google(secrets)["refresh_token"] == "rotated-refresh"
    probes.check()
    assert calls == [1]


def test_a_late_old_probe_keeps_a_new_grants_already_cached_result(tmp_path):
    secrets = SecretStore(tmp_path / "secrets.json")
    secrets.put("mcp-oauth:granola", {"access_token": "old-access"})
    entered, release = threading.Event(), threading.Event()

    def probe():
        if secrets.get("mcp-oauth:granola")["access_token"] == "old-access":
            entered.set()
            assert release.wait(5)
            return ProbeResult("ok", "Old grant worked")
        return ProbeResult("rejected", "New grant was refused")

    probes = ConnectorProbes(google=lambda: None, granola=probe,
                             fingerprints=default_fingerprints(secrets))
    old_check = threading.Thread(target=probes.check)
    old_check.start()
    try:
        assert entered.wait(5)
        secrets.put("mcp-oauth:granola", {"access_token": "new-access"})
        probes.check()
        assert probes.result("granola").outcome == "rejected"
    finally:
        release.set()
        old_check.join(5)
    assert probes.result("granola").outcome == "rejected"
