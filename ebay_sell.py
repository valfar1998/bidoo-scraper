"""eBay Sell API: creazione inserzioni da bozze inventario."""

from __future__ import annotations

import base64
import os
import re
import time
from typing import Any

import requests

from listing_generator import build_ebay_listing_payload
from sources.ebay_api import credentials, marketplace_id

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
INVENTORY_BASE = "https://api.ebay.com/sell/inventory/v1"
USER_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory"

_USER_TOKEN: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def sell_api_enabled() -> bool:
    return bool(os.getenv("EBAY_USER_REFRESH_TOKEN", "").strip())


def _sanitize_sku(listing_id: str) -> str:
    sku = re.sub(r"[^A-Za-z0-9_-]", "-", listing_id)[:50]
    return sku or "item-1"


def get_user_access_token(*, force: bool = False) -> str:
    refresh = os.getenv("EBAY_USER_REFRESH_TOKEN", "").strip()
    if not refresh:
        raise ValueError(
            "EBAY_USER_REFRESH_TOKEN mancante. "
            "Autorizza l'app eBay con scope sell.inventory."
        )
    app_id, cert = credentials()
    now = time.time()
    if not force and _USER_TOKEN["access_token"] and _USER_TOKEN["expires_at"] > now + 60:
        return str(_USER_TOKEN["access_token"])
    auth = base64.b64encode(f"{app_id}:{cert}".encode()).decode()
    response = requests.post(
        OAUTH_URL,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "scope": USER_SCOPE,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"OAuth user token {response.status_code}: {response.text[:300]}")
    data = response.json()
    _USER_TOKEN["access_token"] = data["access_token"]
    _USER_TOKEN["expires_at"] = now + int(data.get("expires_in", 7200))
    return str(data["access_token"])


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_user_access_token()}",
        "Content-Type": "application/json",
        "Content-Language": "it-IT",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id(),
    }


def _put_inventory_item(sku: str, payload: dict[str, Any]) -> None:
    url = f"{INVENTORY_BASE}/inventory_item/{sku}"
    response = requests.put(url, headers=_headers(), json=payload, timeout=45)
    if response.status_code == 401:
        get_user_access_token(force=True)
        response = requests.put(url, headers=_headers(), json=payload, timeout=45)
    if response.status_code not in (200, 201, 204):
        raise RuntimeError(f"inventory_item {response.status_code}: {response.text[:400]}")


def _create_offer(sku: str, price_eur: float, *, publish: bool) -> dict[str, Any]:
    policy_ids = {
        "fulfillmentPolicyId": os.getenv("EBAY_FULFILLMENT_POLICY_ID", "").strip(),
        "paymentPolicyId": os.getenv("EBAY_PAYMENT_POLICY_ID", "").strip(),
        "returnPolicyId": os.getenv("EBAY_RETURN_POLICY_ID", "").strip(),
    }
    missing = [k for k, v in policy_ids.items() if not v]
    if missing:
        raise ValueError(
            f"Policy eBay mancanti: {', '.join(missing)}. "
            "Configura EBAY_*_POLICY_ID nel .env."
        )
    merchant_key = os.getenv("EBAY_MERCHANT_LOCATION_KEY", "").strip()
    if not merchant_key:
        raise ValueError("EBAY_MERCHANT_LOCATION_KEY mancante.")

    category_id = os.getenv("EBAY_DEFAULT_CATEGORY_ID", "9355").strip()
    offer = {
        "sku": sku,
        "marketplaceId": marketplace_id().replace("_", "-"),
        "format": "FIXED_PRICE",
        "availableQuantity": 1,
        "categoryId": category_id,
        "merchantLocationKey": merchant_key,
        "pricingSummary": {"price": {"value": f"{price_eur:.2f}", "currency": "EUR"}},
        "listingPolicies": policy_ids,
        "listingDuration": "GTC",
    }
    response = requests.post(
        f"{INVENTORY_BASE}/offer",
        headers=_headers(),
        json=offer,
        timeout=45,
    )
    if response.status_code == 401:
        get_user_access_token(force=True)
        response = requests.post(
            f"{INVENTORY_BASE}/offer", headers=_headers(), json=offer, timeout=45
        )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"create offer {response.status_code}: {response.text[:400]}")
    data = response.json()
    offer_id = str(data.get("offerId") or "")
    listing_url = ""

    if publish and offer_id:
        pub = requests.post(
            f"{INVENTORY_BASE}/offer/{offer_id}/publish",
            headers=_headers(),
            timeout=45,
        )
        if pub.status_code in (200, 201):
            pub_data = pub.json()
            listing_url = str(pub_data.get("listingId") or "")
            if listing_url and listing_url.isdigit():
                listing_url = f"https://www.ebay.it/itm/{listing_url}"

    return {"offer_id": offer_id, "listing_url": listing_url, "published": bool(listing_url)}


def create_ebay_listing_from_snapshot(
    snapshot: dict[str, Any],
    *,
    listing_id: str,
    price_eur: float | None = None,
    publish: bool | None = None,
) -> dict[str, Any]:
    """Crea inventory item + offer eBay da snapshot alert/inventario."""
    if not sell_api_enabled():
        raise ValueError(
            "eBay Sell API non configurata. Imposta EBAY_USER_REFRESH_TOKEN e le policy."
        )
    payload = build_ebay_listing_payload(snapshot, price_eur=price_eur)
    sku = _sanitize_sku(listing_id)
    if publish is None:
        publish = os.getenv("EBAY_AUTO_PUBLISH", "false").lower() in ("1", "true", "yes")

    inv_item = {
        "product": {
            "title": payload["title"],
            "description": payload["description_plain"],
            "aspects": payload.get("aspects") or {},
        },
        "condition": payload.get("condition", "USED_EXCELLENT"),
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
    }
    if payload.get("image_url"):
        inv_item["product"]["imageUrls"] = [payload["image_url"]]

    _put_inventory_item(sku, inv_item)
    offer = _create_offer(sku, payload["price_eur"], publish=publish)
    return {
        "sku": sku,
        "title": payload["title"],
        "price_eur": payload["price_eur"],
        **offer,
    }
