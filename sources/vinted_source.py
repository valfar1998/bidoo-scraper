"""Vinted.it come FONTE acquisto (sotto-prezzo → rivendi su eBay)."""

from __future__ import annotations

import json
import re

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro
from sources.queries import env_queries

CATALOG = "https://www.vinted.it/catalog"
API = "https://www.vinted.it/api/v2/catalog/items"


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    fetcher.warm("https://www.vinted.it/")
    queries = env_queries("VINTED_SOURCE_QUERIES")
    seen: dict[str, SourceListing] = {}
    for query in queries[:6]:
        items = _search(fetcher, query)
        if items is None:
            print("[vinted_source] WAF: stop altre keyword.")
            break
        for item in items:
            seen[item.listing_id] = item
    if not seen:
        print(
            "[vinted_source] Nessun annuncio. Da cloud spesso 403; da casa di solito ok."
        )
    return list(seen.values())[:80]


def _search(fetcher: SessionFetcher, query: str) -> list[SourceListing] | None:
    try:
        data = fetcher.get_json(
            API,
            params={
                "page": "1",
                "per_page": "24",
                "search_text": query,
                "order": "newest_first",
                "price_from": "15",
                "price_to": "60",
            },
            extra_headers={
                "Accept": "application/json",
                "Referer": f"{CATALOG}?search_text={query}",
            },
        )
        if isinstance(data, dict):
            items = _from_api(data)
            if items:
                return items
    except Exception as exc:
        print(f"[vinted_source] API {query}: {exc}")
        html = _html(fetcher, query)
        if not html:
            return None
        parsed = _from_html(html)
        return parsed or None
    html = _html(fetcher, query)
    return _from_html(html) if html else []


def _html(fetcher: SessionFetcher, query: str) -> str:
    url = f"{CATALOG}?search_text={query}&order=newest_first"
    try:
        return fetcher.get_text(url, referer="https://www.vinted.it/")
    except Exception as exc:
        print(f"[vinted_source] HTML {query}: {exc}")
        return ""


def _from_api(data: dict) -> list[SourceListing]:
    listings: list[SourceListing] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        item_id = str(item.get("id") or "")
        if not title or not item_id:
            continue
        price = _price(item)
        if price < 15 or price > 60:
            continue
        url = str(item.get("url") or f"https://www.vinted.it/items/{item_id}")
        if url.startswith("/"):
            url = "https://www.vinted.it" + url
        listings.append(
            SourceListing(
                source="vinted_source",
                listing_id=item_id,
                title=title[:180],
                url=url,
                current_price_eur=price,
                shipping_eur=5.0,
                extra={"ships": True, "kind": "classified"},
            )
        )
    return listings


def _price(item: dict) -> float:
    for key in ("price", "total_item_price", "discount_price"):
        raw = item.get(key)
        if isinstance(raw, dict):
            amount = raw.get("amount") or raw.get("numeric")
            try:
                return float(str(amount).replace(",", "."))
            except (TypeError, ValueError):
                continue
        if raw not in (None, ""):
            parsed = parse_euro(str(raw))
            if parsed:
                return parsed
            try:
                return float(str(raw).replace(",", "."))
            except ValueError:
                continue
    return 0.0


def _from_html(html: str) -> list[SourceListing]:
    listings: list[SourceListing] = []
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if match:
        try:
            payload = json.loads(match.group(1))
            blob = json.dumps(payload)
            for item_id, title in re.findall(
                r'"id"\s*:\s*(\d{5,}).{0,180}"title"\s*:\s*"([^"]{8,120})"',
                blob,
            ):
                listings.append(
                    SourceListing(
                        source="vinted_source",
                        listing_id=item_id,
                        title=title[:180],
                        url=f"https://www.vinted.it/items/{item_id}",
                        current_price_eur=0.0,
                        shipping_eur=5.0,
                        extra={"ships": True, "kind": "classified"},
                    )
                )
        except json.JSONDecodeError:
            pass
    if listings:
        return listings[:40]
    for item_id, title in re.findall(
        r'href="/items/(\d+)[^"]*"[^>]*>\s*([^<]{8,120})',
        html,
    ):
        listings.append(
            SourceListing(
                source="vinted_source",
                listing_id=item_id,
                title=title.strip()[:180],
                url=f"https://www.vinted.it/items/{item_id}",
                current_price_eur=0.0,
                shipping_eur=5.0,
                extra={"ships": True, "kind": "classified"},
            )
        )
    return listings[:40]
