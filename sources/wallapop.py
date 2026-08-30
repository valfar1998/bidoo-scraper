"""Wallapop Italia: annunci con spedizione (usato da rivendere)."""

from __future__ import annotations

import json
import re

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro
from sources.queries import env_queries

API = "https://api.wallapop.com/api/v3/general/search"
SEARCH = "https://it.wallapop.com/search"
# Milano: raggio nazionale di fatto con distance alto.
LAT, LON = "45.4642", "9.1900"


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    fetcher.warm("https://it.wallapop.com/")
    queries = env_queries("WALLAPOP_QUERIES")
    seen: dict[str, SourceListing] = {}
    for query in queries[:6]:
        items = _search(fetcher, query)
        if items is None:
            print("[wallapop] WAF: stop altre keyword (inutile su GitHub cloud).")
            break
        for item in items:
            seen[item.listing_id] = item
    if not seen:
        print(
            "[wallapop] Nessun annuncio (403/WAF da cloud è frequente). "
            "Da PC di casa di solito risponde."
        )
    return list(seen.values())[:80]


def _search(fetcher: SessionFetcher, query: str) -> list[SourceListing] | None:
    try:
        data = fetcher.get_json(
            API,
            params={
                "keywords": query,
                "latitude": LAT,
                "longitude": LON,
                "order_by": "newest",
                "min_sale_price": "15",
                "max_sale_price": "80",
                "filters_source": "search_box",
                "distance_in_km": "800",
            },
            extra_headers={
                "Accept": "application/json",
                "Origin": "https://it.wallapop.com",
                "Referer": "https://it.wallapop.com/",
                "X-DeviceOS": "0",
            },
        )
        if isinstance(data, dict):
            found = _from_api(data)
            if found:
                return found
    except Exception as exc:
        print(f"[wallapop] API {query}: {exc}")
        html = _html(fetcher, query)
        if not html:
            return None
        parsed = _from_html(html)
        return parsed or None
    html = _html(fetcher, query)
    if not html:
        return []
    return _from_html(html)


def _html(fetcher: SessionFetcher, query: str) -> str:
    url = (
        f"{SEARCH}?keywords={query}&latitude={LAT}&longitude={LON}"
        f"&order_by=newest&filters_source=search_box"
    )
    try:
        return fetcher.get_text(url, referer="https://it.wallapop.com/")
    except Exception as exc:
        print(f"[wallapop] HTML {query}: {exc}")
        return ""


def _from_api(data: dict) -> list[SourceListing]:
    listings: list[SourceListing] = []
    objects = data.get("search_objects") or data.get("items") or []
    for raw in objects:
        item = raw.get("content") if isinstance(raw.get("content"), dict) else raw
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        item_id = str(item.get("id") or item.get("item_id") or "")
        if not title or not item_id:
            continue
        price_obj = item.get("price") or {}
        if isinstance(price_obj, dict):
            amount = price_obj.get("amount") or price_obj.get("cash") or 0
            try:
                price = float(amount)
            except (TypeError, ValueError):
                price = parse_euro(str(amount)) or 0.0
        else:
            price = float(price_obj or 0)
        if price < 15 or price > 80:
            continue
        slug = str(item.get("web_slug") or item.get("slug") or item_id)
        ships = False
        ship = item.get("shipping")
        if isinstance(ship, dict):
            ships = bool(
                ship.get("item_is_shippable") or ship.get("user_allows_shipping")
            )
        elif ship:
            ships = True
        ships = ships or bool(item.get("shipping_allowed"))
        location = ""
        loc = item.get("location") or {}
        if isinstance(loc, dict):
            location = str(loc.get("city") or loc.get("zip") or "")
        listings.append(
            SourceListing(
                source="wallapop",
                listing_id=item_id,
                title=title[:180],
                url=f"https://it.wallapop.com/item/{slug}",
                current_price_eur=price,
                shipping_eur=6.0 if ships else 0.0,
                location=location,
                extra={"ships": ships, "kind": "classified"},
            )
        )
    return listings


def _from_html(html: str) -> list[SourceListing]:
    listings: list[SourceListing] = []
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            listings.extend(_walk_items(payload))
        except json.JSONDecodeError:
            pass
    if listings:
        return listings
    for slug, title in re.findall(
        r'href="https://it\.wallapop\.com/item/([^"]+)"[^>]*>\s*([^<]{8,120})',
        html,
    ):
        listings.append(
            SourceListing(
                source="wallapop",
                listing_id=slug[:48],
                title=title.strip()[:180],
                url=f"https://it.wallapop.com/item/{slug}",
                current_price_eur=0.0,
                extra={"ships": True, "kind": "classified"},
            )
        )
    return listings[:40]


def _walk_items(node: object) -> list[SourceListing]:
    found: list[SourceListing] = []
    if isinstance(node, dict):
        if node.get("title") and (node.get("id") or node.get("item_id")):
            wrapped = _from_api({"search_objects": [node]})
            found.extend(wrapped)
        for value in node.values():
            found.extend(_walk_items(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_items(item))
    return found
