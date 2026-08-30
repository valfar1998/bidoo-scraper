from __future__ import annotations

import json
import os
import re

from http_fetch import SessionFetcher, fetch_with_playwright
from listing import SourceListing
from money import parse_euro

SEARCH_URLS = (
    "https://www.catawiki.com/it/s?q=orologio+casio&sort=ending_soon",
    "https://www.catawiki.com/it/s?q=borsa&sort=ending_soon",
    "https://www.catawiki.com/it/s?q=sneaker&sort=ending_soon",
    "https://www.catawiki.com/it/s?q=profumo&sort=ending_soon",
    "https://www.catawiki.com/it/s?q=lampada+design&sort=ending_soon",
)

LOT_RE = re.compile(
    r'href="(https://www\.catawiki\.com/it/l/(\d+)[^"]*)"[^>]*>\s*([^<]{8,160})',
    re.I,
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    queries = [
        item.strip()
        for item in os.getenv("CATAWIKI_QUERIES", "").split(",")
        if item.strip()
    ]
    urls = [f"https://www.catawiki.com/it/s?q={q}&sort=ending_soon" for q in queries]
    if not urls:
        urls = list(SEARCH_URLS)

    seen: dict[str, SourceListing] = {}
    for url in urls:
        html = _load(fetcher, url)
        for item in _parse(html):
            seen[item.listing_id] = item
    if not seen:
        print(
            "[catawiki] Nessun lotto letto (Akamai). "
            "Imposta USE_PLAYWRIGHT=true e lancia da casa."
        )
    return list(seen.values())


def _load(fetcher: SessionFetcher, url: str) -> str:
    try:
        return fetcher.get_text(url)
    except Exception:
        try:
            return fetch_with_playwright(url)
        except Exception as exc:
            print(f"[catawiki] {exc}")
            return ""


def _parse(html: str) -> list[SourceListing]:
    listings: list[SourceListing] = []
    if "__NEXT_DATA__" in html:
        listings.extend(_from_next_data(html))
    if listings:
        return listings
    for match in LOT_RE.finditer(html):
        url, lot_id, title = match.group(1), match.group(2), match.group(3).strip()
        listings.append(
            SourceListing(
                source="catawiki",
                listing_id=lot_id,
                title=title,
                url=url.split("?")[0],
                current_price_eur=0.0,
            )
        )
    return listings


def _from_next_data(html: str) -> list[SourceListing]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    blobs = json.dumps(payload)
    listings: list[SourceListing] = []
    for lot in _walk_lots(payload):
        listings.append(lot)
    if listings:
        return listings
    # fallback ids in JSON
    for lot_id, title in re.findall(r'"id":\s*(\d{5,}).{0,200}"title":\s*"([^"]{8,120})"', blobs):
        listings.append(
            SourceListing(
                source="catawiki",
                listing_id=str(lot_id),
                title=title,
                url=f"https://www.catawiki.com/it/l/{lot_id}",
                current_price_eur=0.0,
            )
        )
    return listings[:80]


def _walk_lots(node: object) -> list[SourceListing]:
    found: list[SourceListing] = []
    if isinstance(node, dict):
        if "current_bid" in node or "highest_bid" in node or "bidding_end_time" in node:
            lot = _lot_from_dict(node)
            if lot:
                found.append(lot)
        for value in node.values():
            found.extend(_walk_lots(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_lots(item))
    return found


def _money(value: object) -> float:
    if isinstance(value, dict):
        amount = value.get("amount") or value.get("cents") or value.get("value")
        if isinstance(amount, (int, float)) and amount > 500:
            return float(amount) / 100
        if isinstance(amount, (int, float)):
            return float(amount)
        text = str(value.get("formatted") or "")
        return parse_euro(text) or parse_euro(str(value)) or 0.0
    if isinstance(value, (int, float)):
        return float(value) / 100 if value > 500 else float(value)
    return parse_euro(str(value)) or 0.0


def _lot_from_dict(node: dict) -> SourceListing | None:
    lot_id = str(node.get("id") or node.get("lot_id") or "")
    title = str(node.get("title") or node.get("name") or "").strip()
    if not lot_id or not title:
        return None
    bid = node.get("current_bid") or node.get("highest_bid") or node.get("start_bid") or 0
    estimate = node.get("estimated_price") or node.get("estimate") or {}
    retail = 0.0
    if isinstance(estimate, dict):
        retail = _money(estimate.get("max") or estimate.get("high") or estimate)
    slug = node.get("url") or node.get("slug") or f"/it/l/{lot_id}"
    url = slug if str(slug).startswith("http") else f"https://www.catawiki.com{slug}"
    return SourceListing(
        source="catawiki",
        listing_id=lot_id,
        title=title,
        url=url,
        current_price_eur=_money(bid),
        retail_hint_eur=retail,
    )
