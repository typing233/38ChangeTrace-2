import hashlib
import re
import time
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
import httpx

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

_playwright = None
_browser = None


async def get_browser():
    global _playwright, _browser
    if not HAS_PLAYWRIGHT:
        raise RuntimeError("Playwright not installed. Install with: pip install playwright && playwright install chromium")
    if _browser is None:
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    return _browser


async def fetch_static(url: str, headers: dict, cookies: dict, proxy: str, timeout: int) -> tuple[str, dict]:
    """Returns (html, resource_meta)."""
    client_kwargs = {"timeout": timeout, "follow_redirects": True}
    if proxy:
        client_kwargs["proxy"] = proxy
    t0 = time.time()
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.get(url, headers=headers, cookies=cookies)
        resp.raise_for_status()
        elapsed_ms = int((time.time() - t0) * 1000)
        html = resp.text
        meta = {
            "url": str(resp.url),
            "status_code": resp.status_code,
            "content_type": resp.headers.get("content-type", ""),
            "content_length": len(resp.content),
            "encoding": resp.encoding,
            "elapsed_ms": elapsed_ms,
            "response_headers": dict(resp.headers),
            "render_mode": "static",
        }
        meta["resources"] = _extract_resource_list(html, str(resp.url))
        return html, meta


async def fetch_js(url: str, headers: dict, cookies: dict, proxy: str, timeout: int, screenshot_path: str = "") -> tuple[str, dict]:
    """Returns (html, resource_meta)."""
    browser = await get_browser()
    context_kwargs = {}
    if proxy:
        context_kwargs["proxy"] = {"server": proxy}
    if headers:
        context_kwargs["extra_http_headers"] = headers
    context = await browser.new_context(**context_kwargs)
    if cookies:
        cookie_list = [{"name": k, "value": v, "url": url} for k, v in cookies.items()]
        await context.add_cookies(cookie_list)

    page = await context.new_page()
    network_resources = []

    def on_response(response):
        network_resources.append({
            "url": response.url,
            "status": response.status,
            "type": response.request.resource_type,
            "size": int(response.headers.get("content-length", 0)),
        })

    page.on("response", on_response)
    t0 = time.time()
    try:
        resp = await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        elapsed_ms = int((time.time() - t0) * 1000)
        if screenshot_path:
            await page.screenshot(path=screenshot_path, full_page=True)
        title = await page.title()
        html = await page.content()
    finally:
        await page.close()
        await context.close()

    meta = {
        "url": url,
        "status_code": resp.status if resp else 0,
        "content_type": resp.headers.get("content-type", "") if resp else "",
        "page_title": title,
        "elapsed_ms": elapsed_ms,
        "render_mode": "js",
        "network_requests": len(network_resources),
        "resources": _summarize_network_resources(network_resources),
    }
    return html, meta


def _extract_resource_list(html: str, base_url: str) -> dict:
    """Extract resource references from static HTML."""
    soup = BeautifulSoup(html, "lxml")
    resources = {"scripts": [], "stylesheets": [], "images": [], "links": []}

    for tag in soup.find_all("script", src=True):
        resources["scripts"].append(urljoin(base_url, tag["src"]))
    for tag in soup.find_all("link", rel="stylesheet"):
        href = tag.get("href", "")
        if href:
            resources["stylesheets"].append(urljoin(base_url, href))
    for tag in soup.find_all("img", src=True):
        resources["images"].append(urljoin(base_url, tag["src"]))
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if href.startswith("http"):
            resources["links"].append(href)

    title_tag = soup.find("title")
    summary = {
        "page_title": title_tag.get_text(strip=True) if title_tag else "",
        "script_count": len(resources["scripts"]),
        "stylesheet_count": len(resources["stylesheets"]),
        "image_count": len(resources["images"]),
        "link_count": len(resources["links"]),
        "scripts": resources["scripts"][:20],
        "stylesheets": resources["stylesheets"][:10],
        "images": resources["images"][:20],
    }
    return summary


def _summarize_network_resources(resources: list[dict]) -> dict:
    """Summarize network resources captured during JS rendering."""
    by_type = {}
    total_size = 0
    for r in resources:
        rtype = r.get("type", "other")
        by_type.setdefault(rtype, {"count": 0, "size": 0})
        by_type[rtype]["count"] += 1
        by_type[rtype]["size"] += r.get("size", 0)
        total_size += r.get("size", 0)
    return {
        "total_requests": len(resources),
        "total_size_bytes": total_size,
        "by_type": by_type,
    }


def normalize_and_extract(html: str, include_selector: str, exclude_selector: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "lxml")

    if exclude_selector:
        for sel in exclude_selector.split(","):
            sel = sel.strip()
            if sel:
                for el in soup.select(sel):
                    el.decompose()

    if include_selector:
        parts = []
        for sel in include_selector.split(","):
            sel = sel.strip()
            if sel:
                parts.extend(soup.select(sel))
        if parts:
            new_soup = BeautifulSoup("<div></div>", "lxml")
            container = new_soup.find("div")
            for p in parts:
                container.append(p)
            soup = new_soup

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    normalized_html = soup.prettify()
    return normalized_html, text


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
