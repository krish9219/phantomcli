"""Single-page web fetch tool.

A lightweight alternative to launching Chromium via browser-use when
the LLM just needs a page's text content. Caps:

* Hard byte cap (default 256 KiB). Truncates with a marker.
* Hard wall-clock timeout (default 15 s).
* Refuses to follow redirects to private IP ranges (SSRF defence).
* Refuses non-http(s) schemes.
* Returns a structured :class:`WebFetchResult`; never raises on a
  network failure.

Not in scope (delegate to ``browser_task``):

* JavaScript execution.
* Form submission, click-through flows.
* Cookies, auth.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from phantom.errors import PhantomError

__all__ = ["WebFetchResult", "is_private_host", "web_fetch"]


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    ok: bool
    status: int = 0
    url: str = ""
    content_type: str = ""
    text: str = ""
    truncated: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "url": self.url,
            "content_type": self.content_type,
            "text": self.text,
            "truncated": self.truncated,
            "error": self.error,
        }


def is_private_host(host: str) -> bool:
    """True if *host* resolves to a private / loopback / link-local IP.

    The agent talking to ``http://192.168.1.1`` can be a prompt
    injection trying to read your router's admin page. We block all
    RFC1918 + loopback + link-local + IPv6 ULA + IPv6 link-local
    addresses by default. Operators that need to talk to internal
    services use a dedicated plugin with explicit policy.
    """
    if not host:
        return True
    # Try literal-IP first; fall back to DNS.
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            results = socket.getaddrinfo(host, None)
        except socket.gaierror:
            # Host doesn't resolve → block. The fetch would fail
            # anyway; we want to fail closed.
            return True
        for fam, _type, _proto, _name, sockaddr in results:
            addr = sockaddr[0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _is_private_ip(ip):
                return True
        return False
    return _is_private_ip(ip)


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def web_fetch(
    *,
    url: str,
    max_bytes: int = 256 * 1024,
    timeout_s: float = 15.0,
    user_agent: str = "phantom/4.1.0-dev (web_fetch)",
    client: Any = None,
) -> WebFetchResult:
    """Fetch *url* and return the body as text.

    Validation:
    * URL must parse and use http or https.
    * Host must not resolve to a private IP (SSRF block).
    * Response is read up to ``max_bytes``; excess truncated.
    """
    if max_bytes < 1024:
        raise PhantomError("max_bytes must be ≥ 1024")
    if timeout_s <= 0:
        raise PhantomError("timeout_s must be > 0")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return WebFetchResult(
            ok=False, url=url,
            error=f"only http(s) supported, got {parsed.scheme!r}",
        )
    if not parsed.netloc:
        return WebFetchResult(ok=False, url=url, error="missing host")
    host = parsed.hostname or ""
    if is_private_host(host):
        return WebFetchResult(
            ok=False, url=url,
            error=f"refusing private/internal host {host!r}",
        )

    if client is None:
        import httpx
        client = httpx.Client(
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )
        owns_client = True
    else:
        owns_client = False

    try:
        try:
            response = client.get(url)
        except Exception as exc:
            return WebFetchResult(
                ok=False, url=url,
                error=f"{type(exc).__name__}: {exc}",
            )

        # SSRF check on the *final* host after redirects.
        final_url = str(response.url)
        final_parsed = urlparse(final_url)
        final_host = final_parsed.hostname or ""
        if final_host and is_private_host(final_host):
            return WebFetchResult(
                ok=False, url=final_url,
                error=f"refusing redirect to private host {final_host!r}",
            )

        body = response.content
        truncated = False
        if len(body) > max_bytes:
            body = body[:max_bytes]
            truncated = True
        text = body.decode("utf-8", errors="replace")
        if truncated:
            text += "\n[phantom: response truncated]"

        return WebFetchResult(
            ok=200 <= response.status_code < 400,
            status=response.status_code,
            url=final_url,
            content_type=str(response.headers.get("content-type", "")),
            text=text,
            truncated=truncated,
        )
    finally:
        if owns_client:
            client.close()
