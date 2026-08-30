from __future__ import annotations

import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any

URLS = (
    "https://www.gobid.it/it/aste/",
    "https://www.gobid.it/it/categorie/Abbigliamento/",
    "https://www.gobid.it/it/categorie/Varie/",
    "https://www.gobid.it/it/categorie/Gaming/",
    "https://www.gobid.it/it/categorie/Elettronica/",
    "https://www.gobid.it/it/categorie/Orologeria/",
    "https://www.gobid.it/it/categorie/Giocattoli/",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    fetcher.warm("https://www.gobid.it/it/")
    seen: dict[str, SourceListing] = {}
    fails = 0
    for url in URLS:
        try:
            html = fetcher.get_text(url, referer="https://www.gobid.it/it/")
        except Exception as exc:
            print(f"[gobid] {url}: {exc}")
            fails += 1
            if fails >= 2:
                print("[gobid] WAF ripetuto: stop altre categorie, passo oltre.")
                break
            continue
        fails = 0
        for item in _parse(html):
            seen[item.listing_id] = item
    if not seen:
        print(
            "[gobid] Catalogo vuoto (WAF). Su GitHub cloud è frequente. "
            "Da casa/self-hosted con Playwright può funzionare."
        )
    return list(seen.values())


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
        remaining = remaining_from_any(blob)
        listings.append(
            SourceListing(
                source="gobid",
                listing_id=listing_id,
                title=title[:180],
                url=href if href.startswith("http") else "https://www.gobid.it" + href,
                current_price_eur=price or 0.0,
                remaining_seconds=remaining,
                remaining_text=blob[:80] if remaining else "",
            )
        )
    return listings[:120]
