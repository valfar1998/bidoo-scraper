from __future__ import annotations

import os
import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher, fetch_with_playwright
from listing import SourceListing
from money import parse_euro

URLS = (
    "https://www.astagiudiziaria.com/beni-mobili/abbigliamento-e-calzature",
    "https://www.astagiudiziaria.com/beni-mobili/arredamento-e-elettrodomestici",
    "https://www.astagiudiziaria.com/beni-mobili/informatica-ed-elettronica",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    extra = [u.strip() for u in os.getenv("ASTAGIUDIZIARIA_URLS", "").split(",") if u.strip()]
    seen: dict[str, SourceListing] = {}
    for url in extra or URLS:
        html = _load(fetcher, url)
        for item in _parse(html):
            seen[item.listing_id] = item
    if not seen:
        print(
            "[astagiudiziaria] Catalogo JS vuoto. "
            "Prova USE_PLAYWRIGHT=true. MyAsta è gratis per alert email."
        )
    return list(seen.values())


def _load(fetcher: SessionFetcher, url: str) -> str:
    try:
        html = fetcher.get_text(url)
    except Exception:
        html = ""
    if "card" in html.lower() and "€" in html:
        return html
    try:
        return fetch_with_playwright(url)
    except Exception as exc:
        print(f"[astagiudiziaria] {exc}")
        return html


def _parse(html: str) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[SourceListing] = []
    for card in soup.select("a[href]"):
        href = card.get("href") or ""
        title = card.get_text(" ", strip=True)
        if len(title) < 12:
            continue
        if not any(token in href.lower() for token in ("bene", "avviso", "dettaglio", "/p/", "scheda")):
            continue
        price = parse_euro(title) or parse_euro(card.parent.get_text(" ", strip=True) if card.parent else "")
        listing_id = re.sub(r"\W+", "-", href)[-40:]
        listings.append(
            SourceListing(
                source="astagiudiziaria",
                listing_id=listing_id,
                title=title[:180],
                url=href if href.startswith("http") else "https://www.astagiudiziaria.com" + href,
                current_price_eur=price or 0.0,
            )
        )
    return listings[:80]
