from __future__ import annotations

import os

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro

URLS = (
    "https://merkandi.com/",
    "https://www.merkandi.com/en/",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    email = os.getenv("MERKANDI_EMAIL", "").strip()
    password = os.getenv("MERKANDI_PASSWORD", "").strip()
    if not email or not password:
        print(
            "[merkandi] Serve abbonamento + MERKANDI_EMAIL / MERKANDI_PASSWORD. "
            "Senza login i prezzi B2B non sono visibili."
        )
        return []
    listings: list[SourceListing] = []
    for url in URLS:
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            print(f"[merkandi] {exc}")
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
        if not any(token in href.lower() for token in ("offer", "offer-", "product", "lot")):
            continue
        parent = anchor.find_parent(["article", "div", "li"])
        blob = parent.get_text(" ", strip=True) if parent else title
        listings.append(
            SourceListing(
                source="merkandi",
                listing_id=href[-48:],
                title=title[:180],
                url=href if href.startswith("http") else "https://merkandi.com" + href,
                current_price_eur=parse_euro(blob) or 0.0,
            )
        )
    return listings[:60]
