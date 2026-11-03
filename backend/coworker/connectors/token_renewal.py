"""Shared outcomes for renewing an OAuth access token from a saved refresh token.

Every connector that signs in with OAuth renews the same way: exchange the
saved refresh token, keep a rotated refresh token if the provider issues one,
and never save an empty access token. These errors carry fixed wording only.
A token endpoint's error can echo the request, so its text never travels.
"""

from __future__ import annotations

from typing import Any, Literal


class RenewalError(RuntimeError):
    """A renewal that produced no usable access token."""


class CredentialRejected(RenewalError):
    """The provider refused the saved sign-in. Only reconnecting fixes it."""


class RenewalUnavailable(RenewalError):
    """Renewal could not finish for a reason reconnecting would not fix."""

    def __init__(self, message: str, *, kind: Literal["setup", "unreachable"]) -> None:
        super().__init__(message)
        self.kind = kind


def classify_token_error(exc: BaseException, *, provider: str) -> RenewalError:
    """Map a token-endpoint failure onto a renewal outcome without its text."""
    status = getattr(exc, "status", None)
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    if error == "invalid_grant":
        return CredentialRejected(
            f"{provider} no longer accepts the saved sign-in. Reconnect to restore access."
        )
    if error in ("invalid_client", "unauthorized_client") or status in (400, 401, 403):
        return RenewalUnavailable(
            f"{provider} rejected this device's sign-in setup.", kind="setup"
        )
    if isinstance(status, int):
        return RenewalUnavailable(
            f"{provider} did not respond normally. Try again shortly.", kind="unreachable"
        )
    return RenewalUnavailable(f"Sourcecado could not reach {provider}.", kind="unreachable")


def access_from(tokens: Any, *, provider: str) -> tuple[str, str]:
    """The new access token and any rotated refresh token, or a renewal error."""
    if not isinstance(tokens, dict):
        raise RenewalUnavailable(f"{provider} returned an unreadable token.", kind="unreachable")
    access = str(tokens.get("access_token") or "")
    if not access:
        raise RenewalUnavailable(f"{provider} returned no access token.", kind="unreachable")
    return access, str(tokens.get("refresh_token") or "")
