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
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any, Protocol

from coworker.apollo import HttpError, LiveHttp
from coworker.evidence_envelope import (
    EvidenceParts,
    combine,
    external,
    opaque,
)
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


class GmailOutcomeUnknown(GmailError):
    """The request left this machine and no answer came back.

    Distinct from a generic failure because the caller's answer is different:
    a failure means nothing happened and the caller may say so, this means
    nobody knows. `agent_run_dispatch.guarded_call` turns it into a quarantined
    effect a person settles, so it must not be caught as an ordinary error on
    the way up.
    """


class GmailHistoryExpired(GmailError):
    """The stored cursor is older than the history Gmail still keeps.

    Distinct from a generic failure because the caller's answer is different:
    a failure means try again later, this means the incremental boundary is
    gone and the tracked threads have to be read directly.
    """


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a GmailError, if there was one.

    ``_request`` raises ``_from_http_error(exc) from exc``, so the original
    error is still on the chain even after the message has been rewritten.
    """
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, HttpError):
            return int(candidate.status)
        response = getattr(candidate, "response", None)
        status = getattr(response, "status_code", None)
        if status is not None:
            return int(status)
    return None


def _definite(exc: GmailError) -> GmailError:
    """The same failure, stated as one nobody has to settle by hand."""
    if not isinstance(exc, GmailOutcomeUnknown):
        return exc
    return GmailError(f"Gmail token refresh failed. {exc}")


def _never_left(exc: BaseException) -> bool:
    """Whether the request provably never reached Gmail.

    A connection that was never opened, a proxy that refused, a name that did
    not resolve: the bytes went nowhere, so this is an ordinary failure and
    saying so costs nothing. Anything after the socket is open -- a read that
    expires, a write that dies mid-body, a server that hangs up -- may have
    been acted on, and only that half is ambiguous.

    Imported here rather than at module scope to match `apollo.LiveHttp`, which
    keeps `httpx` out of import time.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return False
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ProxyError,
            httpx.PoolTimeout,
            httpx.UnsupportedProtocol,
            httpx.InvalidURL,
        ),
    )


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
    # No status anywhere on the chain, so Gmail never answered. Whether the
    # request was acted on depends on how far it got, and `_never_left` is the
    # only part of that this process can actually decide.
    text = str(exc).strip()
    if _never_left(exc):
        return GmailError(text or "Gmail could not be reached, so nothing was sent.")
    return GmailOutcomeUnknown(
        text or "Gmail did not answer, so the outcome is unknown."
    )


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
        # The mailbox as Gmail stores it, plus the counters a reply-filing test
        # reads to prove the refresh really reached the connector.
        self.inbox: list[dict[str, Any]] = []
        self.history_id = 1000
        self.history_page_size = 2
        self.history_floor: int | None = None
        self.history_calls: list[tuple[str, str | None]] = []
        self.reads: list[str] = []
        self.thread_reads: list[str] = []

    def account(self) -> str | None:
        return self.account_email

    def deliver(
        self,
        *,
        thread_id: str,
        message_id: str,
        sender: str,
        to: str,
        subject: str,
        snippet: str = "",
        cc: str | None = None,
        label_ids: tuple[str, ...] = ("INBOX",),
    ) -> dict[str, Any]:
        """Put one message in the mailbox and advance the history id."""
        self.history_id += 1
        headers = [
            {"name": "From", "value": sender},
            {"name": "To", "value": to},
            {"name": "Subject", "value": subject},
        ]
        if cc:
            headers.append({"name": "Cc", "value": cc})
        record = {
            "id": message_id,
            "threadId": thread_id,
            "labelIds": list(label_ids),
            "snippet": snippet,
            "internalDate": str(1_756_000_000_000 + self.history_id),
            "historyId": str(self.history_id),
            "payload": {"headers": headers},
        }
        self.inbox.append(record)
        return record

    def profile_history_id(self) -> str:
        return str(self.history_id)

    def history(
        self, *, start_history_id: str, page_token: str | None = None
    ) -> dict[str, Any]:
        self.history_calls.append((str(start_history_id), page_token))
        start = int(start_history_id)
        if self.history_floor is not None and start < self.history_floor:
            raise GmailHistoryExpired(
                "The stored Gmail cursor is older than the history Gmail keeps."
            )
        fresh = [
            item
            for item in self.inbox
            if int(item["historyId"]) > start and "INBOX" in item["labelIds"]
        ]
        offset = int(page_token or 0)
        page = fresh[offset : offset + self.history_page_size]
        following = offset + self.history_page_size
        return {
            "message_ids": [item["id"] for item in page],
            "history_id": str(self.history_id),
            "next_page_token": str(following) if following < len(fresh) else None,
        }

    def inbound_message(self, *, message_id: str) -> dict[str, Any]:
        self.reads.append(message_id)
        for item in self.inbox:
            if item["id"] == message_id:
                return inbound_message_view(item)
        raise GmailError(f"Message {message_id} was not found.")

    def thread(self, *, thread_id: str) -> dict[str, Any]:
        self.thread_reads.append(thread_id)
        return {
            "id": thread_id,
            "messages": [
                inbound_message_view(item)
                for item in self.inbox
                if item["threadId"] == thread_id
            ],
        }

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
        # Gmail keeps what we sent on the same thread. A thread re-read has to
        # meet our own message, so the reply reader has to skip it.
        self.deliver(
            thread_id=thread_id,
            message_id=message_id,
            sender=self.account_email or "me@example.test",
            to=item["to"],
            subject=item["subject"],
            label_ids=("SENT",),
        )
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

    def profile_history_id(self) -> str:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def history(
        self, *, start_history_id: str, page_token: str | None = None
    ) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def inbound_message(self, *, message_id: str) -> dict[str, Any]:
        raise GmailError(
            "Gmail is not connected. Click Connect Gmail in the window first."
        )

    def thread(self, *, thread_id: str) -> dict[str, Any]:
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
        except GmailError as exc:
            # A refresh sends no mail, so it never produces an unknown outcome.
            # Whatever went wrong on the token endpoint, the caller has no
            # usable credential and therefore made no request with one. Letting
            # a `GmailOutcomeUnknown` through from here would quarantine a send
            # that was never built.
            raise _definite(exc)
        except Exception as exc:
            raise _definite(_from_http_error(exc)) from exc
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
                try:
                    token = self._refresh(
                        profile, str(profile.get("refresh_token") or "")
                    )
                except GmailError:
                    raise
                except Exception as refresh_exc:
                    # Gmail answered 401, so the first attempt sent nothing,
                    # and the retry was never built. Both halves are known.
                    raise GmailError(
                        "Gmail rejected the access token and refreshing it "
                        f"failed, so nothing was sent. {refresh_exc}"
                    ) from refresh_exc
                headers["Authorization"] = f"Bearer {token}"
                # The retry runs inside this `except`, so the sibling clauses
                # below never see what it raises. Without its own handler a
                # transport error escaped raw, missed every classifier, and was
                # swallowed as an ordinary failure by the tool layer.
                try:
                    if method == "get":
                        return self.http.get(url, headers=headers, **kwargs)
                    return self.http.post(url, headers=headers, **kwargs)
                except GmailError:
                    raise
                except Exception as retry_exc:
                    raise _from_http_error(retry_exc) from retry_exc
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

    def profile_history_id(self) -> str:
        """The mailbox's current history id — the boundary a sync starts from."""
        data = self._request("get", PROFILE_URL) or {}
        history_id = str(data.get("historyId") or "")
        if not history_id:
            raise GmailError("Gmail did not return a history id.")
        return history_id

    def history(
        self, *, start_history_id: str, page_token: str | None = None
    ) -> dict[str, Any]:
        """One page of inbox messages added since ``start_history_id``.

        Scoped to messages added to the inbox so a sync never walks the whole
        mailbox. Gmail answers 404 once the cursor falls out of its history
        window; that is a different situation from a failure, so it gets its
        own error.
        """
        params: dict[str, Any] = {
            "startHistoryId": str(start_history_id),
            "historyTypes": "messageAdded",
            "labelId": "INBOX",
            "maxResults": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            data = self._request("get", HISTORY_URL, params=params) or {}
        except Exception as exc:
            if _http_status(exc) == 404:
                raise GmailHistoryExpired(
                    "The stored Gmail cursor is older than the history Gmail keeps."
                ) from exc
            raise
        message_ids: list[str] = []
        for record in data.get("history") or []:
            for added in (record or {}).get("messagesAdded") or []:
                message = (added or {}).get("message") or {}
                identifier = str(message.get("id") or "")
                if identifier and identifier not in message_ids:
                    message_ids.append(identifier)
        return {
            "message_ids": message_ids,
            "history_id": str(data.get("historyId") or "") or None,
            "next_page_token": str(data.get("nextPageToken") or "") or None,
        }

    def inbound_message(self, *, message_id: str) -> dict[str, Any]:
        data = self._request(
            "get",
            f"{MESSAGES_URL}/{message_id}",
            params={"format": "metadata", "metadataHeaders": list(INBOUND_HEADERS)},
        ) or {}
        return inbound_message_view({"id": message_id, **data})

    def thread(self, *, thread_id: str) -> dict[str, Any]:
        data = self._request(
            "get",
            f"{THREADS_URL}/{thread_id}",
            params={"format": "metadata", "metadataHeaders": list(INBOUND_HEADERS)},
        ) or {}
        return {
            "id": str(data.get("id") or thread_id),
            "messages": [
                inbound_message_view(item)
                for item in (data.get("messages") or [])
                if isinstance(item, dict)
            ],
        }

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
HISTORY_URL = "https://gmail.googleapis.com/gmail/v1/users/me/history"
THREADS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads"

# Everything needed to decide who a reply belongs to, and nothing more.
# `format=metadata` never returns a body, so a background read cannot pull
# message text into a person file even by accident.
INBOUND_HEADERS = ("From", "To", "Cc", "Reply-To", "Delivered-To", "Subject", "Date")


def _received_at(value: Any) -> str | None:
    """Gmail's internalDate, in milliseconds, as an ISO timestamp."""
    try:
        millis = int(str(value))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, UTC).isoformat()


def inbound_message_view(data: dict[str, Any]) -> dict[str, Any]:
    """One Gmail message as the reply reader sees it: identity and headers.

    Shared by the live client and the fake so the fake cannot drift into
    answering a question the real Gmail response could not answer.
    """
    payload = data.get("payload") or {}
    headers = _header_map(payload)
    return {
        "id": str(data.get("id") or ""),
        "thread_id": str(data.get("threadId") or "") or None,
        "label_ids": [str(item) for item in (data.get("labelIds") or [])],
        "from": headers.get("from"),
        "to": headers.get("to"),
        "cc": headers.get("cc"),
        "reply_to": headers.get("reply-to"),
        "delivered_to": headers.get("delivered-to"),
        "subject": headers.get("subject"),
        "snippet": str(data.get("snippet") or ""),
        "received_at": _received_at(data.get("internalDate")) or headers.get("date"),
        "history_id": str(data.get("historyId") or "") or None,
    }


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


def gmail_evidence(tool_name: str, payload: dict[str, Any]) -> EvidenceParts:
    """Adapt a Gmail read or search into evidence envelopes.

    Everything a correspondent controls - sender, subject, snippet, body -
    goes inside the envelope. What is left is Gmail's own identifiers, which
    Sourcecado needs to cite the message and to re-fetch it.
    """
    if not isinstance(payload, dict):
        return opaque("gmail", tool_name, payload)
    if tool_name == "gmail_read":
        message_id = str(payload.get("id") or "")
        body = str(payload.get("body") or "")
        header = "\n".join(
            f"{label}: {payload.get(key) or ''}"
            for label, key in (
                ("From", "from"),
                ("To", "to"),
                ("Subject", "subject"),
                ("Date", "date"),
            )
        )
        snippet = str(payload.get("snippet") or "")
        return external(
            "gmail",
            identity=("message", message_id, payload.get("date")),
            title=str(payload.get("subject") or "Gmail message"),
            body="\n\n".join(part for part in (header, snippet, body) if part),
            metadata={"id": message_id, "sent": bool(payload.get("sent", False))},
            # Gmail caps the extracted body at 8000 characters in `read`.
            truncated=len(body) >= 8000,
            sensitivity="sensitive",
            source_time=str(payload.get("date") or "") or None,
        )
    if tool_name == "gmail_search":
        rows = payload.get("messages")
        rows = rows if isinstance(rows, list) else []
        parts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            message_id = str(row.get("id") or "")
            parts.append(
                external(
                    "gmail",
                    identity=("message", message_id, row.get("date")),
                    title=str(row.get("subject") or "Gmail message"),
                    body="\n".join(
                        f"{label}: {row.get(key) or ''}"
                        for label, key in (
                            ("From", "from"),
                            ("Subject", "subject"),
                            ("Date", "date"),
                        )
                    ),
                    sensitivity="sensitive",
                    source_time=str(row.get("date") or "") or None,
                )
            )
        combined = combine(parts)
        return EvidenceParts(
            metadata={
                "message_ids": [
                    str(row.get("id") or "") for row in rows if isinstance(row, dict)
                ],
                "count": len(rows),
            },
            envelopes=combined.envelopes,
        )
    return opaque("gmail", tool_name, payload)
