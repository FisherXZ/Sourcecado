"""Gmail draft client — injectable so tests never hit the network.

Slice 12: Gmail API users.drafts.create after OAuth. FakeGmail stays for
pytest. Secrets stay out of prompts.

Send is the one external effect in this module that costs a real message to a
real person. It is guarded by a SendAuthority: the identity the director
actually reviewed and approved. The authority is re-checked against the live
draft immediately before the send call, so a draft edited after review sends
nothing. The authority carries a digest of the reviewed body, never the body
itself and never a token.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
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
    def send(self, *, draft_id: str) -> dict[str, Any]: ...
    def get_draft(self, *, draft_id: str) -> dict[str, Any]: ...


class FakeGmail:
    """Test double that mirrors the parts of Gmail this flow depends on.

    ``send_attempts`` records every call into ``send``, including the ones that
    raise. ``sends`` records only the messages that actually left. Tests count
    both: at-most-once is proved by the real call count, not by a status
    string.
    """

    def __init__(self) -> None:
        self.drafts: list[dict[str, Any]] = []
        self.sends: list[dict[str, Any]] = []
        self.send_attempts: list[str] = []
        self.account_email: str | None = None

    def account(self) -> str | None:
        return self.account_email

    def _find(self, draft_id: str) -> dict[str, Any] | None:
        for item in self.drafts:
            if item["id"] == draft_id:
                return item
        return None

    def get_draft(self, *, draft_id: str) -> dict[str, Any]:
        item = self._find(draft_id)
        if item is None or item["sent"]:
            raise GmailError(f"Draft {draft_id} was not found.")
        return {
            "id": item["id"],
            "to": item["to"],
            "subject": item["subject"],
            "body": item["body"],
            "sent": False,
        }

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

    def send(self, *, draft_id: str) -> dict[str, Any]:
        self.send_attempts.append(draft_id)
        item = self._find(draft_id)
        if item is None or item["sent"]:
            # Gmail removes a draft when drafts.send succeeds, so a second send
            # of the same draft id is a 404 there too. Model that: a duplicate
            # send is loud, never a silent second message.
            raise GmailError(f"Draft {draft_id} was not found.")
        item["sent"] = True
        message_id = f"msg_{len(self.sends) + 1}"
        thread_id = f"thread_{len(self.sends) + 1}"
        item["message_id"] = message_id
        item["thread_id"] = thread_id
        self.sends.append({"draft_id": draft_id})
        return {
            "id": message_id,
            "threadId": thread_id,
            "draft_id": draft_id,
            "sent": True,
        }

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

    def send(self, *, draft_id: str) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def get_draft(self, *, draft_id: str) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def account(self) -> str | None:
        return None


def _raw_message(*, to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")


class GmailApi:
    """Live Gmail drafts and send-by-draft-id."""

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

    def send(self, *, draft_id: str) -> dict[str, Any]:
        data = self._request(
            "post",
            f"{DRAFTS_URL}/send",
            headers={"Content-Type": "application/json"},
            json={"id": draft_id},
        ) or {}
        message_id = str(data.get("id") or "")
        if not message_id:
            raise GmailError("Gmail did not return a sent message id.")
        item = {
            "id": message_id,
            "threadId": str(data.get("threadId") or "") or None,
            "draft_id": draft_id,
            "sent": True,
        }
        self.sends.append(item)
        return item

    def get_draft(self, *, draft_id: str) -> dict[str, Any]:
        """Always read the live draft.

        A cached copy of what Sourcecado created would hide an edit made in
        Gmail after the draft was written, and hiding that edit is exactly the
        stale-draft hazard the send gate exists to catch.
        """
        try:
            data = self._request(
                "get",
                f"{DRAFTS_URL}/{draft_id}",
                params={"format": "full"},
            ) or {}
        except GmailError:
            raise
        except Exception as exc:
            raise _from_http_error(exc) from exc
        message = (data.get("message") or {})
        payload = message.get("payload") or {}
        headers = _header_map(payload)
        return {
            "id": draft_id,
            "to": headers.get("to", ""),
            "subject": headers.get("subject", ""),
            "body": _flatten_body(payload),
            "sent": False,
        }

    def account(self) -> str | None:
        return load_google(self.secrets).get("email")

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


class SendAuthorityError(GmailError):
    """The reviewed binding no longer matches the live draft. Nothing was sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_body(body: str) -> str:
    """Compare bodies as the operator read them, not as MIME carried them.

    Gmail round-trips a plain-text body through quoted-printable and CRLF line
    endings, so the bytes that come back are not the bytes that went out. Line
    endings and trailing whitespace are transport, not content: normalizing
    them keeps the digest stable across the round trip while still changing the
    moment a human edits a word.
    """
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def body_digest(body: str) -> str:
    """The reviewed body version, as an identifier that is safe to persist."""
    return hashlib.sha256(normalize_body(body).encode("utf-8")).hexdigest()


def _same_address(left: str | None, right: str | None) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


@dataclass(frozen=True)
class SendAuthority:
    """Exactly what the director approved: one draft, one recipient, one body.

    Identity only. No access token, no refresh token, no message body ever
    lands in an approval record.
    """

    approval_id: str
    person_id: str
    draft_id: str
    account: str | None
    to: str
    subject: str
    body_digest: str

    def as_resource(self) -> dict[str, Any]:
        """The approval-card payload. Safe to persist and to show."""
        return {
            "kind": "gmail_send_authority",
            "person_id": self.person_id,
            "draft_id": self.draft_id,
            "account": self.account,
            "to": self.to,
            "subject": self.subject,
            "body_digest": self.body_digest,
            "sent": False,
        }

    @classmethod
    def from_resource(
        cls, resource: Any, *, approval_id: str
    ) -> "SendAuthority | None":
        if not isinstance(resource, dict):
            return None
        if resource.get("kind") != "gmail_send_authority":
            return None
        required = ("person_id", "draft_id", "to", "subject", "body_digest")
        if not all(str(resource.get(field) or "") for field in required):
            return None
        account = resource.get("account")
        return cls(
            approval_id=approval_id,
            person_id=str(resource["person_id"]),
            draft_id=str(resource["draft_id"]),
            account=str(account) if account else None,
            to=str(resource["to"]),
            subject=str(resource["subject"]),
            body_digest=str(resource["body_digest"]),
        )


def draft_snapshot(client: Any, *, draft_id: str) -> dict[str, Any]:
    """Read the live draft plus the account that would send it.

    Gmail deletes a draft when drafts.send succeeds, so a draft that reads back
    is a draft that has not been sent. That is why an already-sent draft
    surfaces here as ``draft_unavailable`` rather than as a separate check.
    """
    try:
        draft = client.get_draft(draft_id=draft_id)
    except GmailError as exc:
        raise SendAuthorityError("draft_unavailable", str(exc)) from exc
    except Exception as exc:
        raise SendAuthorityError("draft_unavailable", str(exc)) from exc
    body = str(draft.get("body") or "")
    account = None
    reader = getattr(client, "account", None)
    if callable(reader):
        try:
            account = reader() or None
        except Exception:
            account = None
    return {
        "draft_id": str(draft.get("id") or draft_id),
        "to": str(draft.get("to") or ""),
        "subject": str(draft.get("subject") or ""),
        "body": body,
        "body_digest": body_digest(body),
        "account": account,
    }


def authority_for_draft(
    client: Any,
    *,
    approval_id: str,
    person_id: str,
    draft_id: str,
    reviewed_body_digest: str,
) -> SendAuthority:
    """Bind an approval to the draft as it stands right now.

    ``reviewed_body_digest`` is the operator's attestation of the version they
    read. A draft edited between the review and this call is refused here, so
    an approval is never created for a body nobody reviewed.
    """
    snapshot = draft_snapshot(client, draft_id=draft_id)
    if snapshot["body_digest"] != reviewed_body_digest:
        raise SendAuthorityError(
            "stale_draft",
            "This draft changed after it was reviewed. Read it again before sending.",
        )
    return SendAuthority(
        approval_id=approval_id,
        person_id=person_id,
        draft_id=draft_id,
        account=snapshot["account"],
        to=snapshot["to"],
        subject=snapshot["subject"],
        body_digest=snapshot["body_digest"],
    )


def verify_send_authority(client: Any, authority: SendAuthority) -> dict[str, Any]:
    """Re-check every bound field against the live draft. Raises, or returns it."""
    snapshot = draft_snapshot(client, draft_id=authority.draft_id)
    if authority.account is not None and not _same_address(
        snapshot["account"], authority.account
    ):
        raise SendAuthorityError(
            "account_mismatch",
            "The connected Gmail account changed after this send was approved.",
        )
    if not _same_address(snapshot["to"], authority.to):
        raise SendAuthorityError(
            "recipient_mismatch",
            "The draft recipient changed after this send was approved.",
        )
    if snapshot["subject"].strip() != authority.subject.strip():
        raise SendAuthorityError(
            "subject_mismatch",
            "The draft subject changed after this send was approved.",
        )
    if snapshot["body_digest"] != authority.body_digest:
        raise SendAuthorityError(
            "stale_draft",
            "The draft body changed after this send was approved. Nothing was sent.",
        )
    return snapshot


def send_reviewed_draft(client: Any, authority: SendAuthority) -> dict[str, Any]:
    """The only sanctioned path to a real send.

    Verifies the binding, then makes exactly one send call. This function does
    not decide whether a send is allowed to happen at all — that is the
    approval claim in ConversationStore.decide_and_claim_inbox_execution, which
    hands the work to exactly one caller.
    """
    verify_send_authority(client, authority)
    result = client.send(draft_id=authority.draft_id) or {}
    message_id = str(result.get("id") or "")
    if not message_id:
        raise GmailError("Gmail did not return a sent message id.")
    return {
        "sent": True,
        "message_id": message_id,
        "thread_id": str(result.get("threadId") or "") or None,
        "draft_id": authority.draft_id,
        "account": authority.account,
        "to": authority.to,
        "subject": authority.subject,
        "body_digest": authority.body_digest,
        "person_id": authority.person_id,
        "approval_id": authority.approval_id,
    }


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
