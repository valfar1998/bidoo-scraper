"""OAuth + Browse API eBay (Finding API dismessa dal 2025)."""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import requests

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"

_TOKEN: dict[str, Any] = {"access_token": "", "expires_at": 0.0}


def credentials() -> tuple[str, str]:
    app_id = os.getenv("EBAY_APP_ID", "").strip()
    cert = os.getenv("EBAY_CERT_ID", os.getenv("EBAY_CLIENT_SECRET", "")).strip()
    return app_id, cert


def marketplace_id() -> str:
    explicit = os.getenv("EBAY_MARKETPLACE_ID", "").strip()
    if explicit:
        return explicit
    global_id = os.getenv("EBAY_GLOBAL_ID", "EBAY-IT").strip().upper()
    return global_id.replace("-", "_")


def get_access_token(*, force: bool = False) -> str:
    app_id, cert = credentials()
    if not app_id or not cert:
        raise ValueError("EBAY_APP_ID e EBAY_CERT_ID mancanti nel .env")
    now = time.time()
    if not force and _TOKEN["access_token"] and _TOKEN["expires_at"] > now + 60:
        return str(_TOKEN["access_token"])
    auth = base64.b64encode(f"{app_id}:{cert}".encode()).decode()
    response = requests.post(
        OAUTH_URL,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": OAUTH_SCOPE},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"OAuth eBay {response.status_code}: {response.text[:240]}. "
            "Verifica EBAY_APP_ID e EBAY_CERT_ID (Client Secret completo) su developer.ebay.com."
        )
    data = response.json()
    _TOKEN["access_token"] = data["access_token"]
    _TOKEN["expires_at"] = now + int(data.get("expires_in", 7200))
    return str(data["access_token"])


def search_auctions(
    query: str,
    *,
    limit: int = 25,
    extra_filter: str = "",
) -> list[dict[str, Any]]:
    token = get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id(),
        "Accept": "application/json",
    }
    filter_parts = ["buyingOptions:{AUCTION}"]
    min_price = float(os.getenv("EBAY_MIN_PRICE", "5"))
    max_price = float(os.getenv("EBAY_MAX_PRICE", "120"))
    if max_price > min_price:
        filter_parts.append(f"price:[{min_price:.0f}..{max_price:.0f}]")
        filter_parts.append("priceCurrency:EUR")
    conditions = os.getenv("EBAY_CONDITIONS", "NEW,USED,LIKE_NEW").strip()
    if conditions:
        filter_parts.append("conditions:{" + conditions + "}")
    if os.getenv("EBAY_ITALY_ONLY", "true").lower() in ("1", "true", "yes"):
        filter_parts.append("itemLocationCountry:IT")
        filter_parts.append("deliveryCountry:IT")
    if extra_filter.strip():
        filter_parts.append(extra_filter.strip())
    params = {
        "q": query,
        "limit": str(limit),
        "filter": ",".join(filter_parts),
        "sort": "endingSoonest",
    }
    response = requests.get(BROWSE_URL, headers=headers, params=params, timeout=45)
    if response.status_code == 401:
        token = get_access_token(force=True)
        headers["Authorization"] = f"Bearer {token}"
        response = requests.get(BROWSE_URL, headers=headers, params=params, timeout=45)
    if response.status_code != 200:
        raise RuntimeError(f"Browse API {response.status_code}: {response.text[:240]}")
    payload = response.json()
    items = payload.get("itemSummaries") or []
    return items if isinstance(items, list) else []
