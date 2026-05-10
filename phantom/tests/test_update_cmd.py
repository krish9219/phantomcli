"""Tests for ``phantom update`` (phantom/cli/update_cmd.py)."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from phantom.cli.update_cmd import (
    Manifest,
    _extract_to,
    compare_versions,
    fetch_manifest,
    perform_update,
)


# ─── compare_versions ────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,expected", [
    ("1.1.3", "1.1.3", 0),
    ("1.1.3", "1.1.4", -1),
    ("1.1.4", "1.1.3", 1),
    ("1.2.0", "1.1.99", 1),
    ("2.0.0", "1.99.99", 1),
    ("1.0.0", "1.0", 0),  # missing trailing parts treated as 0
    ("1.0", "1.0.0", 0),
    ("1.1.3-dev", "1.1.3", 0),  # pre-release suffix stripped
])
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


# ─── fetch_manifest ──────────────────────────────────────────────────────────

def _stub_urlopen(body: bytes):
    """Build a context manager that stubs urlopen to return *body*.

    The real ``urllib.request`` returns the full body on a no-arg ``read()``,
    or chunks (and finally ``b""``) on ``read(n)``. We mirror both so the
    chunked download loop in ``_download_and_verify`` actually terminates.
    """
    class _Resp:
        headers = {"Content-Length": str(len(body))}
        def __init__(self):
            self._buf = io.BytesIO(body)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1):
            return self._buf.read(n if n is not None else -1)
    return lambda req, timeout=15.0: _Resp()


def test_fetch_manifest_parses_full_payload():
    payload = json.dumps({
        "version": "1.1.4",
        "releaseDate": "2026-05-09",
        "downloadUrl": "https://example.com/x.zip",
        "sha256": "deadbeef",
        "size_bytes": 1234,
        "headline": "hi",
        "changelog": ["a", "b"],
    }).encode()
    with patch("phantom.cli.update_cmd.urlopen", _stub_urlopen(payload)):
        m = fetch_manifest("https://example.com/version.json")
    assert m.version == "1.1.4"
    assert m.sha256 == "deadbeef"
    assert m.changelog == ("a", "b")


def test_fetch_manifest_rejects_invalid_json():
    with patch("phantom.cli.update_cmd.urlopen", _stub_urlopen(b"not json {")):
        with pytest.raises(RuntimeError, match="not valid JSON"):
            fetch_manifest("https://example.com/v.json")


def test_fetch_manifest_rejects_missing_required_field():
    payload = json.dumps({"version": "1.0.0"}).encode()  # no downloadUrl
    with patch("phantom.cli.update_cmd.urlopen", _stub_urlopen(payload)):
        with pytest.raises(RuntimeError, match="missing required field"):
            fetch_manifest("https://example.com/v.json")


# ─── _extract_to ─────────────────────────────────────────────────────────────

def _build_test_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_extract_overwrites_existing_files(tmp_path: Path):
    install = tmp_path / "install"
    install.mkdir()
    (install / "phantom").mkdir()
    (install / "phantom" / "old.py").write_bytes(b"old")
    zip_bytes = _build_test_zip({"phantom/old.py": b"new", "phantom/added.py": b"added"})
    count = _extract_to(install, zip_bytes)
    assert count == 2
    assert (install / "phantom" / "old.py").read_bytes() == b"new"
    assert (install / "phantom" / "added.py").read_bytes() == b"added"


def test_extract_preserves_user_data(tmp_path: Path):
    """User files in install dir root must survive an extract."""
    install = tmp_path / "install"
    install.mkdir()
    (install / ".license").write_bytes(b"secret")
    (install / "memory.db").write_bytes(b"db")
    (install / "providers.json").write_bytes(b'{"default":"x"}')
    zip_bytes = _build_test_zip({"phantom/__init__.py": b"new"})
    _extract_to(install, zip_bytes)
    assert (install / ".license").read_bytes() == b"secret"
    assert (install / "memory.db").read_bytes() == b"db"
    assert (install / "providers.json").read_bytes() == b'{"default":"x"}'


def test_extract_refuses_path_traversal(tmp_path: Path):
    install = tmp_path / "install"
    install.mkdir()
    bad_zip = _build_test_zip({"../../etc/escape": b"pwn"})
    with pytest.raises(RuntimeError, match="unsafe zip entry"):
        _extract_to(install, bad_zip)


def test_extract_refuses_missing_install_dir(tmp_path: Path):
    install = tmp_path / "nope"
    with pytest.raises(RuntimeError, match="install dir not found"):
        _extract_to(install, _build_test_zip({"x.py": b""}))


# ─── perform_update end-to-end ───────────────────────────────────────────────

def test_perform_update_no_op_when_current_matches(tmp_path: Path):
    """Latest == current → exit 0, no files touched."""
    from phantom._version import __version__
    payload = json.dumps({
        "version": __version__,
        "releaseDate": "2026-05-09",
        "downloadUrl": "https://example.com/x.zip",
        "sha256": "0" * 64,
    }).encode()
    install = tmp_path / "install"
    install.mkdir()
    (install / "marker").write_bytes(b"untouched")
    output: list[str] = []
    with patch("phantom.cli.update_cmd.urlopen", _stub_urlopen(payload)):
        rc = perform_update(
            manifest_url="https://example.com/v.json",
            install_dir=install,
            write=output.append,
        )
    assert rc == 0
    assert "already up to date" in "".join(output)
    assert (install / "marker").read_bytes() == b"untouched"


def test_perform_update_downloads_and_extracts_when_newer(tmp_path: Path, monkeypatch):
    # v1.1.32: orphan-install detection bails before download. The
    # tests in this file want to exercise the download path, so stub
    # the check to True (pretend pip-managed).
    monkeypatch.setattr("phantom.cli.update_cmd.is_pip_managed", lambda: True)
    install = tmp_path / "install"
    install.mkdir()
    zip_bytes = _build_test_zip({
        "phantom/_version.py": b'__version__ = "9.9.9"\n',
        "phantom/cli/__init__.py": b'',
    })
    sha = hashlib.sha256(zip_bytes).hexdigest()
    payload = json.dumps({
        "version": "9.9.9",
        "releaseDate": "2099-01-01",
        "downloadUrl": "https://example.com/phantomcli-source.zip",
        "sha256": sha,
        "size_bytes": len(zip_bytes),
        "headline": "test",
    }).encode()

    def fake_urlopen(req, timeout=60.0):
        body = payload if req.full_url.endswith("v.json") else zip_bytes
        return _stub_urlopen(body)(req, timeout=timeout)

    output: list[str] = []
    with patch("phantom.cli.update_cmd.urlopen", fake_urlopen):
        rc = perform_update(
            manifest_url="https://example.com/v.json",
            install_dir=install,
            write=output.append,
        )
    assert rc == 0
    assert (install / "phantom" / "_version.py").read_bytes().startswith(b'__version__ = "9.9.9"')


def test_perform_update_refuses_sha_mismatch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("phantom.cli.update_cmd.is_pip_managed", lambda: True)
    install = tmp_path / "install"
    install.mkdir()
    zip_bytes = _build_test_zip({"phantom/_version.py": b"x"})
    payload = json.dumps({
        "version": "9.9.9",
        "releaseDate": "x",
        "downloadUrl": "https://example.com/phantomcli-source.zip",
        "sha256": "wrong" * 12 + "abcd",
        "size_bytes": len(zip_bytes),
    }).encode()

    def fake_urlopen(req, timeout=60.0):
        body = payload if req.full_url.endswith("v.json") else zip_bytes
        return _stub_urlopen(body)(req, timeout=timeout)

    output: list[str] = []
    with patch("phantom.cli.update_cmd.urlopen", fake_urlopen):
        rc = perform_update(
            manifest_url="https://example.com/v.json",
            install_dir=install,
            write=output.append,
        )
    assert rc == 1
    assert "sha256 mismatch" in "".join(output)


def test_perform_update_force_reinstalls_same_version(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("phantom.cli.update_cmd.is_pip_managed", lambda: True)
    from phantom._version import __version__
    install = tmp_path / "install"
    install.mkdir()
    zip_bytes = _build_test_zip({"phantom/marker.txt": b"forced"})
    sha = hashlib.sha256(zip_bytes).hexdigest()
    payload = json.dumps({
        "version": __version__,
        "releaseDate": "x",
        "downloadUrl": "https://example.com/phantomcli-source.zip",
        "sha256": sha,
        "size_bytes": len(zip_bytes),
    }).encode()

    def fake_urlopen(req, timeout=60.0):
        body = payload if req.full_url.endswith("v.json") else zip_bytes
        return _stub_urlopen(body)(req, timeout=timeout)

    output: list[str] = []
    with patch("phantom.cli.update_cmd.urlopen", fake_urlopen):
        rc = perform_update(
            manifest_url="https://example.com/v.json",
            install_dir=install,
            force=True,
            write=output.append,
        )
    assert rc == 0
    assert (install / "phantom" / "marker.txt").read_bytes() == b"forced"
