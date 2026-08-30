from __future__ import annotations

import os
import re
from urllib.parse import quote_plus

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any
from sources.queries import DEFAULT_FLIP_QUERIES

FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
DEFAULT_KEYWORDS = DEFAULT_FLIP_QUERIES + (
    "lotto stock",
    "rimanenza negozio",
)

_ITEM_RE = re.compile(
    r'href="(https://www\.ebay\.it/itm/(\d+)[^"]*)"[^>]*>.*?s-item__title[^>]*>(?:<!--.*?-->)?([^<]{8,160})',
    re.I | re.S,
)
_PRICE_RE = re.compile(r's-item__price[^>]*>([^<]{2,40})', re.I)
_TIME_RE = re.compile(r's-item__time-(?:left|end)[^>]*>([^<]{2,40})', re.I)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    app_id = os.getenv("EBAY_APP_ID", "").strip()
    keywords = [
        item.strip()
        for item in os.getenv("EBAY_SOURCE_KEYWORDS", ",".join(DEFAULT_KEYWORDS)).split(",")
        if item.strip()
    ][:8]
    fetcher.warm("https://www.ebay.it/")
    listings: list[SourceListing] = []
    if app_id:
        for query in keywords:
            listings.extend(_from_api(fetcher, app_id, query))
    else:
        print("[ebay_source] Senza EBAY_APP_ID uso la ricerca HTML (aste in chiusura).")
        for query in keywords:
            listings.extend(_from_html(fetcher, query))
    found = _dedupe(listings)
    if not found:
        print("[ebay_source] Nessuna asta letta (403 da cloud possibile).")
    return found


def _from_api(fetcher: SessionFetcher, app_id: str, query: str) -> list[SourceListing]:
    params = {
        "OPERATION-NAME": "findItemsAdvanced",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "",
        "GLOBAL-ID": os.getenv("EBAY_GLOBAL_ID", "EBAY-IT"),
        "keywords": query,
        "paginationInput.entriesPerPage": "25",
        "itemFilter(0).name": "ListingType",
        "itemFilter(0).value(0)": "Auction",
        "itemFilter(0).value(1)": "AuctionWithBIN",
        "sortOrder": "EndTimeSoonest",
    }
    url = FINDING_URL + "?" + "&".join(
        f"{quote_plus(key)}={quote_plus(str(value))}" for key, value in params.items()
    )
    try:
        data = fetcher.get_json(url)
    except Exception as exc:
        print(f"[ebay_source] API {query}: {exc}")
        return []
    return _parse(data)


def _from_html(fetcher: SessionFetcher, query: str) -> list[SourceListing]:
    url = (
        "https://www.ebay.it/sch/i.html?_nkw="
        f"{quote_plus(query)}&LH_Auction=1&_sop=1&rt=nc&_ipg=60&LH_PrefLoc=3"
    )
    try:
        html = fetcher.get_text(url, referer="https://www.ebay.it/")
    except Exception as exc:
        print(f"[ebay_source] HTML {query}: {exc}")
        return []
    listings: list[SourceListing] = []
    chunks = re.split(r's-item__link', html)
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
            )
        )
    return listings[:40]


def _parse(data: dict) -> list[SourceListing]:
    response = data.get("findItemsAdvancedResponse") or data.get("findItemsByKeywordsResponse") or []
    if isinstance(response, list):
        response = response[0] if response else {}
    search = (response.get("searchResult") or [{}])[0]
    items = search.get("item") or []
    listings: list[SourceListing] = []
    for item in items:
        item_id = str((item.get("itemId") or [""])[0])
        title = str((item.get("title") or [""])[0])
        url = str((item.get("viewItemURL") or [""])[0])
        selling = (item.get("sellingStatus") or [{}])[0]
        current = (selling.get("currentPrice") or [{}])[0]
        price = float(current.get("__value__") or 0)
        shipping = item.get("shippingInfo") or [{}]
        ship_cost = 0.0
        if shipping:
            amount = (shipping[0].get("shippingServiceCost") or [{}])[0]
            try:
                ship_cost = float(amount.get("__value__") or 0)
            except (TypeError, ValueError):
                ship_cost = 0.0
        bids_raw = selling.get("bidCount") or ["0"]
        try:
            bids = int(bids_raw[0])
        except (TypeError, ValueError, IndexError):
            bids = 0
        listing_info = (item.get("listingInfo") or [{}])[0]
        end_raw = listing_info.get("endTime")
        if isinstance(end_raw, list):
            end_raw = end_raw[0] if end_raw else ""
        remaining = remaining_from_any(end_raw)
        listings.append(
            SourceListing(
                source="ebay_source",
                listing_id=item_id,
                title=title,
                url=url,
                current_price_eur=price,
                shipping_eur=ship_cost,
                bids=bids,
                remaining_text=str(end_raw or ""),
                remaining_seconds=remaining,
            )
        )
    return listings


def _dedupe(items: list[SourceListing]) -> list[SourceListing]:
    seen: dict[str, SourceListing] = {}
    for item in items:
        seen[item.listing_id] = item
    return list(seen.values())
