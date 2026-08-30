"""Subito.it: annunci con spedizione (usato / elettronica / casa)."""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro
from sources.queries import env_queries

SEARCH = "https://www.subito.it/annunci-italia/vendita/usato/"


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    fetcher.warm("https://www.subito.it/")
    queries = env_queries("SUBITO_QUERIES")
    seen: dict[str, SourceListing] = {}
    for query in queries[:6]:
        html = _load(fetcher, query)
        if not html:
            print("[subito] WAF: stop altre keyword.")
            break
        for item in _parse(html):
            seen[item.listing_id] = item
    if not seen:
        print(
            "[subito] Nessun annuncio (Akamai da cloud è frequente). "
            "Da PC di casa di solito risponde."
        )
    return list(seen.values())[:80]


def _load(fetcher: SessionFetcher, query: str) -> str:
    url = f"{SEARCH}?q={query}&shp=true"
    try:
        return fetcher.get_text(url, referer="https://www.subito.it/")
    except Exception as exc:
        print(f"[subito] {query}: {exc}")
        return ""


def _parse(html: str) -> list[SourceListing]:
    listings: list[SourceListing] = []
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            listings.extend(_from_next(payload))
        except json.JSONDecodeError:
            pass
    if listings:
        return listings
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("a[href*='.htm']"):
        href = card.get("href") or ""
        title = card.get_text(" ", strip=True)
        if len(title) < 12 or "subito.it" not in href and not href.endswith(".htm"):
            continue
        parent = card.find_parent(["article", "div", "li"])
        blob = parent.get_text(" ", strip=True) if parent else title
        if "spediz" not in blob.lower() and "shp" not in href:
            continue
        price = parse_euro(blob) or 0.0
        if price and (price < 15 or price > 80):
            continue
        listing_id = re.sub(r"\W+", "-", href)[-48:]
        url = href if href.startswith("http") else "https://www.subito.it" + href
        listings.append(
            SourceListing(
                source="subito",
                listing_id=listing_id,
                title=title[:180],
                url=url,
                current_price_eur=price,
                shipping_eur=7.0,
                extra={"ships": True, "kind": "classified"},
            )
        )
    return listings[:40]


def _from_next(node: object) -> list[SourceListing]:
    found: list[SourceListing] = []
    if isinstance(node, dict):
        if node.get("subject") and (node.get("urn") or node.get("id")):
            item = _item_from_dict(node)
            if item:
                found.append(item)
        for value in node.values():
            found.extend(_from_next(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_from_next(item))
    return found


def _item_from_dict(node: dict) -> SourceListing | None:
    title = str(node.get("subject") or node.get("title") or "").strip()
    item_id = str(node.get("urn") or node.get("id") or "")
    if not title or not item_id:
        return None
    price = 0.0
    features = node.get("features") or {}
    if isinstance(features, dict):
        money = (features.get("/price") or {}).get("values") or []
        if money:
            try:
                price = float(money[0].get("key") or money[0].get("value") or 0)
            except (TypeError, ValueError, AttributeError):
                price = parse_euro(str(money[0])) or 0.0
    if not price:
        price = parse_euro(str(node.get("price") or "")) or 0.0
    if price and (price < 15 or price > 80):
        return None
    urls = node.get("urls") or {}
    href = str(urls.get("default") or node.get("url") or "")
    if href and not href.startswith("http"):
        href = "https://www.subito.it" + href
    ships = True
    if isinstance(features, dict) and features.get("/shippable"):
        ships = True
    geo = node.get("geo") or {}
    city = ""
    if isinstance(geo, dict):
        city = str((geo.get("city") or {}).get("value") or geo.get("town") or "")
    return SourceListing(
        source="subito",
        listing_id=item_id[-48:],
        title=title[:180],
        url=href or f"https://www.subito.it/annunci/{item_id}/",
        current_price_eur=price,
        shipping_eur=7.0 if ships else 0.0,
        location=city,
        extra={"ships": ships, "kind": "classified"},
    )
