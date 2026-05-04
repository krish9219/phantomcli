"""
Shared pytest fixtures for the Phantom test suite.

Isolates every test from the user's real ~/.omnicli/memory.db by pointing
memory.DB_PATH at a per-test tmp file.
"""
from __future__ import annotations

import os
import sys

import pytest

# Make `omnicli` importable regardless of how pytest is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point memory.DB_PATH at a fresh temp DB and clear the Fernet key so
    _SENSITIVE_KEYS encryption uses a fresh keyring entry (if any)."""
    from omnicli import memory

    tmp_db = tmp_path / "memory.db"
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_db))

    # Ensure the DB is initialised once before the test runs, so tests that
    # only call save_config / get_config don't race on init.
    memory.init_db()
    yield tmp_db


@pytest.fixture
def isolated_hooks_config(tmp_path, monkeypatch):
    """Point the hooks module at a tmp hooks.json for the duration of a test."""
    cfg = tmp_path / "hooks.json"
    monkeypatch.setenv("PHANTOM_HOOKS_CONFIG", str(cfg))
    return cfg
