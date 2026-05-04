"""PWA build CLI — emit deployable static assets.

Writes everything you need under ``<out>/``:

* ``index.html``           — minimal app shell
* ``manifest.webmanifest`` — Web App Manifest
* ``service-worker.js``    — generated service worker
* ``main.js``              — placeholder entry point
* ``main.css``             — placeholder styles
* ``icon-192.png``         — placeholder icon (32×32 actual; the
                              browser scales it; replace with a real
                              192×192 before shipping)
* ``icon-512.png``         — placeholder icon (same)
* ``README.md``            — deploy instructions

The generated tree is deployable behind any static host. The default
target is ``phantom.aravindlabs.tech/app/``. Operators replace the
icons + main.js + main.css with their dashboard build output.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

from phantom.pwa.manifest import build_manifest, build_service_worker

__all__ = ["build_pwa"]


# Minimal valid PNG (1×1 transparent pixel). The PWA spec needs valid
# PNGs at the manifest's icon paths; operators replace these in
# production. We hand-build the bytes so the build CLI has zero
# dependencies on Pillow.
def _tiny_png() -> bytes:
    # 1×1 transparent RGBA PNG.
    header = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)  # 1x1, 8-bit RGBA
    idat = zlib.compress(b"\x00\x00\x00\x00\x00")  # filter byte + RGBA(0,0,0,0)
    iend = b""
    return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", iend)


_INDEX_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#0d1117">
  <title>Phantom</title>
  <link rel="manifest" href="./manifest.webmanifest">
  <link rel="icon" href="./icon-192.png">
  <link rel="apple-touch-icon" href="./icon-192.png">
  <link rel="stylesheet" href="./main.css">
</head>
<body>
  <noscript>Phantom requires JavaScript.</noscript>
  <div id="app">Loading Phantom…</div>
  <script>
    if ("serviceWorker" in navigator) {
      window.addEventListener("load", () => {
        navigator.serviceWorker.register("./service-worker.js");
      });
    }
  </script>
  <script src="./main.js"></script>
</body>
</html>
"""

_MAIN_CSS = """\
:root { color-scheme: dark; }
body {
  margin: 0;
  font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  background: #0d1117;
  color: #e6edf3;
}
#app { padding: 1.5rem; }
"""

_MAIN_JS = """\
// Placeholder entry. Operators replace this with their compiled
// dashboard bundle (React / Svelte / vanilla — any flavour).
console.info("Phantom PWA shell loaded. Replace main.js with your dashboard.");
"""

_README = """\
# Phantom PWA — generated assets

This directory was emitted by `phantom pwa build`. Deploy the entire
folder behind a static host at the path declared in the manifest's
`scope` (default `/app/`).

## Contents

* `index.html`           — app shell.
* `manifest.webmanifest` — Web App Manifest.
* `service-worker.js`    — generated SW.
* `main.js` / `main.css` — placeholders. Replace with your dashboard.
* `icon-192.png` / `icon-512.png` — placeholders. Replace with real
  artwork at the listed sizes.

## Deploying behind Caddy

```caddy
phantom.aravindlabs.tech {
    root * /var/www/phantom
    file_server
    encode gzip
    header /app/service-worker.js Cache-Control "no-cache"
    header /app/manifest.webmanifest Cache-Control "max-age=300"
    header /app/* Cache-Control "max-age=86400, must-revalidate"
}
```

## Bumping the cache version

The service worker's cache version is baked in at build time:

```bash
phantom pwa build --cache-version v2 --out dist/pwa/
```

Changing the version forces every installed PWA to refresh its app
shell on next visit.
"""


def build_pwa(
    out: str | Path,
    *,
    cache_version: str = "v1",
    site_name: str = "Phantom",
    short_name: str = "Phantom",
) -> Path:
    """Write the PWA static tree under *out* and return the path."""
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(name=site_name, short_name=short_name)
    sw = build_service_worker(cache_version=cache_version)

    (target / "index.html").write_text(_INDEX_HTML)
    (target / "manifest.webmanifest").write_text(
        json.dumps(manifest, indent=2)
    )
    (target / "service-worker.js").write_text(sw)
    (target / "main.js").write_text(_MAIN_JS)
    (target / "main.css").write_text(_MAIN_CSS)
    (target / "README.md").write_text(_README)

    png = _tiny_png()
    (target / "icon-192.png").write_bytes(png)
    (target / "icon-512.png").write_bytes(png)

    return target
