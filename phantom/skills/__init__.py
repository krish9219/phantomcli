"""Phantom skills — Anthropic-style skill bundle format.

A skill is a directory containing a ``SKILL.md`` (frontmatter + body)
and optional resources. Skills are activated on demand by the agent
loop; until activated, a skill's content is not in the prompt.

Stage 5 ships:

* :class:`SkillBundle`   — frontmatter + body, immutable.
* :class:`SkillLoader`   — discovers bundles in `~/.phantom/skills/`.
* :class:`SkillActivator` — choose-when-to-activate policy.
"""

from __future__ import annotations

from phantom.skills.bundle import SkillBundle
from phantom.skills.loader import SkillLoader, builtin_skills_dir, user_skills_dir

__all__ = [
    "SkillBundle",
    "SkillLoader",
    "builtin_skills_dir",
    "user_skills_dir",
]
