"""PyInstaller entry point for the v1.0 `phantom` binary.

The legacy ``run.py`` boots the v3 omnicli CLI for backwards compat.
This file boots the v4 phantom CLI, which exposes every v1.0 command
(daemon, swarm, self-dev, bench, dictate, plugin install, etc.) and
re-uses the omnicli chat implementation under ``phantom chat``.
"""

from __future__ import annotations

from phantom.cli import main


if __name__ == "__main__":
    main()
