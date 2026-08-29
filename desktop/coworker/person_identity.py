"""Safe person-name handling shared by storage, migration, and presentation."""

from __future__ import annotations

import re
from typing import Any

_MASKED_TOKEN = re.compile(r"(?<!\S)[^\s*]*[A-Za-z][^\s*]*\*+[^\s]*(?!\S)")


def apollo_surname_is_masked(value: object) -> bool:
    return "*" in str(value or "")


def without_apollo_name_masks(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _MASKED_TOKEN.sub("(surname hidden by Apollo)", text)


def sanitize_apollo_name_masks(value: Any) -> Any:
    """Recursively replace Apollo mask tokens without guessing their letters."""
    if isinstance(value, str):
        return without_apollo_name_masks(value)
    if isinstance(value, list):
        return [sanitize_apollo_name_masks(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_apollo_name_masks(item) for item in value)
    if isinstance(value, dict):
        return {
            key: sanitize_apollo_name_masks(item) for key, item in value.items()
        }
    return value
