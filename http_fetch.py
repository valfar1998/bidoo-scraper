"""HTTP in sola lettura per i cataloghi pubblici."""

from __future__ import annotations

import os
from typing import Protocol

import requests

from bidoo_errors import CloudflareBlockedError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}


class HtmlFetcher(Protocol):
    def get_text(self, url: str) -> str: ...


class SessionFetcher:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(BROWSER_HEADERS)

    def __enter__(self) -> SessionFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self._session.close()

    def get_text(self, url: str) -> str:
        response = self._session.get(url, headers=BROWSER_HEADERS, timeout=40)
        if response.status_code in (403, 429, 503):
            if _playwright_enabled():
                return fetch_with_playwright(url)
            raise CloudflareBlockedError(
                f"Accesso bloccato ({response.status_code}) su {url}. "
                "Riprova da casa o imposta USE_PLAYWRIGHT=true."
            )
        response.raise_for_status()
        return response.text

    def get_json(self, url: str, params: dict | None = None) -> dict:
        response = self._session.get(url, headers=BROWSER_HEADERS, params=params, timeout=40)
        response.raise_for_status()
        return response.json()


def _playwright_enabled() -> bool:
    return os.getenv("USE_PLAYWRIGHT", os.getenv("BIDOO_USE_PLAYWRIGHT", "")).lower() in (
        "1",
        "true",
        "yes",
    )


def fetch_with_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context = browser.new_context(
                user_agent=USER_AGENT,
                locale="it-IT",
                viewport={"width": 1366, "height": 900},
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(2500)
            return page.content()
        finally:
            browser.close()


def open_fetcher() -> SessionFetcher:
    return SessionFetcher()
