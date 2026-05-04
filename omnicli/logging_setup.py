"""
PhantomCLI structured logging.

One-shot initialiser that wires every module's `logging.getLogger("omnicli.*")`
into a rotating file at ~/.phantomcli/phantomcli.log plus the existing console
output. Kept minimal on purpose — production-grade without the ceremony.

Usage:
    from omnicli.logging_setup import configure_logging
    configure_logging()
    import logging
    log = logging.getLogger("omnicli.dashboard")
    log.info("started")

Log level is read from config `log_level` (default INFO). Override via
env var PHANTOM_LOG_LEVEL for quick debugging without touching the DB.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading

_CONFIGURED = False
_LOCK = threading.Lock()

DEFAULT_DIR  = os.path.expanduser("~/.phantomcli")
DEFAULT_FILE = os.path.join(DEFAULT_DIR, "phantomcli.log")


class _SafeFormatter(logging.Formatter):
    """Formatter that tolerates missing attributes from odd loggers."""

    default_fmt = "%(asctime)s  %(levelname)-5s  %(name)-22s  %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.default_fmt, datefmt="%Y-%m-%d %H:%M:%S")


def _resolve_level() -> int:
    env = os.environ.get("PHANTOM_LOG_LEVEL")
    if env:
        return logging.getLevelName(env.upper()) if not env.isdigit() else int(env)
    try:
        from omnicli.memory import get_config
        raw = (get_config("log_level", "INFO") or "INFO").upper()
        return logging.getLevelName(raw) if not raw.isdigit() else int(raw)
    except Exception:
        return logging.INFO


def configure_logging(
    log_file: str | None = None,
    console: bool = True,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 3,
) -> str:
    """
    Initialise the root `omnicli` logger. Safe to call multiple times — only
    the first call installs handlers. Returns the absolute log file path.
    """
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            return log_file or DEFAULT_FILE

        try:
            os.makedirs(DEFAULT_DIR, exist_ok=True)
        except OSError:
            pass

        path   = log_file or DEFAULT_FILE
        level  = _resolve_level()
        logger = logging.getLogger("omnicli")
        logger.setLevel(level)
        logger.propagate = False

        fmt = _SafeFormatter()

        handlers: list[logging.Handler] = []
        try:
            fh = logging.handlers.RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            fh.setLevel(level)
            handlers.append(fh)
        except OSError:
            # Fall back gracefully — console still works even if ~ is read-only.
            pass

        if console:
            ch = logging.StreamHandler(stream=sys.stderr)
            ch.setFormatter(fmt)
            # Console: only WARNING+ to avoid noise in the REPL, unless
            # explicitly overridden via env var.
            ch.setLevel(level if os.environ.get("PHANTOM_LOG_LEVEL") else logging.WARNING)
            handlers.append(ch)

        for h in handlers:
            logger.addHandler(h)

        # Catch unraisable tracebacks (background threads) and route to log.
        def _excepthook(exc_type, exc, tb):
            logger.error("uncaught exception", exc_info=(exc_type, exc, tb))
            if sys.__excepthook__:
                sys.__excepthook__(exc_type, exc, tb)
        try:
            sys.excepthook = _excepthook
        except Exception:
            pass

        _CONFIGURED = True
        logger.info("logging initialised  file=%s  level=%s", path, logging.getLevelName(level))
        return path
