"""Tests for v1.1.33 — fix the hidden ``/update`` regression that
shipped silently in v1.1.29 through v1.1.32.

The user's symptom (transcript on 2026-05-10):

    PS C:\\Users\\aravi> phantom update
      current: 1.1.31
      latest:  1.1.32
      installed 601 files.
      updated to v1.1.32.
    PS C:\\Users\\aravi> phantom chat
      Ghost v1.1.31  ←  ←  ←  STILL 1.1.31

Root cause: v1.1.29-v1.1.32 source zips were built with a
``phantomcli-source/`` wrapper directory at the root. ``_extract_to``
copied each top-level entry onto the install dir, so the package
files landed at ``site-packages/phantomcli-source/phantom/`` instead
of ``site-packages/phantom/``. ``601 files`` were copied, just to a
place Python never imports from.

v1.1.33 makes the extract logic tolerate either layout via
``_detect_source_root`` so future zip-build mistakes can't break
``/update`` for users.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


def _build_zip(files: dict[str, bytes]) -> bytes:
    """Build an in-memory zip from a dict of path → bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


# ─── _detect_source_root ──────────────────────────────────────────────────

def test_detect_source_root_returns_tmp_path_when_phantom_at_root(tmp_path: Path):
    """Canonical layout: phantom/ at the root of the temp extract."""
    (tmp_path / "phantom").mkdir()
    (tmp_path / "omnicli").mkdir()
    (tmp_path / "phantom" / "_version.py").write_text('__version__ = "9.9.9"\n')
    from phantom.cli.update_cmd import _detect_source_root
    root = _detect_source_root(tmp_path)
    assert root == tmp_path


def test_detect_source_root_pivots_into_wrapper_dir(tmp_path: Path):
    """Wrapper case: phantomcli-source/phantom/... — _detect_source_root
    must return the wrapper as the new source root so the merge writes
    to the correct install-dir paths."""
    wrapper = tmp_path / "phantomcli-source"
    wrapper.mkdir()
    (wrapper / "phantom").mkdir()
    (wrapper / "phantom" / "_version.py").write_text('__version__ = "9.9.9"\n')
    (wrapper / "omnicli").mkdir()
    from phantom.cli.update_cmd import _detect_source_root
    root = _detect_source_root(tmp_path)
    assert root == wrapper


def test_detect_source_root_pivots_for_omnicli_only_wrapper(tmp_path: Path):
    """An omnicli-only zip (legacy v3 surface) is also a valid signal."""
    wrapper = tmp_path / "release-bundle"
    wrapper.mkdir()
    (wrapper / "omnicli").mkdir()
    (wrapper / "omnicli" / "__init__.py").write_text('__version__ = "9.9.9"\n')
    from phantom.cli.update_cmd import _detect_source_root
    root = _detect_source_root(tmp_path)
    assert root == wrapper


def test_detect_source_root_falls_back_when_layout_unrecognised(tmp_path: Path):
    """No phantom/, no omnicli/, multiple top-level entries — return
    tmp_path unchanged and let the merge proceed (best-effort)."""
    (tmp_path / "random_dir_a").mkdir()
    (tmp_path / "random_dir_b").mkdir()
    (tmp_path / "stray_file.txt").write_text("hi")
    from phantom.cli.update_cmd import _detect_source_root
    root = _detect_source_root(tmp_path)
    assert root == tmp_path


def test_detect_source_root_does_not_pivot_when_wrapper_is_empty(tmp_path: Path):
    """A single wrapper with no phantom/ inside isn't recognised — could
    be user data we'd corrupt by treating as a source root."""
    wrapper = tmp_path / "user_files"
    wrapper.mkdir()
    (wrapper / "memory.db").write_bytes(b"")
    from phantom.cli.update_cmd import _detect_source_root
    root = _detect_source_root(tmp_path)
    assert root == tmp_path


# ─── _extract_to with wrapper directory (the live regression) ─────────────

def test_extract_to_with_wrapper_dir_writes_to_install_phantom(tmp_path: Path):
    """The exact failure mode the user hit: zip has phantomcli-source/
    wrapper. After v1.1.33's fix, _extract_to must still write to
    install/phantom/_version.py — NOT install/phantomcli-source/phantom/."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "phantom").mkdir()
    (install / "phantom" / "_version.py").write_text('__version__ = "1.1.31"\n')

    zip_bytes = _build_zip({
        "phantomcli-source/phantom/_version.py": b'__version__ = "1.1.33"\n',
        "phantomcli-source/phantom/__init__.py": b"",
        "phantomcli-source/omnicli/__init__.py": b"",
    })

    from phantom.cli.update_cmd import _extract_to
    _extract_to(install, zip_bytes)

    # The actual install/phantom/_version.py must now contain the new
    # version. If the wrapper-pivot logic broke, this would still be
    # 1.1.31 and the regression would re-surface.
    actual = (install / "phantom" / "_version.py").read_text()
    assert "1.1.33" in actual, (
        f"_version.py content: {actual!r} — wrapper-dir pivot is broken; "
        "/update would silently no-op as in the user-reported v1.1.32 bug"
    )

    # And the wrapper directory must NOT have been created in the install
    # dir (would be wasted bytes pointing nowhere).
    assert not (install / "phantomcli-source").exists(), (
        "wrapper directory leaked into install dir — the pivot logic "
        "should have stripped it"
    )


def test_extract_to_with_flat_layout_still_works(tmp_path: Path):
    """The canonical layout (phantom/ at root) keeps working."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "phantom").mkdir()
    (install / "phantom" / "_version.py").write_text('__version__ = "1.1.31"\n')

    zip_bytes = _build_zip({
        "phantom/_version.py": b'__version__ = "1.1.33"\n',
        "phantom/__init__.py": b"",
    })

    from phantom.cli.update_cmd import _extract_to
    _extract_to(install, zip_bytes)

    actual = (install / "phantom" / "_version.py").read_text()
    assert "1.1.33" in actual


def test_extract_to_preserves_user_data_with_wrapper_pivot(tmp_path: Path):
    """User data in the install dir (memory.db, .license, etc.) must
    survive a wrapper-pivot extract — the merge_tree contract is unchanged."""
    install = tmp_path / "install"
    install.mkdir()
    (install / "memory.db").write_bytes(b"my memories")
    (install / "phantom").mkdir()
    (install / "phantom" / "_version.py").write_text('__version__ = "1.1.31"\n')

    zip_bytes = _build_zip({
        "phantomcli-source/phantom/_version.py": b'__version__ = "1.1.33"\n',
    })

    from phantom.cli.update_cmd import _extract_to
    _extract_to(install, zip_bytes)

    # Code updated.
    assert "1.1.33" in (install / "phantom" / "_version.py").read_text()
    # User data preserved.
    assert (install / "memory.db").read_bytes() == b"my memories"


# ─── Build-script contract: zip MUST have phantom/ at root ───────────────

def test_repo_release_zip_contract_documented():
    """If you're shipping a new release, the zip must have phantom/ at
    the root — NOT a wrapper dir. v1.1.29-v1.1.32 shipped wrapper zips
    that silently broke /update for everyone. This test fails if the
    contract documentation in update_cmd.py disappears.

    Build command (canonical):
        cd <source_dir> && zip -rq /tmp/phantomcli-source-vX.Y.Z.zip . -x "*.zip"
    NOT:
        cd <parent> && zip -rq ... source_dir/   (← creates wrapper)
    """
    import inspect
    from phantom.cli import update_cmd
    src = inspect.getsource(update_cmd)
    # The wrapper-pivot logic must be present — regression net.
    assert "_detect_source_root" in src
    assert "phantomcli-source" in src, (
        "the comment explaining the v1.1.29-v1.1.32 zip-build mistake "
        "must remain so future build-script edits don't repeat the bug"
    )
