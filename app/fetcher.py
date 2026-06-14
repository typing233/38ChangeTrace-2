import hashlib
import re
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


async def fetch_static(url: str, headers: dict, cookies: dict, proxy: str, timeout: int) -> str:
    client_kwargs = {"timeout": timeout, "follow_redirects": True}
    if proxy:
        client_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.get(url, headers=headers, cookies=cookies)
        resp.raise_for_status()
        return resp.text


async def fetch_js(url: str, headers: dict, cookies: dict, proxy: str, timeout: int, screenshot_path: str = "") -> str:
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
    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        if screenshot_path:
            await page.screenshot(path=screenshot_path, full_page=True)
        html = await page.content()
    finally:
        await page.close()
        await context.close()
    return html


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
