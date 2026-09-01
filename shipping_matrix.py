"""Costi spedizione/ritiro dinamici per peso e volume stimato."""

from __future__ import annotations

import os
import re

from listing import SourceListing
from site_profiles import SiteProfile

_SIZE_TIERS = {
    "envelope": {
        "outbound": {"ebay": 4.0, "vinted": 3.5, "subito": 4.0},
        "inbound": 3.5,
        "pickup": 0.0,
    },
    "small_parcel": {
        "outbound": {"ebay": 6.0, "vinted": 5.0, "subito": 5.5},
        "inbound": 6.0,
        "pickup": 15.0,
    },
    "medium_parcel": {
        "outbound": {"ebay": 9.0, "vinted": 7.5, "subito": 8.0},
        "inbound": 10.0,
        "pickup": 25.0,
    },
    "large_parcel": {
        "outbound": {"ebay": 14.0, "vinted": 12.0, "subito": 12.0},
        "inbound": 18.0,
        "pickup": 35.0,
    },
    "pallet_epal": {
        "outbound": {"ebay": 45.0, "vinted": 40.0, "subito": 40.0},
        "inbound": 0.0,
        "pickup": 80.0,
    },
}

_WEIGHT_RE = re.compile(r"(\d{1,3})\s*kg", re.I)
_EPAL_RE = re.compile(r"\b(epal|euro\s*pallet|bancale|pallet)\b", re.I)
_LARGE_RE = re.compile(
    r"\b(scaffal|frigo|lavatrice|divano|armadio|scrivania|congelatore|climatizzatore)\b",
    re.I,
)
_SMALL_RE = re.compile(
    r"\b(cavo|cover|custodia|filtro|accessori|libro|profumo|orologio|smartwatch)\b",
    re.I,
)


def infer_size_tier(listing: SourceListing, profile: SiteProfile) -> str:
    extra = listing.extra or {}
    explicit = str(extra.get("size_tier") or "").strip().lower()
    if explicit in _SIZE_TIERS:
        return explicit
    if profile.listing_kind == "pallet" or _EPAL_RE.search(listing.title):
        return "pallet_epal"
    weight = extra.get("weight_kg")
    if weight is None:
        match = _WEIGHT_RE.search(listing.title)
        weight = float(match.group(1)) if match else None
    if weight is not None:
        if weight <= 2:
            return "envelope"
        if weight <= 5:
            return "small_parcel"
        if weight <= 15:
            return "medium_parcel"
        return "large_parcel"
    pieces = int(extra.get("pieces") or 0)
    if pieces >= 50:
        return "pallet_epal"
    if pieces >= 15:
        return "large_parcel"
    if _LARGE_RE.search(listing.title):
        return "large_parcel"
    if _SMALL_RE.search(listing.title):
        return "small_parcel"
    if profile.listing_kind == "pallet":
        return "pallet_epal"
    return "small_parcel"


def outbound_shipping_eur(listing: SourceListing, profile: SiteProfile, channel: str) -> float:
    if os.getenv("USE_SHIPPING_MATRIX", "true").lower() not in ("1", "true", "yes"):
        from flip_rules import shipping_for_category
        from money import infer_category

        tag = listing.category_tag or infer_category(listing.title).tag
        return shipping_for_category(tag, profile, channel)
    tier = infer_size_tier(listing, profile)
    return float(_SIZE_TIERS[tier]["outbound"].get(channel, 7.0))


def inbound_shipping_eur(listing: SourceListing, profile: SiteProfile) -> float:
    if listing.shipping_eur > 0:
        return listing.shipping_eur
    if os.getenv("USE_SHIPPING_MATRIX", "true").lower() not in ("1", "true", "yes"):
        return profile.inbound_shipping_eur
    tier = infer_size_tier(listing, profile)
    return float(_SIZE_TIERS[tier]["inbound"])


def pickup_cost_eur(listing: SourceListing, profile: SiteProfile, *, requires_pickup: bool) -> float:
    if not requires_pickup:
        return 0.0
    if profile.listing_kind == "pallet":
        return profile.pickup_buffer_eur or float(_SIZE_TIERS["pallet_epal"]["pickup"])
    if os.getenv("USE_SHIPPING_MATRIX", "true").lower() not in ("1", "true", "yes"):
        return profile.pickup_buffer_eur or float(os.getenv("PICKUP_COST_EUR", "35"))
    tier = infer_size_tier(listing, profile)
    return float(_SIZE_TIERS[tier]["pickup"])
