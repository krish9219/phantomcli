"""Skill bundle — parsed SKILL.md.

Frontmatter syntax: a leading ``---`` block of YAML-ish key/value pairs,
followed by ``---`` and the body. We parse a strict subset (string
values, comma-separated lists) to avoid pulling in PyYAML for a 5-key
file.

Required frontmatter:

* ``name`` — unique identifier.
* ``description`` — one-line summary the agent uses to decide whether
  to activate.

Optional:

* ``tags`` — comma-separated keywords.
* ``trigger`` — comma-separated phrases that strongly suggest activation.
* ``resources`` — comma-separated relative paths the skill expects to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from phantom.errors import PhantomError

__all__ = ["SkillBundle"]


_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class SkillBundle:
    name: str
    description: str
    body: str
    tags: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    source_dir: Path = field(default_factory=lambda: Path("."))

    @classmethod
    def load(cls, source_dir: str | Path) -> "SkillBundle":
        d = Path(source_dir)
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            raise PhantomError(f"no SKILL.md in {d}")
        text = skill_md.read_text(encoding="utf-8")
        m = _FRONT_RE.match(text)
        if not m:
            raise PhantomError(
                f"SKILL.md in {d} must begin with a --- frontmatter block"
            )
        body = text[m.end():].strip()
        kv: dict[str, str] = {}
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            kv[key.strip()] = value.strip()
        for required in ("name", "description"):
            if required not in kv or not kv[required]:
                raise PhantomError(
                    f"SKILL.md in {d} missing required frontmatter key {required!r}"
                )
        return cls(
            name=kv["name"],
            description=kv["description"],
            body=body,
            tags=_split_csv(kv.get("tags", "")),
            triggers=_split_csv(kv.get("trigger", "")),
            resources=_split_csv(kv.get("resources", "")),
            source_dir=d,
        )

    def matches(self, query: str) -> bool:
        """True iff *query* contains any of this skill's triggers
        (case-insensitive)."""
        if not self.triggers:
            return False
        q = query.lower()
        return any(t.lower() in q for t in self.triggers)
