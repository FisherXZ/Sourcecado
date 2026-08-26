"""Google OAuth — one identity, incremental scopes.

Tokens never go in the prompt. Send is in the Gmail grant. gmail_send asks
before users.drafts.send.
"""

from __future__ import annotations

from urllib.parse import urlencode
from typing import Any

from coworker.secrets import SecretStore

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_KEY = "google"
GMAIL_KEY = "gmail"
COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
BASE_SCOPES = (COMPOSE_SCOPE, READ_SCOPE, SEND_SCOPE, DRIVE_SCOPE, CALENDAR_SCOPE, EMAIL_SCOPE)
SCOPES = " ".join(BASE_SCOPES)


def authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    extra_scopes: tuple[str, ...] = (),
) -> str:
    scopes = " ".join(dict.fromkeys([*BASE_SCOPES, *extra_scopes]))
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def exchange_code(
    http: Any,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    return http.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )


def refresh_access_token(
    http: Any,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    return http.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )


def load_google(secrets: SecretStore) -> dict:
    return secrets.get(GOOGLE_KEY) or secrets.get(GMAIL_KEY) or {}


def save_google(secrets: SecretStore, profile: dict) -> None:
    secrets.put(GOOGLE_KEY, profile)
    secrets.put(GMAIL_KEY, profile)


def has_scope(profile: dict, scope: str) -> bool:
    return scope in (profile.get("scopes") or [])


def has_calendar_access(profile: dict) -> bool:
    return has_scope(profile, CALENDAR_SCOPE) or has_scope(profile, CALENDAR_EVENTS_SCOPE)


def merge_scopes(existing: list[str] | None, granted: str | None) -> list[str]:
    out: list[str] = []
    for item in [*(existing or []), *(granted or "").split()]:
        if item and item not in out:
            out.append(item)
    return out


def google_client_credentials() -> tuple[str, str]:
    import os

    client_id = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or ""
    ).strip()
    client_secret = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET") or ""
    ).strip()
    return client_id, client_secret
