"""Inactive assembly contract for versioned Sourcecado system prompts.

Nothing in the active runtime imports this module. It exists so approved prompt
prose can later enter a deterministic, observable boundary without changing the
current sourcing persona during co-authoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class PromptSection:
    id: str
    heading: str
    body: str


@dataclass(frozen=True)
class PromptDiagnostics:
    version: str
    section_ids: tuple[str, ...]
    chars: int
    sha256: str
    budget_chars: int
    remaining_chars: int


@dataclass(frozen=True)
class AssembledPrompt:
    text: str
    diagnostics: PromptDiagnostics


def assemble_prompt(
    *,
    version: str,
    sections: tuple[PromptSection, ...],
    budget_chars: int,
) -> AssembledPrompt:
    seen: set[str] = set()
    for section in sections:
        if section.id in seen:
            raise ValueError(f"duplicate prompt section id: {section.id}")
        seen.add(section.id)
    text = "\n\n".join(
        f"## {section.heading}\n\n{section.body}" for section in sections
    )
    if len(text) > budget_chars:
        raise ValueError(
            f"assembled prompt has {len(text)} characters and exceeds the "
            f"{budget_chars}-character budget"
        )
    return AssembledPrompt(
        text=text,
        diagnostics=PromptDiagnostics(
            version=version,
            section_ids=tuple(section.id for section in sections),
            chars=len(text),
            sha256=sha256(text.encode("utf-8")).hexdigest(),
            budget_chars=budget_chars,
            remaining_chars=budget_chars - len(text),
        ),
    )
