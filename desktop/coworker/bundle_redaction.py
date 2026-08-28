"""The last line of defence before a diagnostic bundle leaves this machine.

Two upstream layers already bound what a bundle can carry: the run receipt's
closed field allowlist, and the state report's own bounded, redacted output
contract. This module is the third, and it is deliberately independent of both.

Independence is the whole point. A scan that imported an upstream redactor
would keep passing after that redactor changed, which is exactly the failure a
third layer exists to catch. So this file imports nothing from the rest of
Sourcecado, carries its own pattern vocabulary, and is tested on its own.

Two jobs:

`relativize` and `scrub` rewrite absolute paths before anything is packaged.
A path anchored in the operator's home directory identifies a real person, so
the whole path token collapses to `<home>`; a path inside the state directory
keeps its remainder, because `<state>/club.db` is diagnostic and safe.

`scan` refuses. It reports a category and a location and never the matched
value, so a refusal can be logged and shown without becoming the leak it was
trying to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

STATE_LABEL = "<state>"
HOME_LABEL = "<home>"

# The shortest string in the registered store worth treating as a credential.
# Below this a value is a connector name, a scope word, or a status.
MIN_REGISTERED_LENGTH = 12


@dataclass(frozen=True)
class ScanMatch:
    """One refusal. Carries where and what kind, never the value itself."""

    category: str
    location: str


SCAN_CATEGORIES = frozenset(
    {
        "registered_secret",
        "private_key",
        "json_web_token",
        "issued_credential",
        "authorization_header",
        "credential_assignment",
        "home_path",
    }
)

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.IGNORECASE
)
_JSON_WEB_TOKEN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
)
# Issuer prefixes that mean "credential" on their own. Each one is a published
# token format, so a match here is never a guess about entropy.
_ISSUED_CREDENTIAL = re.compile(
    r"(?:sk-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_-]{16,}"
    r"|rk-[A-Za-z0-9_-]{16,}"
    r"|pk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{16,}"
    r"|shpat_[A-Za-z0-9]{16,}"
    r"|dop_v1_[A-Za-z0-9]{16,}"
    r"|npm_[A-Za-z0-9]{20,}"
    r"|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"|A(?:KIA|SIA)[0-9A-Z]{16}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|ya29\.[A-Za-z0-9._-]{12,}"
    r"|1//[A-Za-z0-9_-]{12,})"
)
# An authorization value, either behind the header name or bare. The value is
# captured so a plain English sentence — "Basic authentication is required" —
# cannot refuse an export: a real one is never all letters.
_AUTHORIZATION_HEADER = re.compile(
    r"(?i)(?:authorization[\"']?\s*[:=]\s*[\"']?)?"
    r"\b(?:Bearer|Basic)\s+(?P<value>[A-Za-z0-9._\-+/=]{12,})"
)
_WORD = re.compile(r"^[A-Za-z]+$")
# A named credential assigned a value. The name must end at a word boundary so
# `input_tokens` and `context_window_tokens` are not credential names.
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b[\w.\-]*"
    r"(?:api[_-]?key|secret|password|passphrase|credential|cookie|token"
    r"|authorization|client[_-]?secret)"
    r"\b[\"']?\s*[:=]\s*[\"']?"
    r"(?P<value>[^\s\"',;}\]]{12,})"
)
_HOME_PATH = re.compile(
    r"(?<![\w/])(?:/Users/|/home/)[^\s\"',;:]+"
    r"|\b[A-Za-z]:\\[Uu]sers\\[^\s\"',;]+"
)
# A placeholder is not a value. Without this a redacted assignment would look
# like a credential assignment on the next pass.
_PLACEHOLDER = re.compile(r"^[<\[](?:redacted|home|state)", re.IGNORECASE)
_DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?$")


def relativize(value: Any, *, home: Path, state_root: Path) -> str:
    """Anchor every absolute path, and erase the ones anchored in a home.

    The state directory keeps its remainder because the file that is broken is
    the diagnostic fact. A home path loses everything after `<home>`: the
    directory names and the file name are the operator's business, and the only
    thing a reader needs is that the path pointed outside Sourcecado's state.
    """
    text = str(value)
    root = str(state_root)
    if len(root) > 1:
        text = text.replace(root, STATE_LABEL)
    dwelling = str(home)
    if len(dwelling) > 1:
        text = re.sub(re.escape(dwelling) + r"[^\s\"',;:]*", HOME_LABEL, text)
    return _HOME_PATH.sub(HOME_LABEL, text)


def scrub(value: Any, *, home: Path, state_root: Path) -> Any:
    """Apply `relativize` to every string in a JSON-shaped structure."""
    if isinstance(value, str):
        return relativize(value, home=home, state_root=state_root)
    if isinstance(value, dict):
        return {
            key: scrub(item, home=home, state_root=state_root)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(item, home=home, state_root=state_root) for item in value]
    return value


def registered_secret_values(payload: Any) -> frozenset[str]:
    """Every value in the registered secret store worth scanning for.

    A URL and an email address are excluded deliberately. Both appear in that
    store beside the credentials — scope URLs, token endpoints, the connected
    account — and both also appear legitimately elsewhere, so treating them as
    secrets would refuse every export rather than the leaking ones.
    """
    found: set[str] = set()
    _collect(payload, found)
    return frozenset(found)


def _collect(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        if len(value) < MIN_REGISTERED_LENGTH:
            return
        if "://" in value or _looks_like_address(value):
            return
        found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect(item, found)


def _looks_like_address(value: str) -> bool:
    local, separator, domain = value.partition("@")
    return bool(separator) and "." in domain and " " not in value


def scan(
    value: Any,
    *,
    registered: Iterable[str],
    home: Path,
    state_root: Path,
    location: str = "",
) -> tuple[ScanMatch, ...]:
    """Walk a JSON-shaped structure and report every refusal reason found."""
    secrets = tuple(registered)
    matches: list[ScanMatch] = []
    _walk(value, secrets, home, state_root, location, matches)
    return tuple(matches)


def scan_text(
    text: str,
    *,
    registered: Iterable[str],
    home: Path,
    state_root: Path,
    location: str,
) -> tuple[ScanMatch, ...]:
    """Scan one already-serialised blob, such as a file about to be packaged."""
    return _scan_one(text, tuple(registered), home, state_root, location)


def _walk(
    value: Any,
    secrets: tuple[str, ...],
    home: Path,
    state_root: Path,
    location: str,
    matches: list[ScanMatch],
) -> None:
    if isinstance(value, str):
        matches.extend(_scan_one(value, secrets, home, state_root, location))
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk(item, secrets, home, state_root, _join(location, key), matches)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk(item, secrets, home, state_root, _join(location, index), matches)


def _join(location: str, part: Any) -> str:
    return f"{location}.{part}" if location else str(part)


def _scan_one(
    text: str,
    secrets: tuple[str, ...],
    home: Path,
    state_root: Path,
    location: str,
) -> tuple[ScanMatch, ...]:
    found: list[ScanMatch] = []
    for secret in secrets:
        if secret and secret in text:
            found.append(ScanMatch("registered_secret", location or "<root>"))
            break
    if _PRIVATE_KEY.search(text):
        found.append(ScanMatch("private_key", location or "<root>"))
    if _JSON_WEB_TOKEN.search(text):
        found.append(ScanMatch("json_web_token", location or "<root>"))
    if _ISSUED_CREDENTIAL.search(text):
        found.append(ScanMatch("issued_credential", location or "<root>"))
    if any(
        not _WORD.match(match.group("value"))
        for match in _AUTHORIZATION_HEADER.finditer(text)
    ):
        found.append(ScanMatch("authorization_header", location or "<root>"))
    if _assigned_credential(text):
        found.append(ScanMatch("credential_assignment", location or "<root>"))
    if _HOME_PATH.search(_state_relative(text, state_root=state_root)):
        found.append(ScanMatch("home_path", location or "<root>"))
    return tuple(found)


def _state_relative(text: str, *, state_root: Path) -> str:
    """Take the state directory out of the way before looking for home paths.

    A state directory legitimately sits inside a home directory, and the export
    prints it as `<state>/…`. Only what survives that substitution is an
    unmodelled home path.
    """
    root = str(state_root)
    return text.replace(root, STATE_LABEL) if len(root) > 1 else text


def _assigned_credential(text: str) -> bool:
    for match in _CREDENTIAL_ASSIGNMENT.finditer(text):
        value = match.group("value")
        if _PLACEHOLDER.match(value) or _DECIMAL.match(value):
            continue
        return True
    return False
