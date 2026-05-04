import asyncio
from rich.console import Console

console = Console()

# Module-level toggle for silencing the "Phantom Browser launching…" and
# "Direct fetch blocked…" log lines. Commands that print their own clean
# progress (e.g. /web) set this True around their scrape batch via
# try/finally. Default False so the agent's normal tool-use path still
# shows the familiar console hints.
_QUIET: bool = False


def set_quiet(quiet: bool) -> None:
    global _QUIET
    _QUIET = bool(quiet)


def _log(msg: str) -> None:
    if not _QUIET:
        console.print(msg)


async def _fetch_playwright(url: str) -> str:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=15000)
        content = await page.evaluate("document.body.innerText")
        await browser.close()
        return content[:8000] if content else ""

async def _fetch_jina(url: str) -> str:
    """Jina Reader — works on sites that block headless browsers."""
    import aiohttp
    jina_url = f"https://r.jina.ai/{url}"
    headers = {"Accept": "text/plain", "X-Remove-Selector": "nav,footer,aside,script,style"}
    async with aiohttp.ClientSession() as session:
        async with session.get(jina_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                text = await r.text()
                return text[:8000]
    return ""

async def fetch_page_content(url: str) -> str:
    _log(f"[dim]Phantom Browser launching to inspect: {url}[/dim]")
    # Try direct Playwright first
    try:
        content = await _fetch_playwright(url)
        if content and len(content) > 200:
            return content
    except Exception:
        pass

    # Fallback: Jina Reader proxy (bypasses most 403/bot-blocks)
    _log(f"[dim]Direct fetch blocked — retrying via Jina reader…[/dim]")
    try:
        content = await _fetch_jina(url)
        if content and len(content) > 200:
            return content
    except Exception:
        pass

    # Last resort: requests with a browser UA
    try:
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*",
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.ok and r.text:
            from html.parser import HTMLParser
            class _Strip(HTMLParser):
                def __init__(self):
                    super().__init__(); self.parts = []; self._skip = False
                def handle_starttag(self, tag, attrs):
                    if tag in ("script", "style", "nav", "footer"): self._skip = True
                def handle_endtag(self, tag):
                    if tag in ("script", "style", "nav", "footer"): self._skip = False
                def handle_data(self, data):
                    if not self._skip: self.parts.append(data)
            p = _Strip(); p.feed(r.text)
            text = " ".join(p.parts).strip()[:8000]
            if len(text) > 200:
                return text
    except Exception:
        pass

    return f"Could not fetch content from {url} — page may require login or is heavily bot-protected."

def run_browser(url: str) -> str:
    return asyncio.run(fetch_page_content(url))
