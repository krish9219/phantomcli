"""End-to-end tests for the plugin mirror — server + client.

We use Starlette's in-process TestClient so the tests run on a single
event loop with no real ports bound. The bundle build path (tarball
synthesis, SHA-256, manifest) is exercised end-to-end. The signed-bundle
path is only exercised when PyNaCl is installed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from phantom.plugins.mirror import MirrorClient, MirrorError
from phantom.plugins.mirror.server import (
    MirrorStore,
    build_app,
    build_bundle,
    compute_sha256,
)


# ─── helpers ────────────────────────────────────────────────────────────────


def _make_plugin_dir(root: Path, name: str, version: str = "1.0.0", *, body: str = "") -> Path:
    p = root / name
    p.mkdir(parents=True)
    (p / "manifest.json").write_text(json.dumps({
        "name": name,
        "version": version,
        "description": f"test plugin {name}",
        "entry_point": f"phantom.plugins.builtin.clock:ClockPlugin",
        "capabilities": [],
        "author": "test",
    }))
    (p / "__init__.py").write_text(body or f"# {name}\n")
    return p


@pytest.fixture
def store(tmp_path: Path) -> MirrorStore:
    s = MirrorStore(tmp_path / "mirror")
    s.init()
    return s


@pytest.fixture
def populated_store(tmp_path: Path, store: MirrorStore) -> MirrorStore:
    src = tmp_path / "src"
    p = _make_plugin_dir(src, "alpha", "1.0.0")
    store.publish(p)
    p2 = _make_plugin_dir(src, "beta", "0.5.0")
    store.publish(p2)
    p3_dir = tmp_path / "src2" / "alpha"
    p3_dir.parent.mkdir()
    p3_dir.mkdir()
    (p3_dir / "manifest.json").write_text(json.dumps({
        "name": "alpha", "version": "2.0.0", "entry_point": "x:y", "description": "v2",
    }))
    (p3_dir / "__init__.py").write_text("# alpha v2\n")
    store.publish(p3_dir)
    return store


# ─── bundle building ────────────────────────────────────────────────────────


def test_build_bundle_emits_tar_gz_with_manifest(tmp_path: Path):
    src = _make_plugin_dir(tmp_path / "src", "test-plugin")
    out_dir = tmp_path / "out"
    bundle, sha, manifest = build_bundle(src, dest_dir=out_dir)
    assert bundle.exists()
    assert bundle.suffix == ".gz"
    assert manifest["name"] == "test-plugin"
    # SHA matches what compute_sha256 says about the file bytes
    assert sha == compute_sha256(bundle.read_bytes())
    # Tarball roundtrip
    with tarfile.open(bundle, mode="r:gz") as tf:
        names = sorted(m.name for m in tf.getmembers())
        assert "manifest.json" in names
        assert "__init__.py" in names


def test_build_bundle_rejects_missing_manifest(tmp_path: Path):
    src = tmp_path / "no-manifest"
    src.mkdir()
    (src / "__init__.py").write_text("")
    with pytest.raises(ValueError, match="manifest"):
        build_bundle(src, dest_dir=tmp_path / "out")


# ─── store + index management ───────────────────────────────────────────────


def test_publish_creates_index_entry(populated_store: MirrorStore):
    idx = populated_store.load_index()
    plugins = idx["plugins"]
    names_versions = {(p["name"], p["version"]) for p in plugins}
    assert ("alpha", "1.0.0") in names_versions
    assert ("alpha", "2.0.0") in names_versions
    assert ("beta", "0.5.0") in names_versions


def test_publish_replaces_same_version(tmp_path: Path, store: MirrorStore):
    src = _make_plugin_dir(tmp_path / "v1", "x", "1.0.0", body="# old\n")
    store.publish(src)
    src2 = _make_plugin_dir(tmp_path / "v1b", "x", "1.0.0", body="# new\n")
    store.publish(src2)
    plugins = store.load_index()["plugins"]
    assert sum(1 for p in plugins if p["name"] == "x" and p["version"] == "1.0.0") == 1


# ─── HTTP server (in-process TestClient) ────────────────────────────────────


def test_server_serves_index_and_bundle(populated_store: MirrorStore):
    starlette = pytest.importorskip("starlette")  # noqa: F841
    from fastapi.testclient import TestClient
    app = build_app(populated_store)
    client = TestClient(app)
    r = client.get("/index.json")
    assert r.status_code == 200
    body = r.json()
    assert any(p["name"] == "alpha" for p in body["plugins"])

    r = client.get("/plugins/alpha/1.0.0.tar.gz")
    assert r.status_code == 200
    # body is a real tar.gz
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tf:
        assert "manifest.json" in tf.getnames()


def test_server_404_on_unknown_bundle(populated_store: MirrorStore):
    pytest.importorskip("starlette")
    from fastapi.testclient import TestClient
    client = TestClient(build_app(populated_store))
    r = client.get("/plugins/nope/9.9.9.tar.gz")
    assert r.status_code == 404


# ─── client → mirror end-to-end (file:// transport) ─────────────────────────


def test_client_index_via_file_url(tmp_path: Path, populated_store: MirrorStore):
    url = (populated_store.root).as_uri()  # file:// URL
    client = MirrorClient(url=url)
    idx = client.index()
    names = {p.name for p in idx.plugins}
    assert {"alpha", "beta"}.issubset(names)


def test_client_resolve_picks_latest(tmp_path: Path, populated_store: MirrorStore):
    client = MirrorClient(url=populated_store.root.as_uri())
    entry = client.resolve("alpha")
    assert entry.version == "2.0.0"


def test_client_resolve_pinned_version(populated_store: MirrorStore):
    client = MirrorClient(url=populated_store.root.as_uri())
    entry = client.resolve("alpha", version="1.0.0")
    assert entry.version == "1.0.0"


def test_client_resolve_missing_version_errors(populated_store: MirrorStore):
    client = MirrorClient(url=populated_store.root.as_uri())
    with pytest.raises(MirrorError, match="version"):
        client.resolve("alpha", version="9.9.9")


def test_client_install_extracts_to_target(tmp_path: Path, populated_store: MirrorStore):
    install_root = tmp_path / "installs"
    install_root.mkdir()
    client = MirrorClient(url=populated_store.root.as_uri(), install_root=install_root)
    target = client.install("alpha", version="1.0.0")
    assert target == install_root / "alpha"
    assert (target / "manifest.json").exists()
    body = json.loads((target / "manifest.json").read_text())
    assert body["name"] == "alpha"
    assert body["version"] == "1.0.0"


def test_client_install_refuses_overwrite_unless_forced(tmp_path: Path, populated_store: MirrorStore):
    install_root = tmp_path / "installs"
    install_root.mkdir()
    client = MirrorClient(url=populated_store.root.as_uri(), install_root=install_root)
    client.install("alpha", version="1.0.0")
    with pytest.raises(MirrorError, match="already installed"):
        client.install("alpha", version="2.0.0")
    # force overwrites
    target = client.install("alpha", version="2.0.0", force=True)
    body = json.loads((target / "manifest.json").read_text())
    assert body["version"] == "2.0.0"


def test_client_install_detects_sha_mismatch(tmp_path: Path, populated_store: MirrorStore):
    """Tamper with the index sha and confirm the client refuses."""
    idx = populated_store.load_index()
    for p in idx["plugins"]:
        if p["name"] == "alpha" and p["version"] == "1.0.0":
            p["sha256"] = "0" * 64  # bogus
    populated_store.index_path.write_text(json.dumps(idx))
    install_root = tmp_path / "installs"
    install_root.mkdir()
    client = MirrorClient(url=populated_store.root.as_uri(), install_root=install_root)
    with pytest.raises(MirrorError, match="sha256 mismatch"):
        client.install("alpha", version="1.0.0")


def test_client_uninstall(tmp_path: Path, populated_store: MirrorStore):
    install_root = tmp_path / "installs"
    install_root.mkdir()
    client = MirrorClient(url=populated_store.root.as_uri(), install_root=install_root)
    client.install("alpha", version="1.0.0")
    assert client.uninstall("alpha") is True
    assert client.uninstall("alpha") is False


def test_client_search(populated_store: MirrorStore):
    client = MirrorClient(url=populated_store.root.as_uri())
    idx = client.index()
    matches = idx.search("beta")
    assert len(matches) == 1
    assert matches[0].name == "beta"


def test_client_search_empty_returns_all(populated_store: MirrorStore):
    client = MirrorClient(url=populated_store.root.as_uri())
    idx = client.index()
    assert len(idx.search("")) >= 3


# ─── safe extraction (CVE-2007-4559) ────────────────────────────────────────


def test_safe_extract_blocks_path_traversal(tmp_path: Path, store: MirrorStore):
    # Hand-build an evil tarball with a member that escapes via ../
    install_root = tmp_path / "installs"
    install_root.mkdir()
    bad = io.BytesIO()
    with tarfile.open(fileobj=bad, mode="w:gz") as tf:
        evil_body = b"PWNED"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(evil_body)
        tf.addfile(info, io.BytesIO(evil_body))
        # also include a manifest so the install gets past the "missing
        # manifest" check before hitting the safe-extract guard.
        manifest = json.dumps({
            "name": "evil", "version": "1.0.0",
            "entry_point": "x:y", "description": "evil",
        }).encode()
        m_info = tarfile.TarInfo(name="manifest.json")
        m_info.size = len(manifest)
        tf.addfile(m_info, io.BytesIO(manifest))
    body = bad.getvalue()

    # Register the evil bundle in our local store
    store.bundles_dir.mkdir(exist_ok=True)
    (store.bundles_dir / "evil").mkdir(exist_ok=True)
    (store.bundles_dir / "evil" / "1.0.0.tar.gz").write_bytes(body)
    idx = store.load_index()
    idx.setdefault("plugins", []).append({
        "name": "evil", "version": "1.0.0",
        "bundle_url": "plugins/evil/1.0.0.tar.gz",
        "sha256": hashlib.sha256(body).hexdigest(),
    })
    store.index_path.write_text(json.dumps(idx))

    client = MirrorClient(url=store.root.as_uri(), install_root=install_root)
    with pytest.raises(MirrorError, match="path-traversal"):
        client.install("evil", version="1.0.0")


# ─── signed bundles (PyNaCl, optional) ──────────────────────────────────────


def test_signed_install_succeeds_with_correct_key(tmp_path: Path):
    pytest.importorskip("nacl.signing")
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    src = _make_plugin_dir(tmp_path / "src", "signed-pl", "1.0.0")

    store = MirrorStore(tmp_path / "mirror")
    entry = store.publish(src, signing_key_bytes=bytes(sk))
    assert entry.get("signature")
    assert entry.get("public_key")

    install_root = tmp_path / "installs"
    install_root.mkdir()
    client = MirrorClient(url=store.root.as_uri(), install_root=install_root)
    target = client.install("signed-pl", require_signature=True)
    assert (target / "manifest.json").exists()


def test_signed_install_rejects_tampered_bundle(tmp_path: Path):
    pytest.importorskip("nacl.signing")
    from nacl.signing import SigningKey

    sk = SigningKey.generate()
    src = _make_plugin_dir(tmp_path / "src", "tampered", "1.0.0")
    store = MirrorStore(tmp_path / "mirror")
    entry = store.publish(src, signing_key_bytes=bytes(sk))

    # Tamper with the bundle on disk after publishing
    bundle_path = store.bundles_dir / "tampered" / "1.0.0.tar.gz"
    body = bundle_path.read_bytes()
    bundle_path.write_bytes(body + b"\x00")
    # Update sha so the sha-check passes but signature still fails
    new_sha = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    idx = store.load_index()
    for p in idx["plugins"]:
        if p["name"] == "tampered":
            p["sha256"] = new_sha
    store.index_path.write_text(json.dumps(idx))

    install_root = tmp_path / "installs"
    install_root.mkdir()
    client = MirrorClient(url=store.root.as_uri(), install_root=install_root)
    with pytest.raises(MirrorError, match="signature invalid"):
        client.install("tampered", require_signature=True)


def test_signed_install_fails_without_signature_file(tmp_path: Path, populated_store: MirrorStore):
    pytest.importorskip("nacl.signing")
    install_root = tmp_path / "installs"
    install_root.mkdir()
    # alpha was published without a signature file
    client = MirrorClient(url=populated_store.root.as_uri(), install_root=install_root)
    with pytest.raises(MirrorError):
        client.install("alpha", version="1.0.0", require_signature=True)
