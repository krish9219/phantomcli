"""FastAPI router for PWA-related endpoints.

Mounted under ``/pwa/`` on the dashboard:

* ``GET  /pwa/manifest.webmanifest`` — the web manifest
* ``GET  /pwa/sw.js``                — the service worker source
* ``GET  /pwa/vapid-public-key``     — JSON ``{"public_key": "<b64url>"}``
* ``POST /pwa/subscribe``            — register a push subscription
* ``DELETE /pwa/subscribe``          — unregister
* ``GET  /pwa/healthz``              — liveness

The router is built around a :class:`SubscriptionStore` so tests pass an
in-memory store. Production wiring uses the file-backed default.
"""

# NOTE: deliberately NO `from __future__ import annotations` — FastAPI
# introspects function signatures at decorator time to recognise
# Request/WebSocket/etc. parameters; PEP-563 deferred evaluation breaks
# that and FastAPI treats `request: Request` as a missing body field.

import json
from typing import Any, Optional

from phantom.pwa.manifest import build_manifest, build_service_worker
from phantom.pwa.push import PushSubscription, SubscriptionStore

__all__ = ["build_pwa_router"]


def build_pwa_router(
    *,
    store: Optional[SubscriptionStore] = None,
    vapid_public_key_b64url: Optional[str] = None,
):
    """Build a FastAPI APIRouter exposing the PWA endpoints."""
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import JSONResponse, PlainTextResponse, Response

    sub_store = store or SubscriptionStore()
    router = APIRouter(prefix="/pwa")

    @router.get("/manifest.webmanifest", response_class=JSONResponse)
    def manifest() -> Any:
        return JSONResponse(build_manifest(), media_type="application/manifest+json")

    @router.get("/sw.js", response_class=PlainTextResponse)
    def service_worker() -> Any:
        body = build_service_worker()
        return Response(content=body, media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})

    @router.get("/vapid-public-key")
    def vapid_key() -> dict[str, str]:
        if not vapid_public_key_b64url:
            raise HTTPException(503, "push not configured (no VAPID public key)")
        return {"public_key": vapid_public_key_b64url}

    @router.post("/subscribe")
    async def subscribe(request: Request) -> dict[str, Any]:
        body = await request.json()
        endpoint = body.get("endpoint")
        keys = body.get("keys") or {}
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise HTTPException(400, "endpoint must be an https URL")
        p256dh = keys.get("p256dh")
        auth = keys.get("auth")
        if not isinstance(p256dh, str) or not isinstance(auth, str):
            raise HTTPException(400, "keys.p256dh and keys.auth must be strings")
        sub = PushSubscription(
            endpoint=endpoint, p256dh=p256dh, auth=auth,
            user_agent=request.headers.get("user-agent", ""),
        )
        is_new = sub_store.add(sub)
        return {"ok": True, "new": is_new, "total": len(sub_store)}

    @router.delete("/subscribe")
    async def unsubscribe(request: Request) -> dict[str, Any]:
        body = await request.json()
        endpoint = body.get("endpoint")
        if not isinstance(endpoint, str):
            raise HTTPException(400, "endpoint required")
        removed = sub_store.remove(endpoint)
        return {"ok": True, "removed": removed, "total": len(sub_store)}

    @router.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True, "subscriptions": len(sub_store),
                "push_enabled": bool(vapid_public_key_b64url)}

    return router
