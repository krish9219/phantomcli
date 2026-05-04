"""Allow ``python -m phantom.cli`` to run the Typer app directly.

The console-script ``phantom`` (defined in ``pyproject.toml``) is the
primary entry point; this module is a fallback for environments where
the wheel wasn't installed (development checkouts, CI without
``pip install -e``).
"""

from __future__ import annotations

from phantom.cli import main


if __name__ == "__main__":
    main()
