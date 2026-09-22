"""One scanner, used everywhere the update channel produces text.

`coworker/bundle_redaction.py` already owns the pattern vocabulary that decides
whether a string carries a credential or a home path. This module does not add
patterns; it only gathers the values to scan against and applies the same
matcher to the update channel's four output surfaces: the manifest, the update
log lines, the packaged artifact's backend files, and anything a diagnostic
bundle could later pick up.

A second matcher would drift, and the one that drifts is the one that misses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from coworker.bundle_redaction import (
    ScanMatch,
    registered_secret_values,
    relativize,
    scan,
    scan_text,
)

__all__ = [
    "ScanMatch",
    "registered_secrets",
    "refusal_summary",
    "safe_text",
    "scan",
    "scan_text",
]

_CREDENTIAL_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PASSPHRASE")
_MIN_ENV_LENGTH = 16


def registered_secrets(
    state_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Every credential this machine holds, so the scan can refuse on any one.

    These values are used for matching only. They are never rendered, never
    logged, and never placed in a refusal. The environment is included because
    a build runs with signing and connector credentials in it, and the manifest
    is generated from build metadata.
    """
    values: set[str] = set()
    if state_root is not None:
        path = Path(state_root) / "secrets.json"
        if path.is_file():
            try:
                values |= registered_secret_values(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError):
                pass
    for name, value in dict(os.environ if environ is None else environ).items():
        if not any(word in name.upper() for word in _CREDENTIAL_WORDS):
            continue
        if len(value) < _MIN_ENV_LENGTH or value.startswith("/") or "://" in value:
            continue
        values.add(value)
    return frozenset(values)


def refusal_summary(matches: Iterable[ScanMatch]) -> str:
    """Name the categories and where they were found. Never the value."""
    found = sorted({f"{match.category} at {match.location}" for match in matches})
    return ", ".join(found)


def safe_text(
    value: Any,
    *,
    state_root: str | Path,
    home: str | Path | None = None,
    registered: Iterable[str] = (),
) -> str:
    """Operator-facing text with paths anchored, and withheld if it still matches.

    Redaction is not enough on its own. A path can be rewritten; a credential
    cannot be partially rewritten without leaving the part that identifies it.
    So a string that still matches after `relativize` is replaced whole, and the
    replacement names only the category.
    """
    root = Path(state_root)
    dwelling = Path(home) if home is not None else Path.home()
    text = relativize(value, home=dwelling, state_root=root)
    matches = scan_text(
        text,
        registered=registered,
        home=dwelling,
        state_root=root,
        location="update",
    )
    if matches:
        categories = sorted({match.category for match in matches})
        return f"[withheld: {', '.join(categories)}]"
    return text
