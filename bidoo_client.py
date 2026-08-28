"""Client leggero per leggere aste pubbliche da Bidoo (solo monitoraggio)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
DEFAULT_BASE_URL = "https://it.bidoo.com/"

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

CHALLENGE_MARKERS = (
    "Controllo di Sicurezza",
    "Security Check",
    "cf-browser-verification",
    "challenge-platform",
)


@dataclass(frozen=True)
class Auction:
    auction_id: str
    name: str
    slug: str
    retail_value: float
    url: str


@dataclass(frozen=True)
class LiveAuction:
    auction_id: str
    state: str
    expires_at: int
    price_cents: int
    winner: str
    bid_type: str

    @property
    def price_eur(self) -> float:
        return self.price_cents / 100


class FetchContext(Protocol):
    def get_text(self, url: str) -> str: ...


class RequestsFetchContext:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(BROWSER_HEADERS)

    def __enter__(self) -> RequestsFetchContext:
        return self

    def __exit__(self, *args: object) -> None:
        self._session.close()

    def get_text(self, url: str) -> str:
        response = self._session.get(url, headers=BROWSER_HEADERS, timeout=30)
        if response.status_code == 403:
            return _fetch_with_playwright_standalone(url)
        response.raise_for_status()
        return response.text


class PlaywrightFetchContext:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._warmed_up = False

    def __enter__(self) -> PlaywrightFetchContext:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="it-IT",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "it-IT,it;q=0.9"},
        )
        self._page = self._context.new_page()
        return self

    def __exit__(self, *args: object) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _warmup(self, url: str) -> None:
        if self._warmed_up:
            return
        assert self._page is not None
        assert self._context is not None

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=120_000)
                _dismiss_cookie_banner(self._page)
                _wait_for_auction_content(self._page)
                self._warmed_up = True
                return
            except Exception as exc:
                last_error = exc
                print(f"Tentativo {attempt + 1}/3 fallito: {exc}")
                self._page.wait_for_timeout(5000)

        raise RuntimeError(
            "Impossibile caricare le aste da Bidoo dopo 3 tentativi."
        ) from last_error

    def get_text(self, url: str) -> str:
        assert self._page is not None
        assert self._context is not None

        if not self._warmed_up:
            index_url = _index_url_for(url)
            self._warmup(index_url)

        if "data.php" in url:
            response = self._context.request.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "it-IT,it;q=0.9",
                    "Referer": DEFAULT_BASE_URL,
                },
            )
            if response.status >= 400:
                self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                return self._page.content()
            return response.text()

        if self._page.url.rstrip("/") != url.rstrip("/"):
            self._page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            _dismiss_cookie_banner(self._page)
            _wait_for_auction_content(self._page)

        return self._page.content()


def _use_playwright() -> bool:
    return os.getenv("BIDOO_USE_PLAYWRIGHT", "").lower() in ("1", "true", "yes")


def open_fetch_context() -> FetchContext:
    if _use_playwright():
        return PlaywrightFetchContext()
    return RequestsFetchContext()


def _session() -> requests.Session:
    """Compatibilità con codice legacy."""
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    return session


def _index_url_for(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path in ("", "/", "/index.php"):
        return url
    return DEFAULT_BASE_URL


def _dismiss_cookie_banner(page) -> None:
    selectors = (
        "#cookieChoiceDismiss",
        "a.cookie-consent-accept",
        "text=Accetto",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.is_visible(timeout=2000):
                locator.click(timeout=3000)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _is_challenge_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker.lower() in lowered for marker in CHALLENGE_MARKERS)


def _auction_count(page) -> int:
    return page.locator('div[id^="divAsta"]').count()


def _wait_for_auction_content(page) -> None:
    for _ in range(24):
        if _auction_count(page) > 0:
            page.wait_for_timeout(1500)
            return

        html = page.content()
        if "window.pageList" in html and not _is_challenge_page(html):
            page.wait_for_timeout(2000)
            if _auction_count(page) > 0 or "divAsta" in html:
                return

        if _is_challenge_page(html):
            page.wait_for_timeout(5000)
            continue

        try:
            page.wait_for_selector(
                'div[id^="divAsta"]',
                timeout=5000,
                state="attached",
            )
            return
        except Exception:
            page.wait_for_timeout(3000)

    title = page.title()
    snippet = page.content()[:400].replace("\n", " ")
    raise TimeoutError(
        f"Timeout: nessuna asta sulla pagina (titolo: {title!r}). "
        f"Anteprima: {snippet!r}"
    )


def _fetch_with_playwright_standalone(url: str) -> str:
    with PlaywrightFetchContext() as fetcher:
        return fetcher.get_text(url)


def parse_euro_price(text: str) -> float | None:
    match = re.search(r"(\d+[.,]\d{2})", text.replace("\xa0", " "))
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _parse_auctions_soup(html: str, base_url: str) -> list[Auction]:
    soup = BeautifulSoup(html, "html.parser")
    auctions: list[Auction] = []
    seen: set[str] = set()

    for card in soup.select("div[id^='divAsta']"):
        auction_id = card.get("data-id") or card.get("id", "").replace("divAsta", "")
        if not auction_id or auction_id in seen:
            continue

        name_el = card.select_one("a.name")
        name = name_el.get_text(strip=True) if name_el else f"Asta {auction_id}"

        slug = card.get("data-url", "")
        if not slug:
            link = card.select_one("a[href*='auction.php?a=']")
            if link and link.get("href"):
                slug = link["href"].split("a=", 1)[-1]

        retail_el = card.select_one(".reserved-price")
        if not retail_el:
            continue
        retail_value = parse_euro_price(retail_el.get_text(" ", strip=True))
        if retail_value is None:
            continue

        url = urljoin(base_url, f"auction.php?a={slug}") if slug else base_url
        auctions.append(
            Auction(
                auction_id=auction_id,
                name=name,
                slug=slug,
                retail_value=retail_value,
                url=url,
            )
        )
        seen.add(auction_id)

    return auctions


def _parse_auctions_regex(html: str, base_url: str) -> list[Auction]:
    auctions: list[Auction] = []
    seen: set[str] = set()

    for chunk in re.split(r'(?=<div id="divAsta\d+")', html):
        id_match = re.search(
            r'id="divAsta(\d+)"[^>]*data-url="([^"]*)"',
            chunk,
            re.IGNORECASE,
        )
        if not id_match:
            continue

        auction_id, slug = id_match.groups()
        if auction_id in seen:
            continue

        name_match = re.search(r'class="name[^"]*"[^>]*>([^<]+)', chunk)
        retail_match = re.search(
            r"Valore:</span>\s*([\d.,]+)\s*€",
            chunk,
            re.IGNORECASE,
        )
        if not retail_match:
            continue

        retail_value = parse_euro_price(retail_match.group(1))
        if retail_value is None:
            continue

        name = name_match.group(1).strip() if name_match else f"Asta {auction_id}"
        url = urljoin(base_url, f"auction.php?a={slug}") if slug else base_url
        auctions.append(
            Auction(
                auction_id=auction_id,
                name=name,
                slug=slug,
                retail_value=retail_value,
                url=url,
            )
        )
        seen.add(auction_id)

    return auctions


def parse_auctions_from_html(html: str, base_url: str) -> list[Auction]:
    auctions = _parse_auctions_soup(html, base_url)
    if auctions:
        return auctions
    return _parse_auctions_regex(html, base_url)


def fetch_auctions(fetch: FetchContext, base_url: str) -> list[Auction]:
    html = fetch.get_text(base_url)
    auctions = parse_auctions_from_html(html, base_url)

    if not auctions:
        title_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else "sconosciuto"
        has_page_list = "window.pageList" in html
        has_challenge = _is_challenge_page(html)
        raise ValueError(
            "Nessuna asta trovata nella pagina. "
            f"titolo={title!r}, pageList={has_page_list}, "
            f"challenge={has_challenge}. "
            "Bidoo potrebbe aver bloccato la richiesta o la pagina è cambiata."
        )

    return auctions


def parse_live_response(payload: str) -> tuple[int, list[LiveAuction]]:
    payload = payload.strip()
    if "*" not in payload:
        raise ValueError("Risposta data.php non valida")

    server_time_str, body = payload.split("*", 1)
    server_time = int(server_time_str.split("{")[0].split("|")[0])

    match = re.search(r"\((.*)\)", body)
    if not match:
        return server_time, []

    live_auctions: list[LiveAuction] = []
    for chunk in match.group(1).split("#"):
        if not chunk.strip():
            continue
        parts = chunk.split(";")
        if len(parts) < 6:
            continue
        live_auctions.append(
            LiveAuction(
                auction_id=parts[0],
                state=parts[1],
                expires_at=int(parts[2]),
                price_cents=int(parts[3]),
                winner=parts[4],
                bid_type=parts[5],
            )
        )

    return server_time, live_auctions


def fetch_live_auctions(
    fetch: FetchContext,
    base_url: str,
    auction_ids: list[str],
) -> tuple[int, list[LiveAuction]]:
    if not auction_ids:
        return 0, []

    ids_param = ",".join(auction_ids)
    url = urljoin(base_url, f"data.php?LISTID={ids_param}")
    text = fetch.get_text(url)
    return parse_live_response(text)


def seconds_remaining(server_time: int, live: LiveAuction) -> int:
    return max(0, live.expires_at - server_time)
