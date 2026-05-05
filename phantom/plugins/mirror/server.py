"""Plugin mirror server — FastAPI app + bundle helpers.

Routes
------

* ``GET /index.json``                         — list of available plugins.
* ``GET /plugins/<name>/<version>.tar.gz``    — bundle download.

The server reads from a *staging directory* on disk:

    <staging>/
    ├── index.json
    └── bundles/
        └── <name>-<version>.tar.gz

Operators publish a plugin by running :func:`build_bundle` to produce
the tar.gz, then :func:`add_to_index` to register it in ``index.json``.

Production deployment fronts this with Caddy + TLS; the FastAPI app
binds loopback by default.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

__all__ = [
    "MirrorStore",
    "build_bundle",
    "build_app",
    "compute_sha256",
]

log = logging.getLogger("phantom.plugins.mirror.server")


def compute_sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# ─── bundle building ─────────────────────────────────────────────────────────


def build_bundle(plugin_dir: Path, *, dest_dir: Path) -> tuple[Path, str, dict]:
    """Tar-gzip a plugin directory; return (path, sha256, manifest dict).

    `plugin_dir` must contain a ``manifest.json`` at its root.
    """
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"{plugin_dir} has no manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    name = manifest.get("name")
    version = manifest.get("version")
    if not (isinstance(name, str) and isinstance(version, str)):
        raise ValueError("manifest.json missing name/version")

    dest_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = dest_dir / f"{name}-{version}.tar.gz"

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        # Add every regular file under plugin_dir at the bundle root.
        for f in sorted(plugin_dir.rglob("*")):
            if not f.is_file():
                continue
            arcname = str(f.relative_to(plugin_dir))
            tf.add(f, arcname=arcname, recursive=False)
    body = buf.getvalue()
    bundle_path.write_bytes(body)
    sha = compute_sha256(body)
    return bundle_path, sha, manifest


# ─── on-disk store + index management ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MirrorStore:
    root: Path

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @property
    def bundles_dir(self) -> Path:
        # On-disk layout matches the URL layout so file:// access works
        # without rewriting paths. The HTTP server below mounts the
        # same directory structure.
        return self.root / "plugins"

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.bundles_dir.mkdir(exist_ok=True)
        if not self.index_path.exists():
            empty = json.dumps({"plugins": []}, indent=2)
            self.index_path.write_text(empty, encoding="utf-8")

    def load_index(self) -> dict:
        if not self.index_path.exists():
            return {"plugins": []}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"plugins": []}

    def add_to_index(
        self,
        manifest: dict,
        sha256: str,
        *,
        public_key_b64: str = "",
        signature_b64: str = "",
    ) -> dict:
        """Register a built bundle in the index."""
        self.init()
        index = self.load_index()
        plugins = index.setdefault("plugins", [])
        # Drop any existing entry with the same name+version.
        plugins = [p for p in plugins
                   if not (p.get("name") == manifest["name"]
                           and p.get("version") == manifest["version"])]
        entry = {
            "name": manifest["name"],
            "version": manifest["version"],
            "description": manifest.get("description", ""),
            "bundle_url": f"plugins/{manifest['name']}/{manifest['version']}.tar.gz",
            "sha256": sha256,
            "homepage": manifest.get("homepage", ""),
            "author": manifest.get("author", ""),
        }
        if public_key_b64:
            entry["public_key"] = public_key_b64
        if signature_b64:
            entry["signature"] = signature_b64
        plugins.append(entry)
        index["plugins"] = sorted(plugins, key=lambda p: (p["name"], p["version"]))
        body = json.dumps(index, indent=2, sort_keys=True)
        self.index_path.write_text(body, encoding="utf-8")
        return entry

    def publish(
        self,
        plugin_dir: Path,
        *,
        public_key_b64: str = "",
        signing_key_bytes: bytes = b"",
    ) -> dict:
        """Build a bundle from `plugin_dir`, copy into bundles_dir, register.

        If ``signing_key_bytes`` is supplied, sign sha256(bundle) and
        record the detached signature in the index. The matching
        public_key_b64 is also recorded (derived from the signing key
        if not explicitly passed).
        """
        self.init()
        bundle, sha, manifest = build_bundle(plugin_dir, dest_dir=self.root / "_staging")
        target_dir = self.bundles_dir / manifest["name"]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{manifest['version']}.tar.gz"
        shutil.copyfile(bundle, target)
        bundle.unlink(missing_ok=True)

        signature_b64 = ""
        if signing_key_bytes:
            try:
                import base64 as _b64
                from nacl.signing import SigningKey
            except ImportError as e:
                raise RuntimeError("signing_key_bytes supplied but PyNaCl not installed") from e
            sk = SigningKey(signing_key_bytes)
            sig = sk.sign(hashlib.sha256(target.read_bytes()).digest()).signature
            signature_b64 = _b64.b64encode(sig).decode("ascii")
            if not public_key_b64:
                public_key_b64 = _b64.b64encode(sk.verify_key.encode()).decode("ascii")

        return self.add_to_index(
            manifest, sha,
            public_key_b64=public_key_b64,
            signature_b64=signature_b64,
        )

    def bundle_path(self, name: str, version: str) -> Path:
        return self.bundles_dir / name / f"{version}.tar.gz"


# ─── FastAPI app ─────────────────────────────────────────────────────────────


def build_app(store: MirrorStore):
    """Build a FastAPI app that serves `store`."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title="Phantom plugin mirror", version="1.0.0")

    @app.get("/index.json")
    def get_index() -> JSONResponse:
        return JSONResponse(store.load_index())

    @app.get("/plugins/{name}/{version}.tar.gz")
    def get_bundle(name: str, version: str):
        path = store.bundle_path(name, version)
        if not path.exists():
            raise HTTPException(404, f"no such bundle: {name} {version}")
        return FileResponse(path, media_type="application/gzip")

    @app.get("/healthz")
    def health() -> dict:
        return {"ok": True, "plugins": len(store.load_index().get("plugins", []))}

    return app
