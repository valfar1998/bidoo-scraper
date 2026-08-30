from __future__ import annotations

import json
import re

from http_fetch import SessionFetcher
from listing import SourceListing

AUCTIONS_URL = "https://www.surplex.com/it/auctions"
MAX_AUCTIONS = 6
MAX_LOTS = 80


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    html = fetcher.get_text(AUCTIONS_URL)
    auctions = _auctions(html)
    listings: list[SourceListing] = []
    for auction in auctions[:MAX_AUCTIONS]:
        slug = auction.get("urlSlug") or ""
        if not slug:
            continue
        page = fetcher.get_text(f"https://www.surplex.com/it/a/{slug}")
        listings.extend(_lots(page))
        if len(listings) >= MAX_LOTS:
            break
    return listings[:MAX_LOTS]


def _next_data(html: str) -> dict:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def _auctions(html: str) -> list[dict]:
    payload = _next_data(html)
    items = payload.get("props", {}).get("pageProps", {}).get("listData") or []
    open_ones = [
        item
        for item in items
        if isinstance(item, dict) and item.get("biddingStatus") == "BIDDING_OPEN"
    ]
    return open_ones or [item for item in items if isinstance(item, dict)]


def _lots(html: str) -> list[SourceListing]:
    payload = _next_data(html)
    lots = payload.get("props", {}).get("pageProps", {}).get("lots") or {}
    results = lots.get("results") if isinstance(lots, dict) else lots
    if not isinstance(results, list):
        return []
    listings: list[SourceListing] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        if item.get("biddingStatus") not in (None, "BIDDING_OPEN"):
            continue
        amount = item.get("currentBidAmount") or {}
        cents = amount.get("cents") if isinstance(amount, dict) else 0
        price = (cents or 0) / 100
        slug = item.get("urlSlug") or ""
        loc = item.get("location") or {}
        city = loc.get("city") if isinstance(loc, dict) else ""
        country = loc.get("countryCode") if isinstance(loc, dict) else ""
        listings.append(
            SourceListing(
                source="surplex",
                listing_id=str(item.get("displayId") or item.get("id")),
                title=str(item.get("title") or ""),
                url=f"https://www.surplex.com/it/l/{slug}",
                current_price_eur=price,
                bids=int(item.get("bidsCount") or 0),
                location=f"{city} {country}".strip(),
            )
        )
    return listings
