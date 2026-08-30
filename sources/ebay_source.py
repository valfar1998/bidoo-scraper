from __future__ import annotations

import os
from urllib.parse import quote_plus

from http_fetch import SessionFetcher
from listing import SourceListing
from money import remaining_from_any

FINDING_URL = "https://svcs.ebay.com/services/search/FindingService/v1"
DEFAULT_KEYWORDS = (
    "lotto stock",
    "rimanenza negozio",
    "blocco abbigliamento",
    "svuota magazzino",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    app_id = os.getenv("EBAY_APP_ID", "").strip()
    if not app_id:
        print(
            "[ebay_source] Serve EBAY_APP_ID (gratis su developer.ebay.com → Application Keyset). "
            "Senza chiave non leggo le aste."
        )
        return []

    keywords = [
        item.strip()
        for item in os.getenv("EBAY_SOURCE_KEYWORDS", ",".join(DEFAULT_KEYWORDS)).split(",")
        if item.strip()
    ]
    listings: list[SourceListing] = []
    for query in keywords:
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
            print(f"[ebay_source] {query}: {exc}")
            continue
        listings.extend(_parse(data))
    return _dedupe(listings)


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
