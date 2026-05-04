"""HTTP client for the plugin mirror.

Pure stdlib — no `requests`, no `httpx`. The mirror serves small JSON
and small tarballs; ``urllib`` is plenty.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

__all__ = [
    "DEFAULT_MIRROR_URL",
    "MirrorClient",
    "MirrorError",
    "PluginEntry",
    "PluginIndex",
]


DEFAULT_MIRROR_URL: str = "https://phantom.aravindlabs.tech/plugins"


class MirrorError(RuntimeError):
    """Any failure resolving, fetching, verifying, or installing a bundle."""


@dataclass(frozen=True, slots=True)
class PluginEntry:
    name: str
    version: str
    description: str = ""
    bundle_url: str = ""
    sha256: str = ""
    public_key: str = ""        # base64 ed25519 verify-key, optional
    signature: str = ""         # base64 ed25519 signature over sha256(bundle), optional
    homepage: str = ""
    author: str = ""


@dataclass(frozen=True, slots=True)
class PluginIndex:
    plugins: tuple[PluginEntry, ...] = field(default_factory=tuple)
    fetched_from: str = ""

    def by_name(self, name: str) -> list[PluginEntry]:
        return sorted(
            (p for p in self.plugins if p.name == name),
            key=lambda p: _semver_key(p.version),
            reverse=True,
        )

    def search(self, query: str) -> list[PluginEntry]:
        q = query.lower().strip()
        if not q:
            return list(self.plugins)
        out = [
            p for p in self.plugins
            if q in p.name.lower() or q in p.description.lower()
        ]
        return sorted(out, key=lambda p: p.name)


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _semver_key(v: str) -> tuple[int, int, int]:
    m = _SEMVER_RE.match(v)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


# ─── client ──────────────────────────────────────────────────────────────────


class MirrorClient:
    """Fetch the index, download bundles, install plugins."""

    def __init__(
        self,
        url: str = DEFAULT_MIRROR_URL,
        *,
        timeout_s: float = 15.0,
        install_root: Optional[Path] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.install_root = install_root or _user_plugins_dir()

    # ── network helpers ──────────────────────────────────────────────

    def _get(self, path: str) -> bytes:
        if path.startswith(("http://", "https://", "file://")):
            full = path
        else:
            full = f"{self.url}/{path.lstrip('/')}"
        try:
            with urllib.request.urlopen(full, timeout=self.timeout_s) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            raise MirrorError(f"HTTP {e.code} fetching {full}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise MirrorError(f"network error fetching {full}: {e.reason}") from e

    # ── index + search ───────────────────────────────────────────────

    def index(self) -> PluginIndex:
        raw = self._get("index.json")
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise MirrorError(f"index.json is not JSON: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
            raise MirrorError("index.json missing 'plugins' array")
        entries: list[PluginEntry] = []
        for raw_p in data["plugins"]:
            if not isinstance(raw_p, dict):
                continue
            try:
                entries.append(PluginEntry(
                    name=str(raw_p["name"]),
                    version=str(raw_p["version"]),
                    description=str(raw_p.get("description", "")),
                    bundle_url=str(raw_p.get("bundle_url", "")),
                    sha256=str(raw_p.get("sha256", "")),
                    public_key=str(raw_p.get("public_key", "")),
                    signature=str(raw_p.get("signature", "")),
                    homepage=str(raw_p.get("homepage", "")),
                    author=str(raw_p.get("author", "")),
                ))
            except KeyError:
                continue
        return PluginIndex(plugins=tuple(entries), fetched_from=self.url)

    def resolve(self, name: str, version: Optional[str] = None) -> PluginEntry:
        candidates = self.index().by_name(name)
        if not candidates:
            raise MirrorError(f"no plugin named {name!r} in index {self.url}")
        if version is None:
            return candidates[0]
        for entry in candidates:
            if entry.version == version:
                return entry
        raise MirrorError(
            f"plugin {name!r} found, but version {version!r} is not in index "
            f"(available: {', '.join(c.version for c in candidates)})"
        )

    # ── install / uninstall ──────────────────────────────────────────

    def install(
        self,
        name: str,
        *,
        version: Optional[str] = None,
        require_signature: bool = False,
        force: bool = False,
    ) -> Path:
        """Download, verify, and install a plugin. Returns the install dir."""
        entry = self.resolve(name, version)
        target = self.install_root / entry.name
        if target.exists() and not force:
            raise MirrorError(
                f"{name!r} already installed at {target}. Pass force=True to overwrite."
            )

        bundle_url = entry.bundle_url or f"plugins/{entry.name}/{entry.version}.tar.gz"
        body = self._get(bundle_url)

        # SHA-256 verification — required by mirror policy.
        digest = hashlib.sha256(body).hexdigest()
        if entry.sha256 and digest != entry.sha256.lower():
            raise MirrorError(
                f"sha256 mismatch for {entry.name} {entry.version}: "
                f"expected {entry.sha256}, got {digest}"
            )

        # Optional signature verification (Ed25519 over the SHA-256 digest).
        if require_signature and not entry.public_key:
            raise MirrorError(f"plugin {name!r} has no public_key in index but require_signature=True")

        with tempfile.TemporaryDirectory(prefix="phantom-mirror-") as td:
            staging = Path(td) / "stage"
            staging.mkdir()
            try:
                with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tf:
                    _safe_extract(tf, staging)
            except tarfile.TarError as e:
                raise MirrorError(f"bundle is not a valid tar.gz: {e}") from e

            manifest_path = staging / "manifest.json"
            if not manifest_path.exists():
                raise MirrorError("bundle is missing manifest.json at root")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise MirrorError(f"manifest.json is not JSON: {e}") from e

            if manifest.get("name") != entry.name:
                raise MirrorError(
                    f"manifest name {manifest.get('name')!r} != index name {entry.name!r}"
                )

            if require_signature:
                _verify_detached_signature(body, entry.public_key, entry.signature)

            # Atomic-ish install: rmtree + rename. The window between
            # rmtree and rename is tiny; we accept it for simplicity.
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging), str(target))

        return target

    def uninstall(self, name: str) -> bool:
        target = self.install_root / name
        if not target.exists():
            return False
        shutil.rmtree(target)
        return True


# ─── safe extraction (CVE-2007-4559 mitigation) ──────────────────────────────


def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Tar-slip-safe extraction. Reject any member that escapes dest."""
    dest_resolved = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError:
            raise MirrorError(f"refusing path-traversal in tarball: {member.name!r}")
        if member.issym() or member.islnk():
            link_target = (dest / member.name).parent / member.linkname
            try:
                link_target.resolve().relative_to(dest_resolved)
            except ValueError:
                raise MirrorError(f"refusing symlink escape in tarball: {member.name!r} -> {member.linkname!r}")
    # Python 3.12+ ships `filter='data'` for safe extraction; we already
    # validated, but hand it the filter anyway as belt-and-braces.
    try:
        tf.extractall(dest, filter="data")
    except TypeError:  # pragma: no cover — older Python
        tf.extractall(dest)


def _verify_detached_signature(bundle_bytes: bytes, public_key_b64: str, signature_b64: str) -> None:
    """Verify Ed25519 signature on the bundle's SHA-256.

    The signature is *detached* — it lives in the index entry, not in
    the bundle. This means signing a bundle doesn't change its SHA-256,
    avoiding the chicken-and-egg problem of "the signature changes the
    bundle which changes the signature." The public_key + signature both
    come from the index entry; operators trust the index transport to
    the same extent they trust their TLS chain.
    """
    if not signature_b64:
        raise MirrorError("require_signature=True but index has no 'signature' field")
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError as e:
        raise MirrorError("require_signature=True needs PyNaCl installed") from e
    try:
        vk = VerifyKey(base64.b64decode(public_key_b64, validate=True))
    except ValueError as e:
        raise MirrorError(f"public_key is not valid base64: {e}") from e
    try:
        sig_bytes = base64.b64decode(signature_b64, validate=True)
    except ValueError as e:
        raise MirrorError(f"signature is not valid base64: {e}") from e
    digest = hashlib.sha256(bundle_bytes).digest()
    try:
        vk.verify(digest, sig_bytes)
    except BadSignatureError as e:
        raise MirrorError("bundle signature invalid") from e


def _user_plugins_dir() -> Path:
    base = Path(os.environ.get("PHANTOM_HOME") or os.path.expanduser("~/.phantom"))
    p = base / "plugins"
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    return p
