from __future__ import annotations

import os

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro

URLS = (
    "https://www.stocklots24.it/",
    "https://www.stocklots24.com/",
    "https://www.stocklots24.com/kaufen.htm",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    email = os.getenv("STOCKLOTS24_EMAIL", "").strip()
    if not email:
        print(
            "[stocklots24] Registrazione consigliata (STOCKLOTS24_EMAIL / PASSWORD). "
            "Provo comunque le vetrine pubbliche."
        )
    listings: list[SourceListing] = []
    for url in URLS:
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            print(f"[stocklots24] {exc}")
            continue
        listings.extend(_parse(html))
    return listings


def _parse(html: str) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[SourceListing] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        title = anchor.get_text(" ", strip=True)
        if len(title) < 16:
            continue
        if not any(token in href.lower() for token in ("angebot", "offer", "detail", "artikel", "lot")):
            continue
        parent = anchor.find_parent(["article", "div", "li", "tr"])
        blob = parent.get_text(" ", strip=True) if parent else title
        listings.append(
            SourceListing(
                source="stocklots24",
                listing_id=href[-48:],
                title=title[:180],
                url=href if href.startswith("http") else "https://www.stocklots24.com/" + href.lstrip("/"),
                current_price_eur=parse_euro(blob) or 0.0,
            )
        )
    return listings[:60]
