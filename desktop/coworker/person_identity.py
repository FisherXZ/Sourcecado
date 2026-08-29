"""Safe person-name handling shared by storage, migration, and presentation."""

from __future__ import annotations

import re

_MASKED_TOKEN = re.compile(r"(?<!\S)[^\s*]*[A-Za-z][^\s*]*\*+[^\s]*(?!\S)")


def apollo_surname_is_masked(value: object) -> bool:
    return "*" in str(value or "")


def without_apollo_name_masks(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _MASKED_TOKEN.sub("(surname hidden by Apollo)", text)
