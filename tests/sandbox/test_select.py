"""Tests for :mod:`phantom.sandbox.select`.

We test the selection algorithm with a fake backend pool so the result
is deterministic regardless of what the host has installed.
"""

from __future__ import annotations

import pytest

from phantom.errors import SandboxUnavailableError
from phantom.sandbox._backend import SandboxBackend
from phantom.sandbox.policy import SandboxPolicy
from phantom.sandbox.result import SandboxResult
from phantom.sandbox.select import (
    PHANTOM_SANDBOX_TIER_ENV,
    available_backends,
    clear_cache,
    select_backend,
)


class _FakeBackend(SandboxBackend):
    """Test double — a backend that probes True/False per construction.

    Note: the ABC declares :attr:`name` and :attr:`tier_rank` as final
    ClassVars. We satisfy the type-checker via instance assignment in
    ``__init__``; this is fine for a test double.
    """

    def __init__(self, name: str, tier_rank: int, *, available: bool):
        self.name = name  # type: ignore[misc]
        self.tier_rank = tier_rank  # type: ignore[misc]
        self._available = available

    def probe(self) -> bool:
        return self._available

    def launch(self, argv, policy):  # noqa: ARG002
        return SandboxResult(
            stdout="", stderr="", exit_code=0, wall_s=0.0,
            tier=self.name, truncated=False,
        )


@pytest.fixture(autouse=True)
def _clean_cache():
    """Each test starts with a fresh selection cache."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def env_clean(monkeypatch):
    """Make sure no leftover env var from another test or the host."""
    monkeypatch.delenv(PHANTOM_SANDBOX_TIER_ENV, raising=False)


class TestSelectBackend:
    def test_returns_lowest_rank_available(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=False),
            _FakeBackend("beta", 2, available=True),
            _FakeBackend("gamma", 3, available=True),
        ]
        chosen = select_backend(backends=pool)
        assert chosen.name == "beta"

    def test_skips_unavailable(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=False),
            _FakeBackend("beta", 2, available=False),
            _FakeBackend("gamma", 3, available=True),
        ]
        chosen = select_backend(backends=pool)
        assert chosen.name == "gamma"

    def test_raises_when_none_available(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=False),
            _FakeBackend("beta", 2, available=False),
        ]
        with pytest.raises(SandboxUnavailableError, match="no sandbox backend"):
            select_backend(backends=pool)

    def test_explicit_preferred_overrides_rank(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=True),
        ]
        chosen = select_backend(preferred="beta", backends=pool)
        assert chosen.name == "beta"

    def test_preferred_unavailable_falls_through(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=False),
        ]
        # Operator preferred 'beta' but it's unavailable; we fall through
        # to the highest-ranked available backend.
        chosen = select_backend(preferred="beta", backends=pool)
        assert chosen.name == "alpha"

    def test_disabled_skips_named(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=True),
        ]
        chosen = select_backend(disabled=frozenset({"alpha"}), backends=pool)
        assert chosen.name == "beta"

    def test_disabled_takes_priority_over_preferred(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=True),
        ]
        # If the user pinned a tier AND disabled it, the disable wins —
        # operator policy is the higher-priority signal.
        chosen = select_backend(
            preferred="alpha",
            disabled=frozenset({"alpha"}),
            backends=pool,
        )
        assert chosen.name == "beta"

    def test_env_var_acts_as_preferred(self, monkeypatch):
        monkeypatch.setenv(PHANTOM_SANDBOX_TIER_ENV, "gamma")
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=True),
            _FakeBackend("gamma", 3, available=True),
        ]
        chosen = select_backend(backends=pool)
        assert chosen.name == "gamma"

    def test_explicit_preferred_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv(PHANTOM_SANDBOX_TIER_ENV, "alpha")
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=True),
        ]
        chosen = select_backend(preferred="beta", backends=pool)
        assert chosen.name == "beta"

    def test_empty_env_var_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv(PHANTOM_SANDBOX_TIER_ENV, "")
        pool = [_FakeBackend("alpha", 1, available=True)]
        chosen = select_backend(backends=pool)
        assert chosen.name == "alpha"


class TestSelectionCache:
    def test_results_cached(self, env_clean):
        # If the cache works, mutating the pool after the first call
        # should not change the second call's result.
        b1 = _FakeBackend("alpha", 1, available=True)
        pool = [b1]
        first = select_backend(backends=pool)
        b1._available = False  # noqa: SLF001
        second = select_backend(backends=pool)
        assert first is second

    def test_clear_cache_forces_reprobe(self, env_clean):
        b1 = _FakeBackend("alpha", 1, available=True)
        pool = [b1]
        select_backend(backends=pool)
        b1._available = False  # noqa: SLF001
        clear_cache()
        with pytest.raises(SandboxUnavailableError):
            select_backend(backends=pool)

    def test_different_keys_cache_independently(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=True),
        ]
        a = select_backend(backends=pool)
        b = select_backend(preferred="beta", backends=pool)
        assert a.name == "alpha"
        assert b.name == "beta"


class TestAvailableBackends:
    def test_returns_only_available(self, env_clean):
        pool = [
            _FakeBackend("alpha", 1, available=True),
            _FakeBackend("beta", 2, available=False),
            _FakeBackend("gamma", 3, available=True),
        ]
        out = available_backends(backends=pool)
        names = [b.name for b in out]
        assert names == ["alpha", "gamma"]

    def test_returns_empty_when_none_available(self, env_clean):
        pool = [_FakeBackend("alpha", 1, available=False)]
        out = available_backends(backends=pool)
        assert out == []
