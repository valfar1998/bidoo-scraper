from __future__ import annotations

import os
import re
from urllib.parse import quote_plus

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any
from sources.ebay_api import credentials, search_auctions
from sources.queries import VINTED_FLIP_QUERIES, env_queries

_ITEM_RE = re.compile(
    r'href="(https://www\.ebay\.it/itm/(\d+)[^"]*)"[^>]*>.*?s-item__title[^>]*>(?:<!--.*?-->)?([^<]{8,160})',
    re.I | re.S,
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    app_id, cert = credentials()
    keywords = env_queries("EBAY_SOURCE_KEYWORDS", VINTED_FLIP_QUERIES)[:12]
    per_query = int(os.getenv("EBAY_RESULTS_PER_QUERY", "30"))
    listings: list[SourceListing] = []
    if app_id and cert:
        for query in keywords:
            listings.extend(_from_browse(query, limit=per_query))
    elif app_id:
        print(
            "[ebay_source] EBAY_CERT_ID mancante: aggiungi il Client Secret "
            "(Cert ID) nel .env per la Browse API."
        )
        fetcher.warm("https://www.ebay.it/")
        for query in keywords:
            listings.extend(_from_html(fetcher, query))
    else:
        print("[ebay_source] Senza EBAY_APP_ID uso la ricerca HTML (aste in chiusura).")
        fetcher.warm("https://www.ebay.it/")
        for query in keywords:
            listings.extend(_from_html(fetcher, query))
    found = _dedupe(listings)
    if not found:
        print("[ebay_source] Nessuna asta letta (403 da cloud o credenziali eBay errate).")
    return found


def _from_browse(query: str, *, limit: int) -> list[SourceListing]:
    try:
        items = search_auctions(query, limit=limit)
    except Exception as exc:
        print(f"[ebay_source] Browse API {query}: {exc}")
        return []
    listings: list[SourceListing] = []
    for item in items:
        listing = _browse_item_to_listing(item)
        if listing:
            listings.append(listing)
    return listings


def _browse_item_to_listing(item: dict) -> SourceListing | None:
    raw_id = str(item.get("itemId") or "")
    listing_id = _legacy_item_id(raw_id)
    if not listing_id:
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    url = str(item.get("itemWebUrl") or f"https://www.ebay.it/itm/{listing_id}")
    price_block = item.get("currentBidPrice") or item.get("price") or {}
    try:
        price = float(price_block.get("value") or 0)
    except (TypeError, ValueError):
        price = parse_euro(str(price_block)) or 0.0
    ship_cost = 0.0
    for option in item.get("shippingOptions") or []:
        cost = (option or {}).get("shippingCost") or {}
        try:
            ship_cost = float(cost.get("value") or 0)
        except (TypeError, ValueError):
            ship_cost = 0.0
        if ship_cost:
            break
    try:
        bids = int(item.get("bidCount") or 0)
    except (TypeError, ValueError):
        bids = 0
    end_raw = str(item.get("itemEndDate") or "")
    remaining = remaining_from_any(end_raw)
    image = item.get("image") or item.get("thumbnailImages") or {}
    if isinstance(image, list):
        image = image[0] if image else {}
    image_url = str((image or {}).get("imageUrl") or "")
    condition = str(item.get("condition") or item.get("conditionId") or "")
    extra = {
        "image_url": image_url,
        "has_image": bool(image_url),
        "condition": condition,
        "ships": True,
    }
    return SourceListing(
        source="ebay_source",
        listing_id=listing_id,
        title=title[:180],
        url=url,
        current_price_eur=price,
        shipping_eur=ship_cost,
        bids=bids,
        remaining_text=end_raw,
        remaining_seconds=remaining,
        extra=extra,
    )


def _legacy_item_id(item_id: str) -> str:
    parts = item_id.split("|")
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    digits = re.search(r"(\d{9,})", item_id)
    return digits.group(1) if digits else item_id


def _from_html(fetcher: SessionFetcher, query: str) -> list[SourceListing]:
    max_price = float(os.getenv("EBAY_MAX_PRICE", "120"))
    url = (
        "https://www.ebay.it/sch/i.html?_nkw="
        f"{quote_plus(query)}&LH_Auction=1&_sop=1&rt=nc&_ipg=60&LH_PrefLoc=3"
        f"&_udhi={int(max_price)}"
    )
    try:
        html = fetcher.get_text(url, referer="https://www.ebay.it/")
    except Exception as exc:
        print(f"[ebay_source] HTML {query}: {exc}")
        return []
    listings: list[SourceListing] = []
    chunks = re.split(r"s-item__link", html)
    for chunk in chunks[1:]:
        id_match = re.search(r"/itm/(\d+)", chunk)
        title_match = re.search(r"s-item__title[^>]*>(?:<span[^>]*>)?([^<]{8,160})", chunk)
        if not id_match:
            continue
        item_id = id_match.group(1)
        title = (title_match.group(1) if title_match else "").strip()
        if not title or title.lower().startswith("shop on ebay"):
            continue
        price = parse_euro(chunk[:800]) or 0.0
        time_match = re.search(r"(\d+\s*(?:g|h|m|d|giorn|ore|min)[^<]{0,20})", chunk, re.I)
        remaining_text = time_match.group(1) if time_match else ""
        listings.append(
            SourceListing(
                source="ebay_source",
                listing_id=item_id,
                title=title[:180],
                url=f"https://www.ebay.it/itm/{item_id}",
                current_price_eur=price,
                shipping_eur=8.0,
                remaining_text=remaining_text,
                remaining_seconds=remaining_from_any(remaining_text),
                extra={"ships": True},
            )
        )
    return listings[:40]


def _dedupe(items: list[SourceListing]) -> list[SourceListing]:
    seen: dict[str, SourceListing] = {}
    for item in items:
        seen[item.listing_id] = item
    return list(seen.values())
