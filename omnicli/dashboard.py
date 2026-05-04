import os
import html
import logging
import sqlite3
import asyncio
import json
import secrets
import time
from collections import defaultdict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from omnicli.logging_setup import configure_logging
configure_logging()
from omnicli.memory import DB_PATH, get_config, save_message, get_recent_history, init_db
from omnicli.auth import get_api_key
from omnicli.licensing import is_licensed, validate_key_online, get_license_info, revoke_local_license
from omnicli import __version__ as APP_VERSION

log = logging.getLogger("omnicli.dashboard")
app = FastAPI(title="PhantomCLI Dashboard")
connected_clients: list[WebSocket] = []

# ─── RATE LIMITING ────────────────────────────────────────────────────────────

_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _rate_check(key: str, limit: int, window: int) -> bool:
    now  = time.time()
    hits = _rate_buckets[key]
    hits[:] = [t for t in hits if t > now - window]
    if len(hits) >= limit:
        return False
    hits.append(now)
    idle = [k for k, v in _rate_buckets.items() if v and max(v) < now - window * 2]
    for k in idle:
        del _rate_buckets[k]
    return True


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ─── CSRF TOKENS ─────────────────────────────────────────────────────────────
# Stored in-memory — intentionally local-only. Persistence across restarts is
# not required for a localhost dashboard; the risk model is cross-origin requests,
# not cross-session replay.

_csrf_tokens: dict[str, float] = {}
_CSRF_TTL = 3600


def _issue_csrf() -> str:
    token = secrets.token_hex(32)
    _csrf_tokens[token] = time.time() + _CSRF_TTL
    return token


def _validate_csrf(token: str) -> bool:
    now     = time.time()
    expired = [k for k, v in _csrf_tokens.items() if v < now]
    for k in expired:
        del _csrf_tokens[k]
    expiry = _csrf_tokens.get(token)
    return bool(expiry and now <= expiry)


def _purge_csrf() -> None:
    now     = time.time()
    expired = [k for k, v in _csrf_tokens.items() if v < now]
    for k in expired:
        del _csrf_tokens[k]


# ─── LICENSE GATE MIDDLEWARE ──────────────────────────────────────────────────

LICENSE_EXEMPT = {"/activate", "/api/license/activate", "/api/license/status", "/phantomcli"}


class LicenseGate(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in LICENSE_EXEMPT or path.startswith("/static"):
            return await call_next(request)
        if not is_licensed():
            if (request.headers.get("accept", "").startswith("application/json")
                    or request.headers.get("content-type", "") == "application/json"):
                return JSONResponse({"error": "License required", "redirect": "/activate"}, status_code=402)
            return RedirectResponse("/activate")
        return await call_next(request)


app.add_middleware(LicenseGate)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─── STATS ────────────────────────────────────────────────────────────────────

def get_stats():
    if not os.path.exists(DB_PATH):
        return {"interactions": 0, "model": "Not Configured", "router": "Not Configured",
                "user_msgs": 0, "ai_msgs": 0}
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM episodic_logs");          total     = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM episodic_logs WHERE role='user'");      user_msgs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM episodic_logs WHERE role='assistant'"); ai_msgs   = cur.fetchone()[0]
    return {
        "interactions": total,
        "model":        get_config("main_model",   "Unknown"),
        "router":       get_config("router_model", "Unknown"),
        "user_msgs":    user_msgs,
        "ai_msgs":      ai_msgs,
    }


def get_chat_history():
    if not os.path.exists(DB_PATH):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT role, content, timestamp FROM episodic_logs ORDER BY id ASC")
        return [{"role": r[0], "content": r[1], "timestamp": str(r[2])} for r in cur.fetchall()]


# ─── WEBSOCKET ────────────────────────────────────────────────────────────────

_ws_rate: dict[int, list[float]] = defaultdict(list)
_WS_MSG_LIMIT  = 30
_WS_MSG_WINDOW = 60


# Concurrent-socket cap. Even on localhost a rogue script can open thousands
# of sockets and exhaust the event loop — this stops that. Override via config
# `max_ws_clients` if you legitimately need more.
_DEFAULT_MAX_WS_CLIENTS = 32


def _max_ws_clients() -> int:
    try:
        return max(1, int(get_config("max_ws_clients", str(_DEFAULT_MAX_WS_CLIENTS))
                          or _DEFAULT_MAX_WS_CLIENTS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_WS_CLIENTS


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    csrf_token: str = Query(default=""),
):
    if not _validate_csrf(csrf_token):
        await websocket.close(code=1008, reason="Invalid or missing CSRF token")
        return

    if len(connected_clients) >= _max_ws_clients():
        log.warning("rejecting websocket: cap reached (%d)", len(connected_clients))
        await websocket.close(code=1013, reason="Server busy — too many connections")
        return

    await websocket.accept()
    connected_clients.append(websocket)

    try:
        while True:
            data    = await websocket.receive_text()
            payload = json.loads(data)

            if payload.get("type") != "chat":
                continue

            # Per-connection rate limit
            conn_id = id(websocket)
            now     = time.time()
            bucket  = _ws_rate[conn_id]
            bucket[:] = [t for t in bucket if t > now - _WS_MSG_WINDOW]
            if len(bucket) >= _WS_MSG_LIMIT:
                await websocket.send_text(json.dumps({
                    "type": "error", "message": "Rate limit exceeded. Slow down.",
                }))
                continue
            bucket.append(now)

            prompt = payload.get("message", "").strip()
            # Trust level is always read from server-side config — never from client
            trust_level = int(get_config("default_trust", "3"))

            if not prompt:
                continue
            if len(prompt) > 32_000:
                await websocket.send_text(json.dumps({
                    "type": "error", "message": "Message too long (max 32 000 chars).",
                }))
                continue

            init_db()
            api_key = get_api_key()
            if not api_key:
                await websocket.send_text(json.dumps({
                    "type": "error", "message": "Not configured. Run python run.py setup first.",
                }))
                continue

            # ── Streaming bridge ────────────────────────────────────────────
            # generate_response() is synchronous. We bridge its on_chunk callback
            # to the async WebSocket via asyncio.Queue + call_soon_threadsafe.

            from omnicli.engine import generate_response
            from omnicli.tasks import TaskTracker
            chat_history = get_recent_history(limit=20)
            save_message("user", prompt)

            await websocket.send_text(json.dumps({"type": "thinking"}))

            loop      = asyncio.get_running_loop()
            # Queue carries tagged tuples: ("chunk", text) or ("task", tasks_list)
            # or None sentinel to signal completion.
            event_q: asyncio.Queue = asyncio.Queue()

            def on_chunk(text: str):
                loop.call_soon_threadsafe(event_q.put_nowait, ("chunk", text))

            def on_task(tracker: TaskTracker):
                try:
                    payload = tracker.to_dicts()
                except Exception:
                    return
                loop.call_soon_threadsafe(event_q.put_nowait, ("task", payload))

            tracker = TaskTracker(on_change=on_task)

            def run_sync():
                try:
                    result = generate_response(prompt, chat_history, trust_level, on_chunk, tracker=tracker)
                except Exception as exc:
                    result = (f"**API Error:** {exc}", [])
                finally:
                    loop.call_soon_threadsafe(event_q.put_nowait, None)  # sentinel
                return result

            future = loop.run_in_executor(None, run_sync)

            # Relay events to the WebSocket as they arrive
            while True:
                evt = await event_q.get()
                if evt is None:
                    break
                kind, payload = evt
                if kind == "chunk":
                    await websocket.send_text(json.dumps({"type": "chunk", "content": payload}))
                elif kind == "task":
                    await websocket.send_text(json.dumps({"type": "task_update", "tasks": payload}))

            ai_response, _ = await future
            save_message("assistant", ai_response)

            # Notify Telegram for substantial responses
            try:
                from omnicli.cli import send_telegram
                if len(ai_response.split()) > 50:
                    send_telegram(
                        f"⚡ *PhantomCLI Task Done*\n\n"
                        f"*You asked:* {prompt[:100]}\n\n"
                        f"*Result:* {ai_response[:300]}"
                    )
            except Exception:
                pass

            await websocket.send_text(json.dumps({
                "type":    "done",
                "message": ai_response,
            }))

    except WebSocketDisconnect:
        log.debug("websocket disconnected cleanly")
    except Exception:
        log.exception("websocket handler crashed")
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "message": "An internal error occurred.",
            }))
        except Exception:
            log.debug("failed to send error frame on dying websocket", exc_info=True)
    finally:
        _ws_rate.pop(id(websocket), None)
        if websocket in connected_clients:
            connected_clients.remove(websocket)


# ─── API ROUTES ───────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def api_stats():
    return get_stats()


@app.get("/api/license/status")
async def api_license_status():
    return get_license_info()


@app.get("/api/csrf-token")
async def api_csrf_token():
    _purge_csrf()
    return {"csrf_token": _issue_csrf()}


@app.post("/api/license/activate")
async def api_license_activate(request: Request):
    ip = _client_ip(request)
    if not _rate_check(f"license_activate:{ip}", 5, 60):
        return JSONResponse(
            {"success": False, "error": "Too many attempts. Wait 60 seconds."},
            status_code=429,
        )

    body = await request.json()

    csrf = body.get("csrf_token", "")
    if not _validate_csrf(csrf):
        return JSONResponse({"success": False, "error": "Invalid or expired CSRF token."}, status_code=403)

    key = body.get("key", "").strip()
    if not key:
        return JSONResponse({"success": False, "error": "License key is required"}, status_code=400)

    valid, email_or_error = validate_key_online(key)
    if valid:
        return JSONResponse({"success": True, "email": email_or_error})
    return JSONResponse({"success": False, "error": email_or_error}, status_code=400)


@app.post("/api/license/revoke")
async def api_license_revoke(request: Request):
    body = await request.json()
    csrf = body.get("csrf_token", "")
    if not _validate_csrf(csrf):
        return JSONResponse({"success": False, "error": "Invalid or expired CSRF token."}, status_code=403)
    revoke_local_license()
    return JSONResponse({"success": True})


# ─── PAGES ────────────────────────────────────────────────────────────────────

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def _load_template(name: str) -> str:
    """Read a template file, or raise a 500 with a helpful install-hint message."""
    path = os.path.join(_TEMPLATES_DIR, name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise RuntimeError(
            f"Template `{name}` is missing at `{path}`. "
            "This usually means the package was installed without its template files. "
            "Reinstall PhantomCLI, or restore the templates directory from the source zip."
        )


def _template_error_page(message: str, status_code: int = 500) -> HTMLResponse:
    safe = html.escape(message)
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>PhantomCLI — Template Error</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#0b0b0f;color:#eee;"
        "margin:0;padding:48px;} .box{max-width:640px;margin:auto;background:#17171d;"
        "padding:24px;border-radius:12px;border:1px solid #2a2a33;} code{background:#222;"
        "padding:2px 6px;border-radius:4px;} h1{color:#ff6b6b;margin-top:0;}</style></head>"
        "<body><div class='box'><h1>⚠ Template not found</h1>"
        f"<p>{safe}</p>"
        "<p>Run <code>python run.py update</code> or reinstall the package.</p>"
        "</div></body></html>"
    )
    return HTMLResponse(body, status_code=status_code)


@app.get("/activate", response_class=HTMLResponse)
async def serve_activate():
    try:
        return HTMLResponse(_load_template("activate.html"))
    except RuntimeError as exc:
        return _template_error_page(str(exc))


@app.get("/phantomcli", response_class=HTMLResponse)
async def serve_phantomcli_buy():
    return RedirectResponse("https://phantom.aravindlabs.tech/phantomcli")


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    stats   = get_stats()
    history = get_chat_history()
    try:
        tmpl = _load_template("dashboard.html")
    except RuntimeError as exc:
        return _template_error_page(str(exc))

    # All user-controlled strings are HTML-escaped before insertion.
    # JSON blobs are safe (json.dumps escapes < > & by default).
    safe_stats   = json.dumps(stats)
    safe_history = json.dumps(history)

    tmpl = tmpl.replace("__STATS_JSON__",    safe_stats)
    tmpl = tmpl.replace("__HISTORY_JSON__",  safe_history)
    tmpl = tmpl.replace("__MAIN_MODEL__",    html.escape(str(stats["model"])))
    tmpl = tmpl.replace("__ROUTER_MODEL__",  html.escape(str(stats["router"])))
    tmpl = tmpl.replace("__TOTAL__",         html.escape(str(stats["interactions"])))
    tmpl = tmpl.replace("__USER_MSGS__",     html.escape(str(stats["user_msgs"])))
    tmpl = tmpl.replace("__AI_MSGS__",       html.escape(str(stats["ai_msgs"])))
    tmpl = tmpl.replace("__APP_VERSION__",   html.escape(str(APP_VERSION)))
    return tmpl
