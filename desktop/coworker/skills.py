"""Sourcecado skill loading: catalog metadata first, instructions on demand."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


BUILTIN_SKILLS = Path(__file__).resolve().parent / "builtin_skills"


@dataclass
class Skill:
    name: str
    description: str
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
                    skill = parse_skill(md)
                    self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        skill = self._skills.get(name)
        if skill is None:
            self.rescan()
            skill = self._skills.get(name)
        return skill

    def catalog(self) -> list[dict[str, str]]:
        return [{"name": s.name, "description": s.description} for s in self._skills.values()]

    def names(self) -> list[str]:
        return list(self._skills)


def parse_skill(md: Path) -> Skill:
    text = md.read_text(encoding="utf-8")
    name, description, allowed, body = md.parent.name, "", [], text
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
                elif key in ("allowed-tools", "allowed_tools"):
                    allowed = [part.strip() for part in value.split(",") if part.strip()]
    return Skill(
        name=name,
        description=description,
        instructions=body.strip(),
        path=str(md.parent),
        allowed_tools=allowed,
    )


def catalog_text(loader: SkillLoader) -> str:
    rows = loader.catalog()
    if not rows:
        return ""
    lines = [f"- {row['name']}: {row['description']}" for row in rows]
    return (
        "Available skills — call load_skill with the name to load full instructions:\n"
        + "\n".join(lines)
    )
