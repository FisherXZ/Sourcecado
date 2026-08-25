"""Gmail draft client — injectable so tests never hit the network.

Slice 12: Gmail API users.drafts.create after OAuth. There is no send method on
the live client. FakeGmail stays for pytest. Secrets stay out of prompts.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any, Protocol

from coworker.apollo import HttpError, LiveHttp
from coworker.connectors.google_oauth import (
    google_client_credentials,
    load_google,
    refresh_access_token,
    save_google,
)
from coworker.secrets import SecretStore

DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


class GmailError(RuntimeError):
    pass


def _from_http_error(exc: BaseException) -> GmailError:
    """Turn httpx/Google HTTP failures into a GmailError with Google's message."""
    if isinstance(exc, HttpError):
        payload = exc.body
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "").strip()
                if message:
                    return GmailError(message)
            elif isinstance(err, str) and err.strip():
                return GmailError(err.strip())
        return GmailError(f"Gmail API returned HTTP {exc.status}.")
    response = getattr(exc, "response", None)
    if response is not None:
        payload = None
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "").strip()
                if message:
                    return GmailError(message)
            elif isinstance(err, str) and err.strip():
                return GmailError(err.strip())
        status = getattr(response, "status_code", None)
        if status:
            return GmailError(f"Gmail API returned HTTP {status}.")
    text = str(exc).strip()
    return GmailError(text or "Gmail request failed.")


class GmailClient(Protocol):
    drafts: list[dict[str, Any]]
    sends: list[dict[str, Any]]

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]: ...


class FakeGmail:
    def __init__(self) -> None:
        self.drafts: list[dict[str, Any]] = []
        self.sends: list[dict[str, Any]] = []

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        item = {
            "id": f"draft_{len(self.drafts) + 1}",
            "to": to,
            "subject": subject,
            "body": body,
            "sent": False,
        }
        self.drafts.append(item)
        return {
            "id": item["id"],
            "to": to,
            "subject": subject,
            "drafted": True,
            "sent": False,
        }

    def send(self, *args: Any, **kwargs: Any) -> None:
        self.sends.append({"args": args, "kwargs": kwargs})
        raise GmailError("gmail_draft never sends")

    def search(self, query: str, max_results: int = 10) -> dict[str, Any]:
        return {"messages": []}

    def read(self, *, message_id: str) -> dict[str, Any]:
        return {"id": message_id, "body": "", "sent": False}


class MissingGmail:
    drafts: list[dict[str, Any]] = []
    sends: list[dict[str, Any]] = []

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def search(self, query: str, max_results: int = 10) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def read(self, *, message_id: str) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )


def _raw_message(*, to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")


class GmailApi:
    """Live Gmail drafts. No send()."""

    drafts: list[dict[str, Any]]
    sends: list[dict[str, Any]]

    def __init__(
        self,
        secrets: SecretStore,
        *,
        http: Any,
        client_id: str,
        client_secret: str,
    ) -> None:
        self.secrets = secrets
        self.http = http
        self.client_id = client_id
        self.client_secret = client_secret
        self.drafts = []
        self.sends = []

    def _access_token(self) -> str:
        profile = load_google(self.secrets)
        refresh = str(profile.get("refresh_token") or "")
        if not refresh:
            raise GmailError("Gmail is not connected.")
        cached = str(profile.get("access_token") or "")
        if cached:
            return cached
        return self._refresh(profile, refresh)

    def _refresh(self, profile: dict[str, Any], refresh: str) -> str:
        try:
            tokens = refresh_access_token(
                self.http,
                client_id=self.client_id,
                client_secret=self.client_secret,
                refresh_token=refresh,
            )
        except GmailError:
            raise
        except Exception as exc:
            raise _from_http_error(exc) from exc
        access = str(tokens.get("access_token") or "")
        if not access:
            raise GmailError("Gmail token refresh failed.")
        profile = dict(profile)
        profile["access_token"] = access
        save_google(self.secrets, profile)
        return access

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        token = self._access_token()
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {token}"
        try:
            if method == "get":
                return self.http.get(url, headers=headers, **kwargs)
            return self.http.post(url, headers=headers, **kwargs)
        except HttpError as exc:
            if exc.status == 401:
                profile = load_google(self.secrets)
                profile["access_token"] = ""
                save_google(self.secrets, profile)
                token = self._refresh(profile, str(profile.get("refresh_token") or ""))
                headers["Authorization"] = f"Bearer {token}"
                if method == "get":
                    return self.http.get(url, headers=headers, **kwargs)
                return self.http.post(url, headers=headers, **kwargs)
            raise _from_http_error(exc) from exc
        except GmailError:
            raise
        except Exception as exc:
            raise _from_http_error(exc) from exc

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        try:
            data = self._request(
                "post",
                DRAFTS_URL,
                headers={"Content-Type": "application/json"},
                json={"message": {"raw": _raw_message(to=to, subject=subject, body=body)}},
            )
        except GmailError:
            raise
        except Exception as exc:
            raise _from_http_error(exc) from exc
        draft_id = str((data or {}).get("id") or "")
        if not draft_id:
            raise GmailError("Gmail did not return a draft id.")
        item = {
            "id": draft_id,
            "to": to,
            "subject": subject,
            "drafted": True,
            "sent": False,
        }
        self.drafts.append(item)
        return item

    def search(self, query: str, max_results: int = 10) -> dict[str, Any]:
        listing = self._request(
            "get",
            MESSAGES_URL,
            params={"q": query, "maxResults": max(1, min(int(max_results), 10))},
        ) or {}
        messages = []
        for row in listing.get("messages") or []:
            mid = str(row.get("id") or "")
            if not mid:
                continue
            meta = self._request(
                "get",
                f"{MESSAGES_URL}/{mid}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            ) or {}
            headers = _header_map(meta.get("payload") or {})
            messages.append(
                {
                    "id": mid,
                    "threadId": row.get("threadId"),
                    "from": headers.get("from"),
                    "subject": headers.get("subject"),
                    "date": headers.get("date"),
                }
            )
        return {"messages": messages}

    def read(self, *, message_id: str) -> dict[str, Any]:
        data = self._request(
            "get",
            f"{MESSAGES_URL}/{message_id}",
            params={"format": "full"},
        ) or {}
        payload = data.get("payload") or {}
        headers = _header_map(payload)
        body = _flatten_body(payload)[:8000]
        return {
            "id": str(data.get("id") or message_id),
            "from": headers.get("from"),
            "to": headers.get("to"),
            "subject": headers.get("subject"),
            "date": headers.get("date"),
            "snippet": data.get("snippet"),
            "body": body,
            "sent": False,
        }


MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


def _header_map(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for header in payload.get("headers") or []:
        name = str(header.get("name") or "").lower()
        if name:
            out[name] = str(header.get("value") or "")
    return out


def _flatten_body(payload: dict[str, Any]) -> str:
    mime = str(payload.get("mimeType") or "")
    data = ((payload.get("body") or {}).get("data")) or ""
    if data and mime.startswith("text/plain"):
        return _b64(data)
    parts = payload.get("parts") or []
    chunks = [_flatten_body(part) for part in parts if isinstance(part, dict)]
    text = "\n".join(chunk for chunk in chunks if chunk)
    if text:
        return text
    if data:
        return _b64(data)
    return ""


def _b64(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def gmail_from_secrets(secrets: SecretStore, *, http: Any | None = None) -> FakeGmail | GmailApi | MissingGmail:
    profile = load_google(secrets)
    if not profile.get("refresh_token"):
        return MissingGmail()
    client_id, client_secret = google_client_credentials()
    if not client_id or not client_secret:
        return MissingGmail()
    return GmailApi(
        secrets,
        http=http if http is not None else LiveHttp(),
        client_id=client_id,
        client_secret=client_secret,
    )
