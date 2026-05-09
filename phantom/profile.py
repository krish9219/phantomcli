"""User profile — assistant_name, user_name, workspace_path, first_seen.

Persisted at ``$PHANTOM_HOME/profile.json`` (mode 0600). Created on first
``phantom chat`` if missing; the chat REPL prompts the user for these
values once and then never asks again.

The profile is *informational* — it shapes the system prompt, the boot
banner, and where the agent creates files by default. It does not gate
any feature; any field can be empty without breaking chat.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "Profile",
    "default_workspace_hint",
    "load_profile",
    "profile_path",
    "save_profile",
]


def profile_path() -> Path:
    base = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base / "profile.json"


@dataclass
class Profile:
    user_name: str = ""           # what to call the user (e.g. "Aravind")
    assistant_name: str = "Phantom"  # what the user calls Phantom
    workspace_path: str = ""      # default project root for `write_file` etc.
    first_seen: str = ""          # ISO-8601 of first onboarding
    god_mode: bool = False        # system-prompt unlock; toggled by /god-mode

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        out = cls()
        for f in ("user_name", "assistant_name", "workspace_path", "first_seen"):
            v = data.get(f)
            if isinstance(v, str):
                setattr(out, f, v)
        if isinstance(data.get("god_mode"), bool):
            out.god_mode = data["god_mode"]
        return out

    def is_complete(self) -> bool:
        """True when the user has answered the first-run questions.

        ``assistant_name`` and ``user_name`` together imply the user has
        been through onboarding at least once.
        """
        return bool(self.user_name and self.assistant_name and self.workspace_path)


def default_workspace_hint() -> str:
    """Suggest a workspace path. ~/Projects/ on POSIX, %USERPROFILE%\\Projects\\ on Windows."""
    home = Path.home()
    if platform.system() == "Windows":
        return str(home / "Projects")
    return str(home / "Projects")


def load_profile() -> Profile:
    p = profile_path()
    if not p.exists():
        return Profile()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Profile()
    if not isinstance(data, dict):
        return Profile()
    return Profile.from_dict(data)


def save_profile(profile: Profile) -> None:
    if not profile.first_seen:
        profile.first_seen = datetime.now(timezone.utc).isoformat()
    p = profile_path()
    p.write_text(json.dumps(asdict(profile), indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
