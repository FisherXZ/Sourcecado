"""One-slot MCP OAuth for Granola. Interactive connect only.

Discovers Granola's authorization server, registers a public client (DCR),
then opens that server's authorize URL (not the MCP CloudFront origin).
Finish exchanges the authorization code for tokens. A missing code never
stores a placeholder token.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as pysecrets
import threading
from typing import Any, Callable
from urllib.parse import urlencode

from coworker.connectors.token_renewal import (
    CredentialRejected,
    RenewalUnavailable,
    access_from,
    classify_token_error,
)
from coworker.secrets import SecretStore

RESOURCE = "https://mcp.granola.ai/mcp"
PRM_URL = "https://mcp.granola.ai/.well-known/oauth-protected-resource"
_RENEW_LOCK = threading.Lock()


def _open_browser(url: str) -> bool:
    try:
        import subprocess

        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _pkce(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class McpOAuth:
    def __init__(
        self,
        secrets: SecretStore,
        public_url: str,
        http: Any | None = None,
        browser_opener: Callable[[str], bool] | None = None,
    ) -> None:
        self.secrets = secrets
        self.public_url = public_url.rstrip("/")
        self.http = http
        self.browser_opener = browser_opener or _open_browser
        self._pending: dict[str, str] | None = None

    def _client(self) -> Any:
        from coworker.apollo import LiveHttp

        return self.http if self.http is not None else LiveHttp()

    def _discover(self, http: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        prm = http.get(PRM_URL)
        if not isinstance(prm, dict):
            raise RuntimeError("granola metadata failed")
        issuers = prm.get("authorization_servers") or []
        issuer = str(issuers[0] if issuers else "").rstrip("/")
        if not issuer:
            raise RuntimeError("granola authorization server missing")
        meta = http.get(f"{issuer}/.well-known/oauth-authorization-server")
        if not isinstance(meta, dict):
            raise RuntimeError("granola authorization metadata failed")
        return prm, meta

    def start(self, server: str) -> dict[str, Any]:
        http = self._client()
        prm, meta = self._discover(http)
        auth_endpoint = str(meta.get("authorization_endpoint") or "")
        token_endpoint = str(meta.get("token_endpoint") or "")
        register_endpoint = str(meta.get("registration_endpoint") or "")
        if not auth_endpoint or not token_endpoint or not register_endpoint:
            raise RuntimeError("granola oauth endpoints missing")
        redirect = f"{self.public_url}/v1/mcp/oauth/callback"
        client = http.post(
            register_endpoint,
            json={
                "client_name": "Club",
                "redirect_uris": [redirect],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "application_type": "native",
            },
        )
        if not isinstance(client, dict) or not client.get("client_id"):
            raise RuntimeError("granola client registration failed")
        client_id = str(client["client_id"])
        scopes = prm.get("scopes_supported") or ["mcp"]
        scope = " ".join(str(s) for s in scopes) if isinstance(scopes, list) else "mcp"
        state = pysecrets.token_urlsafe(16)
        verifier = pysecrets.token_urlsafe(32)
        self._pending = {
            "server": server,
            "state": state,
            "verifier": verifier,
            "client_id": client_id,
            "token_url": token_endpoint,
        }
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect,
                "state": state,
                "code_challenge": _pkce(verifier),
                "code_challenge_method": "S256",
                "resource": RESOURCE,
                "scope": scope,
            }
        )
        url = f"{auth_endpoint}?{query}"
        opened = bool(self.browser_opener(url))
        return {"url": url, "started": True, "opened": opened, "redirect_uri": redirect}

    def finish(self, *, code: str, state: str) -> None:
        if not str(code or "").strip():
            raise RuntimeError("missing code")
        if self._pending is None:
            raise RuntimeError("no waiter")
        pending = self._pending
        if state != pending["state"]:
            raise RuntimeError("state mismatch")
        http = self._client()
        redirect = f"{self.public_url}/v1/mcp/oauth/callback"
        tokens = http.post(
            pending["token_url"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect,
                "code_verifier": pending["verifier"],
                "client_id": pending["client_id"],
                "resource": RESOURCE,
            },
        )
        if not isinstance(tokens, dict):
            raise RuntimeError("token exchange failed")
        access = str(tokens.get("access_token") or "")
        if not access:
            raise RuntimeError("token exchange failed")
        refresh = str(tokens.get("refresh_token") or "")
        server = pending["server"]
        client_id = pending["client_id"]
        self._pending = None
        self.secrets.put(
            f"mcp-oauth:{server}",
            {
                "access_token": access,
                "refresh_token": refresh,
                "server": server,
                "client_id": client_id,
                "token_url": pending["token_url"],
            },
        )

    def renew(self, server: str = "granola", *, rejected: str | None = None) -> str:
        """Exchange the saved refresh token for a new access token.

        Serialized, and a caller whose token was refused passes it as
        ``rejected`` so a token another caller already replaced is reused
        rather than spending the refresh token twice.
        """
        key = f"mcp-oauth:{server}"
        with _RENEW_LOCK:
            saved = dict(self.secrets.get(key) or {})
            current = str(saved.get("access_token") or "")
            if rejected is not None and current and current != rejected:
                return current
            refresh = str(saved.get("refresh_token") or "")
            client_id = str(saved.get("client_id") or "")
            if not refresh or not client_id:
                raise CredentialRejected(
                    "Granola no longer accepts the saved sign-in. Reconnect to restore access."
                )
            http = self._client()
            token_url = str(saved.get("token_url") or "")
            if not token_url:
                # Grants saved before the token URL was kept: look it up again.
                try:
                    _, meta = self._discover(http)
                except Exception:
                    raise RenewalUnavailable(
                        "Sourcecado could not reach Granola.", kind="unreachable"
                    ) from None
                token_url = str(meta.get("token_endpoint") or "")
                if not token_url:
                    raise RenewalUnavailable(
                        "Granola did not publish a token address.", kind="unreachable"
                    )
            try:
                tokens = http.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                        "client_id": client_id,
                        "resource": RESOURCE,
                    },
                )
            except Exception as exc:
                outcome = classify_token_error(exc, provider="Granola")
                # A registered public client that is refused is only fixed by
                # signing in again, which registers a new one.
                if isinstance(outcome, RenewalUnavailable) and outcome.kind == "setup":
                    outcome = CredentialRejected(
                        "Granola no longer accepts the saved sign-in. Reconnect to restore access."
                    )
                raise outcome from None
            access, rotated = access_from(tokens, provider="Granola")
            saved["access_token"] = access
            if rotated:
                saved["refresh_token"] = rotated
            saved["token_url"] = token_url
            self.secrets.put(key, saved)
            return access
