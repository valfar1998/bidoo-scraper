from __future__ import annotations

import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any

URLS = (
    "https://www.gobid.it/it/categorie/Elettronica/",
    "https://www.gobid.it/it/categorie/Orologeria/",
    "https://www.gobid.it/it/categorie/Gaming/",
    "https://www.gobid.it/it/categorie/Giocattoli/",
    "https://www.gobid.it/it/categorie/Abbigliamento/",
    "https://www.gobid.it/it/aste/",
)

NAV_RE = re.compile(
    r"^(home|categorie|login|accedi|registrati|contatti|privacy|cookie|aste\s*$)",
    re.I,
)
LOT_HREF = re.compile(r"/lotti?/|/auction/|/lotto/", re.I)
EURO_RE = re.compile(r"(?:€|eur)\s*[\d.,]+|[\d.,]+\s*(?:€|eur)", re.I)


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
    for nav in soup.select("nav, header, footer, .breadcrumb, .menu, #menu"):
        nav.decompose()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        if not LOT_HREF.search(href) and not ("/aste/" in href.lower() and len(anchor.get_text(strip=True)) > 20):
            continue
        title = anchor.get_text(" ", strip=True)
        if len(title) < 12 or NAV_RE.match(title):
            continue
        parent = anchor.find_parent(["article", "div", "li", "tr"])
        price = _price_from(parent) if parent is not None else _price_from(anchor)
        if price <= 0:
            continue
        listing_id = re.sub(r"\W+", "-", href)[-48:]
        blob = parent.get_text(" ", strip=True) if parent else title
        listings.append(
            SourceListing(
                source="gobid",
                listing_id=listing_id,
                title=title[:180],
                url=href if href.startswith("http") else "https://www.gobid.it" + href,
                current_price_eur=price,
                remaining_seconds=remaining_from_any(blob),
                remaining_text=blob[:80],
            )
        )
    return listings[:120]


def _price_from(node) -> float:
    if node is None:
        return 0.0
    for candidate in node.select("[class*='prezz'], [class*='price'], [class*='importo']"):
        value = parse_euro(candidate.get_text(" ", strip=True))
        if value and value > 0:
            return value
    for text in node.find_all(string=EURO_RE):
        value = parse_euro(str(text))
        if value and 1 <= value < 50_000:
            return value
    match = EURO_RE.search(node.get_text(" ", strip=True))
    if match:
        return parse_euro(match.group(0)) or 0.0
    return 0.0
