"""Markdown persona — YAML frontmatter + body as the on-duty identity.

Copied shape from OpenWorker `coworker/personas/manifest.py`: frontmatter is
identity, the markdown body is the system prompt. No PyYAML; slice 7 only
needs id, name, tools, body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class ManifestError(ValueError):
    pass


@dataclass
class Persona:
    id: str
    name: str
    body: str
    tools: list[str] = field(default_factory=list)
    path: Path | None = None


BUILTIN_DIR = Path(__file__).resolve().parent / "personas"
REPO_PERSONAS = Path(__file__).resolve().parents[2] / "personas"


def parse_persona(text: str, *, path: Path | None = None) -> Persona:
    raw = text.lstrip("\ufeff")
    if not raw.startswith("---"):
        raise ManifestError("persona markdown needs YAML frontmatter")
    rest = raw[3:]
    end = rest.find("\n---")
    if end < 0:
        raise ManifestError("persona frontmatter is not closed")
    meta_text = rest[:end]
    body = rest[end + 4 :].strip()
    if not body:
        raise ManifestError("persona body is empty")
    meta: dict[str, str] = {}
    for line in meta_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    pid = meta.get("id") or ""
    name = meta.get("name") or ""
    if not pid or not name:
        raise ManifestError("persona frontmatter needs id and name")
    return Persona(id=pid, name=name, body=body, tools=_parse_list(meta.get("tools") or ""), path=path)


def _parse_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [part.strip() for part in raw.split(",") if part.strip()]


def persona_path(persona_id: str) -> Path:
    if persona_id == "sourcing":
        path = REPO_PERSONAS / "sourcing.md"
        if path.is_file():
            return path
    path = BUILTIN_DIR / f"{persona_id}.md"
    if path.is_file():
        return path
    raise ManifestError(f"unknown persona {persona_id}")


def load_persona(persona_id: str) -> Persona:
    path = persona_path(persona_id)
    return parse_persona(path.read_text(encoding="utf-8"), path=path)
