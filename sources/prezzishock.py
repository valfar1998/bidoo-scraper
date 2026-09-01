from __future__ import annotations

import requests

from http_fetch import USER_AGENT
from http_fetch import SessionFetcher
from listing import SourceListing
from php_auction import parse_php_auction_table

BASE = "https://www.prezzishock.it/"
URLS = (
    "https://www.prezzishock.it/auctions_show.php?option=ending&limit=100",
    "https://www.prezzishock.it/auctions_show.php?start=100&limit=100&option=ending",
    "https://www.prezzishock.it/auctions_show.php?start=200&limit=100&option=ending",
    "https://www.prezzishock.it/auctions_show.php?start=300&limit=100&option=ending",
)
# PrezziShock risponde con pagina vuota se inviamo Sec-Fetch-* da Chrome.
_LEGACY_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9",
}


def _get_html(url: str) -> str:
    response = requests.get(
        url,
        headers={**_LEGACY_HEADERS, "Referer": BASE},
        timeout=45,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "iso-8859-1"
    return response.text or ""


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    seen: dict[str, SourceListing] = {}
    for url in URLS:
        try:
            html = _get_html(url)
        except Exception as exc:
            print(f"[prezzishock] skip {url}: {exc}")
            continue
        for item in parse_php_auction_table(html, source="prezzishock", base_url=BASE):
            seen[item.listing_id] = item
    if not seen:
        print("[prezzishock] Catalogo vuoto o bloccato.")
    return list(seen.values())
