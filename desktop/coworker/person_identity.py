"""Safe person-name handling shared by storage, migration, and presentation."""

from __future__ import annotations

import re
from typing import Any

_MASKED_TOKEN = re.compile(r"(?<!\S)\S*\*+\S*(?!\S)")


def _replace_masked_token(match: re.Match[str]) -> str:
    token = match.group(0)
    # Preserve ordinary Markdown emphasis and horizontal rules. Apollo name
    # masks contain at least one real Unicode letter or number outside the
    # asterisks, regardless of script.
    if token.startswith("*") and token.endswith("*"):
        return token
    if not any(character.isalnum() for character in token if character != "*"):
        return token
    return "(surname hidden by Apollo)"


def apollo_surname_is_masked(value: object) -> bool:
    return "*" in str(value or "")


def without_apollo_name_masks(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _MASKED_TOKEN.sub(_replace_masked_token, text)


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
