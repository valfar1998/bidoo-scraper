from __future__ import annotations

import os

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro

# Marketplace europeo; il catalogo pieno è dietro login.
URLS = (
    "https://bstock.com/",
    "https://europe.bstock.com/",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    email = os.getenv("BSTOCK_EMAIL", "").strip()
    password = os.getenv("BSTOCK_PASSWORD", "").strip()
    if not email or not password:
        print(
            "[bstock] Servono BSTOCK_EMAIL e BSTOCK_PASSWORD nel .env "
            "(account gratuito su bstock.com). Catalogo pallet non è pubblico."
        )
        return []

    listings: list[SourceListing] = []
    for url in URLS:
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            print(f"[bstock] {exc}")
            continue
        listings.extend(_parse(html))
    if not listings:
        print("[bstock] Login presente ma catalogo non parsato (pagine dinamiche). Apri europe.bstock.com a mano.")
    return listings


def _parse(html: str) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[SourceListing] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        title = anchor.get_text(" ", strip=True)
        if "auction" not in href.lower() and "lot" not in href.lower():
            continue
        if len(title) < 12:
            continue
        parent = anchor.find_parent(["article", "div", "li"])
        blob = parent.get_text(" ", strip=True) if parent else title
        listings.append(
            SourceListing(
                source="bstock",
                listing_id=href[-48:],
                title=title[:180],
                url=href if href.startswith("http") else "https://bstock.com" + href,
                current_price_eur=parse_euro(blob) or 0.0,
            )
        )
    return listings[:60]
