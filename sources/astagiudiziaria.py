from __future__ import annotations

import os
import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any

URLS = (
    "https://www.astagiudiziaria.com/beni-mobili/abbigliamento-e-calzature",
    "https://www.astagiudiziaria.com/beni-mobili/arredamento-e-elettrodomestici",
    "https://www.astagiudiziaria.com/beni-mobili/informatica-ed-elettronica",
    "https://www.astagiudiziaria.com/beni-mobili/hobby-e-collezionismo",
    "https://www.astagiudiziaria.com/beni-mobili/giocattoli-e-modellismo",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    extra = [u.strip() for u in os.getenv("ASTAGIUDIZIARIA_URLS", "").split(",") if u.strip()]
    seen: dict[str, SourceListing] = {}
    fetcher.warm("https://www.astagiudiziaria.com/")
    for url in extra or URLS:
        try:
            html = fetcher.get_text(url, referer="https://www.astagiudiziaria.com/")
        except Exception as exc:
            print(f"[astagiudiziaria] {url}: {exc}")
            html = ""
        for item in _parse(html):
            seen[item.listing_id] = item
    if not seen:
        print(
            "[astagiudiziaria] Catalogo JS vuoto. "
            "Prova USE_PLAYWRIGHT=true. MyAsta è gratis per alert email."
        )
    return list(seen.values())


def _parse(html: str) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    for nav in soup.select("nav, header, footer, .breadcrumb"):
        nav.decompose()
    listings: list[SourceListing] = []
    for script in soup.select('script[type="application/ld+json"]'):
        listings.extend(_from_ldjson(script.string or ""))
    if listings:
        return listings[:80]
    for card in soup.select("a[href]"):
        href = card.get("href") or ""
        title = card.get_text(" ", strip=True)
        if len(title) < 12:
            continue
        if not any(token in href.lower() for token in ("bene", "avviso", "dettaglio", "/p/", "scheda", "lotto")):
            continue
        parent = card.find_parent(["article", "div", "li", "tr"])
        blob = parent.get_text(" ", strip=True) if parent else title
        price = parse_euro(blob) or parse_euro(title)
        if not price:
            continue
        listing_id = re.sub(r"\W+", "-", href)[-40:]
        listings.append(
            SourceListing(
                source="astagiudiziaria",
                listing_id=listing_id,
                title=title[:180],
                url=href if href.startswith("http") else "https://www.astagiudiziaria.com" + href,
                current_price_eur=price,
                remaining_seconds=remaining_from_any(blob),
            )
        )
    return listings[:80]


def _from_ldjson(raw: str) -> list[SourceListing]:
    import json

    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    found: list[SourceListing] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("headline") or "").strip()
        url = str(item.get("url") or "")
        offers = item.get("offers") or {}
        price = 0.0
        if isinstance(offers, dict):
            try:
                price = float(offers.get("price") or 0)
            except (TypeError, ValueError):
                price = parse_euro(str(offers.get("price") or "")) or 0.0
        if not title or not url or price <= 0:
            continue
        listing_id = re.sub(r"\W+", "-", url)[-40:]
        found.append(
            SourceListing(
                source="astagiudiziaria",
                listing_id=listing_id,
                title=title[:180],
                url=url,
                current_price_eur=price,
            )
        )
    return found
