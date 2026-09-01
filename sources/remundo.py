from __future__ import annotations

import os
import re

import requests

from http_fetch import SessionFetcher, USER_AGENT
from listing import SourceListing
from money import parse_italian_amount

PRODUCTS_URL = "https://remundo.it/products.json"
COLLECTIONS = (
    "casa-elettrodomestici",
    "elettronica",
    "fai-da-te",
    "bellezza",
    "abbigliamento",
)
# Shopify pubblico: JSON via requests, max 60s. Niente Playwright (carica subito o fallisce).
_JSON_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "it-IT,it;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def _timeout_s() -> int:
    try:
        return max(10, int(os.getenv("REMUNDO_FETCH_TIMEOUT", "60")))
    except ValueError:
        return 60


def _fetch_json(url: str, params: dict | None = None) -> dict:
    response = requests.get(
        url,
        headers=_JSON_HEADERS,
        params=params,
        timeout=_timeout_s(),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"JSON inatteso da {url}")
    return data


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    del fetcher  # Remundo non usa SessionFetcher/Playwright.
    seen: dict[str, SourceListing] = {}
    json_ok = False
    for page in range(1, 8):
        try:
            data = _fetch_json(PRODUCTS_URL, params={"limit": 50, "page": page})
        except Exception as exc:
            print(f"[remundo] products.json page {page}: {exc}")
            continue
        json_ok = True
        products = data.get("products") or []
        if not products:
            break
        for product in products:
            item = _from_product(product)
            if item:
                seen[item.listing_id] = item
    for slug in COLLECTIONS:
        try:
            data = _fetch_json(
                f"https://remundo.it/collections/{slug}/products.json",
                params={"limit": 30},
            )
        except Exception:
            continue
        for product in data.get("products") or []:
            item = _from_product(product)
            if item:
                seen[item.listing_id] = item
    if not json_ok and not seen:
        print("[remundo] products.json irraggiungibile.")
    print(f"[remundo] Bancali disponibili: {len(seen)}.")
    return list(seen.values())


def _from_product(product: dict) -> SourceListing | None:
    variants = product.get("variants") or []
    live = [v for v in variants if v.get("available")]
    if not live:
        return None
    variant = live[0]
    try:
        price = float(variant.get("price") or 0)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    title = str(product.get("title") or "").strip()
    handle = str(product.get("handle") or product.get("id"))
    retail = parse_italian_amount(_retail_blob(title))
    body = str(product.get("body_html") or "")
    packing = bool(re.search(r"packing\s*list|elenco\s+ean|\bean\b", body, re.I))
    pieces = _pieces(title)
    images = product.get("images") or []
    image_url = ""
    if images and isinstance(images[0], dict):
        image_url = str(images[0].get("src") or "")
    extra = {
        "pieces": pieces,
        "packing_list": packing,
        "image_url": image_url,
        "has_image": bool(image_url),
    }
    return SourceListing(
        source="remundo",
        listing_id=str(product.get("id") or handle),
        title=title,
        url=f"https://remundo.it/products/{handle}",
        current_price_eur=price,
        retail_hint_eur=retail or 0.0,
        extra=extra,
    )


def _retail_blob(title: str) -> str:
    match = re.search(r"Retail\s*([\d.,]+)", title, re.I)
    return match.group(1) if match else ""


def _pieces(title: str) -> int:
    match = re.search(r"(\d+)\s*Pezzi", title, re.I)
    return int(match.group(1)) if match else 0
