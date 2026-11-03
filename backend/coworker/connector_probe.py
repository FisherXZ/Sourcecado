"""Cheap read-only probes that tell a saved credential from a working one.

`GET /v1/connectors` reads local secrets only. `POST /v1/connectors/check`
runs these probes and the catalog folds the last result in, so a token that
is saved but dead reads as an error instead of Connected.

A probe never spends Apollo credits, never sends, and never returns or logs
exception text: an HTTP error can carry a URL, a response body, or an
authorization header, so outcomes carry fixed plain-language detail only.
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable, Literal

from coworker.apollo import LiveHttp
from coworker.connectors.google_oauth import (
    GoogleTokens,
    google_client_credentials,
    load_google,
)
from coworker.connectors.token_renewal import CredentialRejected, RenewalUnavailable

Outcome = Literal["ok", "rejected", "unreachable"]

PROBE_TIMEOUT_SECONDS = 8.0
PROBE_CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class ProbeResult:
    outcome: Outcome
    detail: str


def credential_fingerprint(*parts: object) -> str | None:
    """A one-way key for the credential a probe ran against. Never stored raw."""
    material = "\x00".join(str(part or "") for part in parts)
    if not material.strip("\x00"):
        return None
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def google_fingerprint(secrets: Any) -> str | None:
    return credential_fingerprint(load_google(secrets).get("refresh_token"))


def granola_fingerprint(secrets: Any) -> str | None:
    tokens = secrets.get("mcp-oauth:granola") or {}
    return credential_fingerprint(tokens.get("access_token"), tokens.get("refresh_token"))


def default_fingerprints(secrets: Any) -> dict[str, Callable[[], str | None]]:
    return {
        "google": lambda: google_fingerprint(secrets),
        "granola": lambda: granola_fingerprint(secrets),
    }


def probe_google(secrets: Any, http: Any | None) -> ProbeResult:
    """Renew the saved Google sign-in once and keep the new access token.

    Gmail, Drive and Calendar share this sign-in, so one renewal checks all three.
    """
    client_id, client_secret = google_client_credentials()
    tokens = GoogleTokens(
        secrets,
        http if http is not None else LiveHttp(),
        client_id=client_id,
        client_secret=client_secret,
    )
    try:
        tokens.renew()
    except CredentialRejected:
        return ProbeResult(
            "rejected",
            "Google no longer accepts the saved sign-in. Reconnect to restore access.",
        )
    except RenewalUnavailable as exc:
        if exc.kind == "setup":
            return ProbeResult(
                "unreachable",
                "Google rejected this device's sign-in setup. "
                "Check the Google OAuth client settings.",
            )
        return ProbeResult("unreachable", str(exc))
    return ProbeResult("ok", "This connection is ready to use.")


ProbeFn = Callable[[], ProbeResult]


class ConnectorProbes:
    """Runs probes concurrently and remembers each result per credential."""

    def __init__(
        self,
        *,
        google: Callable[[], ProbeResult],
        granola: Callable[[], ProbeResult],
        fingerprints: dict[str, Callable[[], str | None]],
        clock: Callable[[], float] = time.monotonic,
        timeout: float = PROBE_TIMEOUT_SECONDS,
        cache_seconds: float = PROBE_CACHE_SECONDS,
    ) -> None:
        self._probes: dict[str, ProbeFn] = {"google": google, "granola": granola}
        self._fingerprints = fingerprints
        self._clock = clock
        self._timeout = timeout
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._results: dict[str, tuple[str, float, ProbeResult]] = {}

    def result(self, key: str) -> ProbeResult | None:
        """The last result for the credential saved now, if any."""
        fingerprint = self._fingerprints[key]()
        with self._lock:
            cached = self._results.get(key)
        if fingerprint is None or cached is None or cached[0] != fingerprint:
            return None
        return cached[2]

    def check(self) -> None:
        """Probe every connector with a saved credential and no fresh result."""
        now = self._clock()
        due: dict[str, str] = {}
        for key in self._probes:
            fingerprint = self._fingerprints[key]()
            if fingerprint is None:
                continue
            with self._lock:
                cached = self._results.get(key)
            fresh = (
                cached is not None
                and cached[0] == fingerprint
                and now - cached[1] < self._cache_seconds
            )
            if not fresh:
                due[key] = fingerprint
        if not due:
            return
        pool = ThreadPoolExecutor(max_workers=len(due))
        try:
            futures = {key: pool.submit(self._safe, self._probes[key]) for key in due}
            deadline = self._clock() + self._timeout
            for key, future in futures.items():
                try:
                    result = future.result(timeout=max(0.0, deadline - self._clock()))
                except FutureTimeout:
                    result = ProbeResult("unreachable", "The connection check timed out.")
                # A probe may renew the token it checks, so file the result
                # under the credential as it stands after the probe.
                fingerprint = self._fingerprints[key]() or due[key]
                with self._lock:
                    self._results[key] = (fingerprint, self._clock(), result)
        finally:
            # A hung probe must not hold the request open past its deadline.
            pool.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _safe(probe: ProbeFn) -> ProbeResult:
        try:
            return probe()
        except Exception:
            return ProbeResult("unreachable", "The connection check failed.")
