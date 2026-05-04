"""``python -m phantom.plugins.mirror.serve_cli`` — boot the mirror server.

Reads its config from environment variables so it slots cleanly into a
Docker / systemd / Caddy stack:

* ``PHANTOM_MIRROR_DATA`` — store root (default: ``/srv/phantom-mirror``)
* ``PHANTOM_MIRROR_HOST`` — bind host  (default: ``127.0.0.1``)
* ``PHANTOM_MIRROR_PORT`` — bind port  (default: ``8801``)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("phantom.mirror")

    data_root = Path(os.environ.get("PHANTOM_MIRROR_DATA", "/srv/phantom-mirror"))
    host = os.environ.get("PHANTOM_MIRROR_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("PHANTOM_MIRROR_PORT", "8801"))
    except ValueError:
        log.error("PHANTOM_MIRROR_PORT must be an integer")
        return 2

    try:
        from phantom.plugins.mirror.server import MirrorStore, build_app
        import uvicorn
    except ImportError as e:
        log.error("missing dependency: %s. Install with `pip install fastapi uvicorn`", e)
        return 3

    store = MirrorStore(data_root)
    store.init()
    log.info("phantom mirror serving %s on %s:%d (%d plugins)",
             data_root, host, port, len(store.load_index().get("plugins", [])))
    app = build_app(store)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
