"""Skill discovery."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from phantom.errors import PhantomError
from phantom.skills.bundle import SkillBundle

__all__ = ["SkillLoader", "builtin_skills_dir", "user_skills_dir"]

log = logging.getLogger(__name__)


def user_skills_dir() -> Path:
    base = os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom")
    p = Path(base) / "skills"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p


def builtin_skills_dir() -> Path:
    return Path(__file__).resolve().parent / "builtin"


class SkillLoader:
    """Discovers skill bundles."""

    def __init__(self, *, search_paths: list[Path] | None = None) -> None:
        if search_paths is None:
            search_paths = [builtin_skills_dir(), user_skills_dir()]
        self._search_paths = tuple(search_paths)

    @property
    def search_paths(self) -> tuple[Path, ...]:
        return self._search_paths

    def discover(self) -> list[SkillBundle]:
        out: list[SkillBundle] = []
        seen: set[str] = set()
        for root in self._search_paths:
            if not root.exists():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if not (child / "SKILL.md").exists():
                    continue
                try:
                    bundle = SkillBundle.load(child)
                except PhantomError as exc:
                    log.warning("skipping %s: %s", child, exc)
                    continue
                if bundle.name in seen:
                    log.warning("duplicate skill name %r", bundle.name)
                    continue
                seen.add(bundle.name)
                out.append(bundle)
        return out

    def select_for(self, query: str) -> list[SkillBundle]:
        """Return skills whose triggers match *query*, in discovery order."""
        return [b for b in self.discover() if b.matches(query)]
