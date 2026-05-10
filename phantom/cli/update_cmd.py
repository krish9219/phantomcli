"""``phantom update`` — self-update from the official manifest.

Reads ``version.json`` over HTTPS, compares with ``phantom.__version__``,
and if newer downloads + verifies + extracts the source zip on top of
the install dir. User data (memory.db, providers.json, .license,
.machine_key, oauth/, .repl_history) lives in the install dir root
*outside* the package directories shipped in the zip, so the extract
preserves it.

Exit codes:

* 0 — already current (or update succeeded)
* 1 — network / manifest / extract failure
* 2 — sha mismatch, install dir not writable, or refused
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen

import typer

from phantom._version import __version__

__all__ = [
    "DEFAULT_MANIFEST_URL",
    "Manifest",
    "compare_versions",
    "fetch_manifest",
    "find_install_dir",
    "perform_update",
    "update",
]


DEFAULT_MANIFEST_URL = (
    "https://phantom.aravindlabs.tech/phantomcli/downloads/version.json"
)


@dataclass(frozen=True, slots=True)
class Manifest:
    version: str
    release_date: str
    download_url: str
    sha256: str
    size_bytes: int
    headline: str
    changelog: tuple[str, ...]


def fetch_manifest(url: str, *, timeout: float = 15.0) -> Manifest:
    """GET *url* and parse the manifest. Raises RuntimeError on any failure."""
    req = Request(url, headers={"User-Agent": f"phantom/{__version__}"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except Exception as exc:
        raise RuntimeError(f"could not fetch manifest from {url}: {exc}") from exc
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"manifest is not valid JSON: {exc}") from exc
    try:
        return Manifest(
            version=str(data["version"]),
            release_date=str(data.get("releaseDate", "")),
            download_url=str(data["downloadUrl"]),
            sha256=str(data.get("sha256", "")),
            size_bytes=int(data.get("size_bytes", 0)),
            headline=str(data.get("headline", "")),
            changelog=tuple(str(x) for x in (data.get("changelog") or [])),
        )
    except KeyError as exc:
        raise RuntimeError(f"manifest missing required field: {exc}") from exc


def compare_versions(a: str, b: str) -> int:
    """Return -1, 0, +1 for a<b, a==b, a>b. Strict semver-ish on numeric parts."""
    def parts(v: str) -> tuple[int, ...]:
        out: list[int] = []
        for chunk in v.split("-", 1)[0].split("."):
            try:
                out.append(int(chunk))
            except ValueError:
                out.append(0)
        return tuple(out)
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa = pa + (0,) * (n - len(pa))
    pb = pb + (0,) * (n - len(pb))
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def find_install_dir() -> Path:
    """Resolve the directory the ``phantom`` package was loaded from.

    The install layout is ``$INSTALL/phantom/...`` and ``$INSTALL/omnicli/...``,
    so the install root is two levels up from this module.
    """
    return Path(__file__).resolve().parent.parent.parent


def is_pip_managed() -> bool:
    """True iff ``pip show phantom-cli`` finds a registered package.

    When False but the user can still run ``phantom``, they have an
    "orphan install" — files on disk and a ``phantom`` shim on PATH,
    but no pip metadata. Zip-extract updates land in the package
    directory, but on Windows the entry-point script in ``Scripts/``
    won't be refreshed and PATH may resolve to a different copy
    entirely. Better to bail with a clear hint than silently update
    the wrong thing.
    """
    import subprocess
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "show", "phantom-cli"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        # Pip not available or hung — assume not pip-managed and let
        # the standard zip-extract path try.
        return False
    return proc.returncode == 0 and "Name:" in proc.stdout


def _orphan_install_hint(zip_url: str) -> str:
    """The block of advice we print when the user runs `phantom update`
    on an orphan install. Concrete, copy-pasteable commands."""
    py = sys.executable
    return (
        "  ! orphan install detected\n"
        "    Pip has no record of phantom-cli, but `phantom` is on your\n"
        "    PATH. A zip-extract update will land in the package dir but\n"
        "    PATH may keep resolving to the old executable, so the\n"
        "    update would silently not take effect.\n\n"
        "    Reinstall fresh via pip — this registers phantom-cli with\n"
        "    pip and replaces the entry-point script:\n\n"
        f"      {py} -m pip install --upgrade --force-reinstall \\\n"
        f"          {zip_url}\n\n"
        "    Then open a fresh terminal and run `phantom version`.\n"
    )


def _download_and_verify(
    url: str, expected_sha: str, *, timeout: float = 60.0,
    progress: Optional[callable] = None,
) -> bytes:
    req = Request(url, headers={"User-Agent": f"phantom/{__version__}"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            buf = io.BytesIO()
            read = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                buf.write(chunk)
                read += len(chunk)
                if progress and total:
                    progress(read, total)
            body = buf.getvalue()
    except Exception as exc:
        raise RuntimeError(f"download failed: {exc}") from exc

    if expected_sha:
        actual = hashlib.sha256(body).hexdigest()
        if actual.lower() != expected_sha.lower():
            raise RuntimeError(
                f"sha256 mismatch: expected {expected_sha}, got {actual}"
            )
    return body


def _extract_to(install_dir: Path, zip_bytes: bytes) -> int:
    """Extract zip into *install_dir*, overwriting. Returns file count."""
    if not install_dir.exists() or not install_dir.is_dir():
        raise RuntimeError(f"install dir not found: {install_dir}")
    if not os.access(install_dir, os.W_OK):
        raise RuntimeError(f"install dir not writable: {install_dir}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for member in zf.namelist():
                # Refuse path traversal: any entry escaping the temp dir.
                target = (tmp_path / member).resolve()
                if not str(target).startswith(str(tmp_path.resolve())):
                    raise RuntimeError(f"unsafe zip entry: {member}")
            zf.extractall(tmp_path)
            count = len(zf.namelist())

        # Copy each top-level entry over the install dir. We do NOT rmtree
        # the install dir first — user data lives there.
        for entry in tmp_path.iterdir():
            dest = install_dir / entry.name
            if entry.is_dir():
                if dest.exists() and dest.is_dir():
                    _merge_tree(entry, dest)
                else:
                    shutil.copytree(entry, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, dest)
    return count


def _merge_tree(src: Path, dst: Path) -> None:
    """Recursive copy that overwrites files but does not delete extras."""
    for entry in src.iterdir():
        target = dst / entry.name
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _merge_tree(entry, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, target)


def perform_update(
    *,
    manifest_url: str,
    install_dir: Optional[Path] = None,
    force: bool = False,
    write: callable = sys.stdout.write,
) -> int:
    """Drive the update. Returns the exit code (0 success, non-zero failure)."""
    write(f"  current: {__version__}\n")
    write(f"  manifest: {manifest_url}\n")

    try:
        manifest = fetch_manifest(manifest_url)
    except RuntimeError as e:
        write(f"  error: {e}\n")
        return 1
    write(f"  latest:  {manifest.version}  ({manifest.release_date})\n")

    cmp = compare_versions(__version__, manifest.version)
    if cmp >= 0 and not force:
        write("  already up to date.\n")
        return 0

    # Orphan-install check: if pip has no record of phantom-cli, the
    # zip-extract path can write to the wrong place (or to a place that
    # PATH ignores). Bail with a concrete pip-reinstall command. This
    # is the v1.1.32 fix for the issue user @aravi hit going from
    # v1.1.28 → v1.1.31 — `/update` reported success but the running
    # `phantom.exe` kept loading the stale package.
    if not is_pip_managed():
        write(_orphan_install_hint(manifest.download_url))
        return 2

    target_dir = install_dir or find_install_dir()
    write(f"  install: {target_dir}\n")

    if not target_dir.exists():
        write(f"  error: install dir not found at {target_dir}\n")
        return 2
    if not os.access(target_dir, os.W_OK):
        write(f"  error: install dir not writable: {target_dir}\n")
        write("  hint: rerun the installer instead — install.ps1 / install.sh\n")
        return 2

    write(f"  downloading {manifest.size_bytes} bytes…\n")
    try:
        zip_bytes = _download_and_verify(manifest.download_url, manifest.sha256)
    except RuntimeError as e:
        write(f"  error: {e}\n")
        return 1

    write("  extracting…\n")
    try:
        count = _extract_to(target_dir, zip_bytes)
    except RuntimeError as e:
        write(f"  error: {e}\n")
        return 1

    write(f"  installed {count} files.\n")
    if manifest.headline:
        write(f"\n  {manifest.headline}\n")
    write(f"\n  updated to v{manifest.version}. Run `phantom version` to verify.\n")
    return 0


def update(
    check: bool = typer.Option(
        False, "--check",
        help="Print whether an update is available, without downloading.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Re-install even if already on the latest version.",
    ),
    manifest_url: str = typer.Option(
        DEFAULT_MANIFEST_URL, "--manifest-url",
        help="Override the manifest URL (for testing or self-hosted mirrors).",
    ),
) -> None:
    """Update Phantom to the latest release."""
    if check:
        try:
            manifest = fetch_manifest(manifest_url)
        except RuntimeError as e:
            typer.echo(f"  error: {e}", err=True)
            raise typer.Exit(1)
        typer.echo(f"  current: {__version__}")
        typer.echo(f"  latest:  {manifest.version}  ({manifest.release_date})")
        cmp = compare_versions(__version__, manifest.version)
        if cmp >= 0:
            typer.echo("  status:  already up to date.")
            raise typer.Exit(0)
        typer.echo("  status:  update available — run `phantom update` to install.")
        if manifest.headline:
            typer.echo(f"  notes:   {manifest.headline}")
        raise typer.Exit(0)

    rc = perform_update(manifest_url=manifest_url, force=force)
    raise typer.Exit(rc)
