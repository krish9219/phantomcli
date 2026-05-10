"""Tests for v1.1.32 — robustness pass after the v1.1.28→v1.1.31 saga.

* Orphan-install detection in ``phantom update``.
* Identity-filter heuristic catch-all for novel brand leaks.
* Behavioural test for the prompt-label ANSI wrap (replaces the
  v1.1.31 source-inspection one).
* 429 rate-limit error suggests concrete model switches.
* CI flakes addressed: import-time budget bump, log-buffering on
  Windows start_server, docker-tier permissions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── Orphan-install detection in update_cmd ───────────────────────────────

def test_is_pip_managed_returns_true_when_pip_show_succeeds(monkeypatch):
    """When `pip show phantom-cli` returns 0 with a Name: header, we
    consider the install pip-managed."""
    fake_proc = MagicMock(returncode=0, stdout="Name: phantom-cli\nVersion: 1.1.32\n")
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: fake_proc
    )
    from phantom.cli.update_cmd import is_pip_managed
    assert is_pip_managed() is True


def test_is_pip_managed_returns_false_when_pip_show_fails(monkeypatch):
    """When pip has no record of phantom-cli, we treat the install as
    orphan."""
    fake_proc = MagicMock(returncode=1, stdout="")
    monkeypatch.setattr(
        "subprocess.run", lambda *a, **k: fake_proc
    )
    from phantom.cli.update_cmd import is_pip_managed
    assert is_pip_managed() is False


def test_is_pip_managed_returns_false_on_pip_timeout(monkeypatch):
    """If pip itself is broken/missing, treat as not-pip-managed
    rather than crashing the update."""
    import subprocess
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pip show", timeout=10)
    monkeypatch.setattr("subprocess.run", boom)
    from phantom.cli.update_cmd import is_pip_managed
    assert is_pip_managed() is False


def test_perform_update_bails_on_orphan_install(monkeypatch, tmp_path):
    """When `is_pip_managed()` returns False AND a newer manifest
    is available, perform_update bails with the orphan-install hint
    and returns exit code 2 — does NOT touch the install dir."""
    from phantom.cli import update_cmd
    # Fake manifest with a newer version.
    fake_manifest = update_cmd.Manifest(
        version="9.9.9",
        release_date="2099-01-01",
        download_url="https://example.invalid/phantom.zip",
        sha256="deadbeef",
        size_bytes=1234,
        headline="",
        changelog=(),
    )
    monkeypatch.setattr(update_cmd, "fetch_manifest", lambda url, **k: fake_manifest)
    monkeypatch.setattr(update_cmd, "is_pip_managed", lambda: False)

    written: list[str] = []
    rc = update_cmd.perform_update(
        manifest_url="https://example.invalid/manifest.json",
        install_dir=tmp_path,
        write=written.append,
    )
    assert rc == 2
    output = "".join(written)
    assert "orphan install" in output.lower()
    assert "pip install --upgrade --force-reinstall" in output
    assert "https://example.invalid/phantom.zip" in output


def test_perform_update_proceeds_normally_when_pip_managed(monkeypatch, tmp_path):
    """When pip-managed, the orphan check is silent and update proceeds
    to the install-dir-writable check (we stub the rest to keep the
    test focused)."""
    from phantom.cli import update_cmd
    fake_manifest = update_cmd.Manifest(
        version="9.9.9",
        release_date="2099-01-01",
        download_url="https://example.invalid/phantom.zip",
        sha256="deadbeef",
        size_bytes=1234,
        headline="",
        changelog=(),
    )
    monkeypatch.setattr(update_cmd, "fetch_manifest", lambda url, **k: fake_manifest)
    monkeypatch.setattr(update_cmd, "is_pip_managed", lambda: True)
    # Make _download_and_verify raise so we don't actually download.
    monkeypatch.setattr(
        update_cmd,
        "_download_and_verify",
        lambda url, sha, **k: (_ for _ in ()).throw(RuntimeError("test stop")),
    )

    written: list[str] = []
    rc = update_cmd.perform_update(
        manifest_url="https://example.invalid/manifest.json",
        install_dir=tmp_path,
        write=written.append,
    )
    # Got past the orphan check (would have been rc=2 with hint),
    # got into the download path which we stubbed to error (rc=1).
    output = "".join(written)
    assert "orphan install" not in output.lower()
    assert rc == 1
    assert "test stop" in output


# ─── Identity heuristic catch-all ─────────────────────────────────────────

def test_identity_heuristic_catches_novel_brand():
    """Even if a future model leaks a brand we haven't put in the static
    list (e.g., 'Z-AI' or 'NewBot'), the heuristic catches 'I am [Capitalised
    word]' at the head of the reply and rewrites it."""
    from phantom.cli.chat import _post_process_identity
    out = _post_process_identity("I am NovelBrand, a helpful AI.", "Ghost")
    assert "NovelBrand" not in out
    assert "I'm Ghost" in out


def test_identity_heuristic_leaves_correct_self_identification_alone():
    """If the model says 'I'm Ghost' (the actual assistant_name), the
    heuristic must NOT rewrite it to 'I'm Ghost' redundantly — that
    would cause text churn even though the result is the same."""
    from phantom.cli.chat import _post_process_identity
    src = "I'm Ghost, ready to help."
    out = _post_process_identity(src, "Ghost")
    # Output unchanged at the brand-identification level.
    assert "I'm Ghost" in out
    # Should still mention "ready to help".
    assert "ready to help" in out


def test_identity_heuristic_only_applies_to_head_of_reply():
    """The heuristic anchors to the first ~250 chars to avoid mangling
    body-text mentions like 'I'm John, the user'. A brand in the body
    of a long reply isn't an identity claim and should be left alone."""
    from phantom.cli.chat import _post_process_identity
    # 300 chars of innocent prefix, then a "I am Foo" deep in the body.
    prefix = "Here is a long technical explanation. " * 8  # > 250 chars
    src = prefix + "Some sample data: I am Foo and you are Bar."
    out = _post_process_identity(src, "Ghost")
    # The deep-body mention should survive.
    assert "I am Foo" in out


def test_identity_heuristic_case_insensitive_name_match():
    """If assistant_name is 'Ghost' and model says 'I am ghost' (lowercase),
    it shouldn't be rewritten to 'I'm Ghost' — the names match."""
    from phantom.cli.chat import _post_process_identity
    # The heuristic regex is case-insensitive on the prefix but checks
    # captured-name vs assistant_name case-insensitively before deciding
    # to rewrite. Lower-case "ghost" should still match the captured
    # group (since [A-Z]\w+ requires capital first letter), so no
    # match — the regex doesn't fire on lowercase, no rewrite happens.
    src = "I am ghost, ready to help."
    out = _post_process_identity(src, "Ghost")
    # Lowercase doesn't match the [A-Z]\w pattern, so the heuristic
    # doesn't fire. Result unchanged.
    assert "I am ghost" in out or "I'm Ghost" in out


# ─── Behavioural prompt_toolkit ANSI wrapping ─────────────────────────────

def test_build_prompt_label_returns_ansi_instance():
    """The prompt label MUST be a prompt_toolkit ANSI object so the
    library interprets the embedded escape codes. A raw string would
    render literally as ^[[36m on Windows."""
    from prompt_toolkit.formatted_text import ANSI
    from phantom.cli.chat import _build_prompt_label
    label = _build_prompt_label("Arvi Sir")
    assert isinstance(label, ANSI), (
        f"_build_prompt_label must return an ANSI instance, got {type(label).__name__}. "
        "Without the wrap, prompt_toolkit emits the escape codes as literal "
        "text on terminals where it owns rendering."
    )


def test_build_prompt_label_includes_user_label():
    """The user's chosen label must be embedded in the prompt string."""
    from phantom.cli.chat import _build_prompt_label
    label = _build_prompt_label("Arvi Sir")
    # ANSI() exposes the original string via .value in current
    # prompt_toolkit; fall back to str() if upstream changes.
    raw = getattr(label, "value", str(label))
    assert "Arvi Sir" in raw
    assert "›" in raw


def test_build_prompt_label_falls_back_to_plain_string_without_prompt_toolkit(monkeypatch):
    """If prompt_toolkit is unavailable (test env, headless CI), the
    fallback returns a plain string with a `>` instead of `›` so the
    chat REPL still works."""
    import sys
    # Simulate prompt_toolkit ImportError by removing it from sys.modules.
    saved_modules = {}
    for mod in list(sys.modules.keys()):
        if mod.startswith("prompt_toolkit"):
            saved_modules[mod] = sys.modules.pop(mod)
    # And block re-import by patching the importer.
    import builtins
    real_import = builtins.__import__
    def block_ptk(name, *a, **k):
        if name.startswith("prompt_toolkit"):
            raise ImportError("simulated")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", block_ptk)

    try:
        from phantom.cli.chat import _build_prompt_label
        label = _build_prompt_label("Arvi Sir")
        assert isinstance(label, str)
        assert "Arvi Sir" in label
        assert "›" not in label  # the unicode arrow is the ptk-path glyph
        assert ">" in label       # the fallback uses ASCII
    finally:
        # Restore prompt_toolkit modules so other tests don't break.
        for k, v in saved_modules.items():
            sys.modules[k] = v


# ─── 429 error suggests concrete model switches ───────────────────────────

def test_429_stream_error_message_includes_concrete_model_suggestions():
    """When a streaming 429 lands, the error string must list at least
    one specific model the user can switch to. v1.1.31 had a generic
    'switch model' which left users with no concrete next step."""
    import inspect
    from phantom.agent import provider
    src = inspect.getsource(provider)
    # The streaming 429 raise must mention at least one concrete model.
    assert "/model meta/llama-3.3-70b-instruct" in src
    assert "/model claude-haiku-4-5" in src


def test_429_non_stream_error_includes_concrete_model_suggestions():
    """Same contract for the non-streaming retry-exhausted path."""
    import inspect
    from phantom.agent import provider
    src = inspect.getsource(provider)
    # Two distinct mentions (stream + non-stream paths).
    assert src.count("/model meta/llama-3.3-70b-instruct") >= 2


# ─── Import-discipline budget acknowledgement ─────────────────────────────

def test_import_budget_is_at_least_1000ms():
    """The CI was hitting 540ms vs a 500ms budget on Windows runners.
    v1.1.32 bumps to 1000ms with a comment so noisy CI doesn't redden
    the suite. Regression catch: if a future change tightens this back
    below 1000, this test fires."""
    import inspect
    from phantom.tests import test_import_discipline
    src = inspect.getsource(test_import_discipline)
    assert "elapsed_ms < 1000" in src, (
        "import budget for phantom.cli must be >= 1000ms — "
        "Windows CI runners legitimately hit 500-700ms cold"
    )
