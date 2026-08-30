"""Sourcecado skill loading: catalog metadata first, instructions on demand."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from coworker.agent_runs import redact_secrets


BUILTIN_SKILLS = Path(__file__).resolve().parent / "builtin_skills"
# Public URLs and relative references are useful in instructions. Absolute
# file URIs, POSIX paths, home shorthand, drive paths, and UNC paths are not.
_PRIVATE_PATH = re.compile(
    r"\bfile://(?:localhost)?/[^\s`<>\"'\[\](){}]+"
    r"|(?<![A-Za-z0-9_])\\\\[^\s`<>\"'\[\](){}]+"
    r"|(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s`<>\"'\[\](){}]+"
    r"|(?<![A-Za-z0-9_])~[\\/][^\s`<>\"'\[\](){}]+"
    r"|(?<![:/A-Za-z0-9_])/(?!/)[^\s`<>\"'\[\](){}]+",
    re.IGNORECASE,
)


@dataclass
class Skill:
    name: str
    description: str
    use_when: str = ""
    source: str = "workspace"
    instructions: str = ""
    path: str = ""
    allowed_tools: list[str] = field(default_factory=list)


class SkillLoader:
    def __init__(self, dirs: list[str | Path]) -> None:
        self._dirs = [Path(d) for d in dirs]
        self._skills: dict[str, Skill] = {}
        self.rescan()

    def rescan(self) -> None:
        self._skills = {}
        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for sub in sorted(directory.iterdir()):
                md = sub / "SKILL.md"
                if md.is_file():
                    source = (
                        "builtin"
                        if directory.resolve() == BUILTIN_SKILLS.resolve()
                        else "workspace"
                    )
                    skill = parse_skill(md, source=source)
                    self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        skill = self._skills.get(name)
        if skill is None:
            self.rescan()
            skill = self._skills.get(name)
        return skill

    def catalog(self) -> list[dict[str, str]]:
        return [
            {
                "name": _safe_public_text(skill.name),
                "purpose": _safe_public_text(skill.description),
                "use_when": _safe_public_text(skill.use_when),
                "source": skill.source if skill.source in {"builtin", "workspace"} else "workspace",
                "status": "ready",
                "instructions": _safe_public_text(skill.instructions),
            }
            for skill in self._skills.values()
        ]

    def names(self) -> list[str]:
        return list(self._skills)


def _safe_public_text(value: str) -> str:
    def replace_path(match: re.Match[str]) -> str:
        path = match.group(0)
        trailing = ""
        while path and path[-1] in ".,;:!?":
            trailing = path[-1] + trailing
            path = path[:-1]
        return f"[REDACTED PATH]{trailing}"

    return _PRIVATE_PATH.sub(replace_path, redact_secrets(str(value)))


def parse_skill(md: Path, *, source: str = "workspace") -> Skill:
    text = md.read_text(encoding="utf-8")
    name, description, use_when, allowed, body = md.parent.name, "", "", [], text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            frontmatter = text[3:end]
            body = text[end + 4 :].lstrip("\n")
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                key, value = key.strip().lower(), value.strip()
                if key == "name" and value:
                    name = value
                elif key == "description":
                    description = value
                elif key in ("use-when", "use_when"):
                    use_when = value
                elif key in ("allowed-tools", "allowed_tools"):
                    allowed = [part.strip() for part in value.split(",") if part.strip()]
    return Skill(
        name=name,
        description=description,
        use_when=use_when,
        source=source,
        instructions=body.strip(),
        path=str(md.parent),
        allowed_tools=allowed,
    )


def catalog_text(loader: SkillLoader) -> str:
    rows = loader.catalog()
    if not rows:
        return ""
    lines = [f"- {row['name']}: {row['purpose']}" for row in rows]
    return (
        "Available skills — call load_skill with the name to load full instructions:\n"
        + "\n".join(lines)
    )
