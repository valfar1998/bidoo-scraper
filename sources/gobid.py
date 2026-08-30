from __future__ import annotations

import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher, fetch_with_playwright
from listing import SourceListing
from money import parse_euro

URLS = (
    "https://www.gobid.it/it/aste/",
    "https://www.gobid.it/it/categorie/Abbigliamento/",
    "https://www.gobid.it/it/categorie/Varie/",
    "https://www.gobid.it/it/categorie/Gaming/",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    seen: dict[str, SourceListing] = {}
    for url in URLS:
        html = _load(fetcher, url)
        for item in _parse(html):
            seen[item.listing_id] = item
    if not seen:
        print("[gobid] WAF/403. USE_PLAYWRIGHT=true da casa. Serve registrazione + cauzione per offrire.")
    return list(seen.values())


def _load(fetcher: SessionFetcher, url: str) -> str:
    try:
        return fetcher.get_text(url)
    except Exception:
        try:
            return fetch_with_playwright(url)
        except Exception as exc:
            print(f"[gobid] {exc}")
            return ""


def _parse(html: str) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[SourceListing] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        title = anchor.get_text(" ", strip=True)
        if "/lotti/" not in href and "/lotto/" not in href and "/auction/" not in href.lower():
            if "/aste/" not in href.lower() or len(title) < 16:
                continue
        if len(title) < 12:
            continue
        parent = anchor.find_parent(["article", "div", "li", "tr"])
        blob = parent.get_text(" ", strip=True) if parent else title
        price = parse_euro(blob) or 0.0
        listing_id = re.sub(r"\W+", "-", href)[-48:]
        listings.append(
            SourceListing(
                source="gobid",
                listing_id=listing_id,
                title=title[:180],
                url=href if href.startswith("http") else "https://www.gobid.it" + href,
                current_price_eur=price or 0.0,
            )
        )
    return listings[:80]
