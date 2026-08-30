"""HTTP in sola lettura: session cookie, retry, Playwright condiviso se bloccato."""

from __future__ import annotations

import json
import os
import time
from typing import Protocol

import requests

from bidoo_errors import CloudflareBlockedError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

BLOCK_MARKERS = (
    "ci siamo quasi",
    "just a moment",
    "just a moment...",
    "attention required",
    "access denied",
    "request blocked",
    "the request could not be satisfied",
    "checking your browser before accessing",
    "cf-browser-verification",
    "pardon our interruption",
    "/_sec/cp_challenge",
    "errors.edgesuite.net",
)

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['it-IT', 'it', 'en-US'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
"""


class HtmlFetcher(Protocol):
    def get_text(self, url: str) -> str: ...


def _playwright_enabled() -> bool:
    return os.getenv("USE_PLAYWRIGHT", os.getenv("BIDOO_USE_PLAYWRIGHT", "")).lower() in (
        "1",
        "true",
        "yes",
    )


def is_github_hosted() -> bool:
    """True sul runner Ubuntu di GitHub (non self-hosted, non PC di casa)."""
    env = os.getenv("RUNNER_ENVIRONMENT", "").lower()
    if env == "self-hosted":
        return False
    if env == "github-hosted":
        return True
    if os.getenv("GITHUB_ACTIONS") == "true":
        labels = os.getenv("RUNNER_LABELS", "").lower()
        if "self-hosted" in labels:
            return False
        return True
    return False


def _playwright_ok() -> bool:
    if not _playwright_enabled():
        return False
    if is_github_hosted() and os.getenv("USE_PLAYWRIGHT_ON_GITHUB", "false").lower() not in (
        "1",
        "true",
        "yes",
    ):
        return False
    return True


def _retries() -> int:
    default = "1" if is_github_hosted() else "3"
    try:
        return max(1, int(os.getenv("FETCH_RETRIES", default)))
    except ValueError:
        return 1 if is_github_hosted() else 3


def looks_blocked(html: str) -> bool:
    if not html or len(html) < 80:
        return True
    text = html.lower()
    # CDN "akamai" compare anche in pagine vere: non usarlo come segnale unico.
    return any(marker in text for marker in BLOCK_MARKERS)


class SessionFetcher:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(BROWSER_HEADERS)
        self._pw = None
        self._browser = None
        self._context = None

    def __enter__(self) -> SessionFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self._close_browser()
        self._session.close()

    def warm(self, url: str) -> None:
        """Prima visita homepage: cookie/session prima del catalogo."""
        try:
            self.get_text(url, allow_playwright=True)
        except Exception:
            pass

    def get_text(self, url: str, *, allow_playwright: bool = True, referer: str = "") -> str:
        last_error: Exception | None = None
        for attempt in range(_retries()):
            try:
                headers = dict(BROWSER_HEADERS)
                if referer:
                    headers["Referer"] = referer
                    headers["Sec-Fetch-Site"] = "same-origin"
                response = self._session.get(url, headers=headers, timeout=45)
                if response.status_code in (403, 429, 503):
                    last_error = CloudflareBlockedError(
                        f"HTTP {response.status_code} su {url}"
                    )
                    time.sleep(1.2 * (attempt + 1))
                    continue
                response.raise_for_status()
                html = response.text or ""
                if looks_blocked(html):
                    last_error = CloudflareBlockedError(f"Pagina challenge su {url}")
                    time.sleep(0.8 * (attempt + 1))
                    continue
                return html
            except CloudflareBlockedError as exc:
                last_error = exc
            except Exception as exc:
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        if allow_playwright and _playwright_ok():
            try:
                html = self._playwright_get(url)
                if html and not looks_blocked(html):
                    return html
                if html:
                    last_error = CloudflareBlockedError(f"Playwright ancora bloccato su {url}")
            except Exception as exc:
                last_error = exc
        raise last_error or CloudflareBlockedError(f"Fetch fallito: {url}")

    def get_json(
        self,
        url: str,
        params: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict | list:
        last_error: Exception | None = None
        headers = {**BROWSER_HEADERS, **(extra_headers or {})}
        if not extra_headers or "Accept" not in extra_headers:
            headers["Accept"] = "application/json"
        for attempt in range(_retries()):
            try:
                response = self._session.get(url, headers=headers, params=params, timeout=45)
                if response.status_code in (403, 429, 503):
                    last_error = CloudflareBlockedError(f"HTTP {response.status_code} JSON {url}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                response.raise_for_status()
                data = response.json()
                if data is None:
                    last_error = ValueError("JSON vuoto")
                    continue
                return data
            except Exception as exc:
                last_error = exc
                time.sleep(0.8 * (attempt + 1))
        if _playwright_ok():
            full = url
            if params:
                from urllib.parse import urlencode

                full = url + ("&" if "?" in url else "?") + urlencode(params)
            try:
                raw = self._playwright_get(full)
                if raw and not looks_blocked(raw):
                    text = raw.strip()
                    start = text.find("{")
                    start_list = text.find("[")
                    if start_list != -1 and (start == -1 or start_list < start):
                        start = start_list
                    if start != -1:
                        chunk = text[start:]
                        end_obj = chunk.rfind("}")
                        end_arr = chunk.rfind("]")
                        end = max(end_obj, end_arr)
                        if end > 0:
                            return json.loads(chunk[: end + 1])
            except Exception as exc:
                last_error = exc
        raise last_error or CloudflareBlockedError(f"JSON fallito: {url}")

    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-http2",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={
                "Accept-Language": "it-IT,it;q=0.9",
            },
        )
        self._context.add_init_script(_STEALTH_JS)

    def _playwright_get(self, url: str) -> str:
        self._ensure_browser()
        assert self._context is not None
        page = self._context.new_page()
        wait_ms = int(os.getenv("PLAYWRIGHT_WAIT_MS", "4500"))
        goto_ms = int(os.getenv("PLAYWRIGHT_GOTO_MS", "55000"))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=goto_ms)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            # Challenge Cloudflare/Akamai: aspetta fino a ~12s se la pagina è un muro.
            for _ in range(8):
                if not looks_blocked(html) and len(html) > 400:
                    break
                page.wait_for_timeout(1500)
                html = page.content()
            return html
        finally:
            page.close()

    def _close_browser(self) -> None:
        for closer in (self._context, self._browser):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                pass
        self._context = None
        self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


def fetch_with_playwright(url: str) -> str:
    """Compat: una pagina isolata. Preferisci SessionFetcher.get_text (browser riusato)."""
    with SessionFetcher() as fetcher:
        return fetcher._playwright_get(url)


def open_fetcher() -> SessionFetcher:
    return SessionFetcher()
