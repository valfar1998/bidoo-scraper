"""Stima all-in vs rivendita su eBay, Vinted e Subito."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from listing import SourceListing
from money import infer_category, looks_like_bulk_lot
from resale_estimator import brand_resale_boost
from site_profiles import SiteProfile

Channel = Literal["ebay", "vinted", "subito"]
Verdict = Literal["conviene", "evita"]

FEE_PCT: dict[Channel, float] = {
    "ebay": 0.12,
    "vinted": 0.05,
    "subito": 0.0,
}

CHANNEL_RESALE_FACTOR: dict[Channel, float] = {
    "ebay": 1.00,
    "vinted": 0.92,
    "subito": 0.88,
}


@dataclass(frozen=True)
class ChannelEstimate:
    platform: Channel
    resale_value_eur: float
    fee_eur: float
    outbound_shipping_eur: float
    net_profit_eur: float
    margin_pct: float


@dataclass(frozen=True)
class ClassicEstimate:
    inferred_resale_eur: float
    landed_cost_eur: float
    buyer_premium_eur: float
    haircut_eur: float
    max_bid_eur: float
    break_even_bid_eur: float
    channels: dict[Channel, ChannelEstimate]
    best_platform: Channel
    score: int
    is_viable: bool
    verdict: Verdict
    verdict_reason: str
    category_tag: str
    category_name: str


def _outbound(profile: SiteProfile, channel: Channel) -> float:
    if channel == "ebay":
        return profile.default_outbound_ebay
    if channel == "vinted":
        return profile.default_outbound_vinted
    return profile.default_outbound_subito


def infer_resale_value(listing: SourceListing, profile: SiteProfile) -> float:
    category = infer_category(listing.title)
    boost = brand_resale_boost(listing.title, listing.listing_id)
    if listing.retail_hint_eur > 0:
        ratio = min(profile.claimed_retail_factor, max(0.18, category.resale_ratio + boost))
        return listing.retail_hint_eur * ratio
    multiplier = profile.resale_multiplier * (1 + boost)
    if looks_like_bulk_lot(listing.title):
        multiplier *= 1.15
    return max(listing.current_price_eur * multiplier, listing.current_price_eur * 1.05)


def estimate_classic(
    listing: SourceListing,
    profile: SiteProfile,
    *,
    min_profit_eur: float,
    min_margin_pct: float,
    min_headroom_eur: float,
) -> ClassicEstimate:
    category = infer_category(listing.title)
    inferred = infer_resale_value(listing, profile)
    haircut_pct = profile.lot_haircut
    if looks_like_bulk_lot(listing.title):
        haircut_pct = max(haircut_pct, 0.15)
    haircut = inferred * haircut_pct
    sellable = inferred - haircut

    inbound = listing.shipping_eur if listing.shipping_eur > 0 else profile.inbound_shipping_eur
    premium = listing.current_price_eur * profile.buyer_premium
    landed = listing.current_price_eur + premium + inbound + profile.pickup_buffer_eur

    channels: dict[Channel, ChannelEstimate] = {}
    for channel in ("ebay", "vinted", "subito"):
        resale = sellable * CHANNEL_RESALE_FACTOR[channel]
        fee = resale * FEE_PCT[channel]
        outbound = _outbound(profile, channel)
        net = resale - landed - fee - outbound
        cost_base = max(landed, 0.01)
        channels[channel] = ChannelEstimate(
            platform=channel,
            resale_value_eur=resale,
            fee_eur=fee,
            outbound_shipping_eur=outbound,
            net_profit_eur=net,
            margin_pct=(net / cost_base) * 100,
        )

    ve = max(
        (channels["ebay"], channels["vinted"]),
        key=lambda item: item.net_profit_eur,
    )
    best_platform = ve.platform
    best = ve

    fee_pct = FEE_PCT[best_platform]
    outbound = _outbound(profile, best_platform)
    max_total = sellable * CHANNEL_RESALE_FACTOR[best_platform] * (1 - fee_pct) - outbound - min_profit_eur
    max_bid = (max_total - inbound - profile.pickup_buffer_eur) / (1 + profile.buyer_premium)
    break_even = (
        sellable * CHANNEL_RESALE_FACTOR[best_platform] * (1 - fee_pct)
        - outbound
        - inbound
        - profile.pickup_buffer_eur
    ) / (1 + profile.buyer_premium)

    headroom = max_bid - listing.current_price_eur
    viable = (
        best.net_profit_eur >= min_profit_eur
        and best.margin_pct >= min_margin_pct
        and headroom >= min_headroom_eur
        and max_bid > listing.current_price_eur
    )

    if listing.current_price_eur > max_bid:
        verdict: Verdict = "evita"
        reason = (
            f"Prezzo attuale ({listing.current_price_eur:.2f} €) sopra il limite "
            f"({max(0, max_bid):.2f} €) per guadagno ≥{min_profit_eur:.0f} € su Vinted/eBay."
        )
    elif not viable:
        verdict = "evita"
        reason = (
            f"Vinted/eBay: {best.net_profit_eur:.0f} € ({best.margin_pct:.0f}%). "
            f"Sotto soglia {min_profit_eur:.0f} €."
        )
    else:
        verb = "Compra" if profile.listing_kind == "pallet" else "Offri"
        verdict = "conviene"
        reason = (
            f"{verb} al max {max(0, max_bid):.2f} €. "
            f"Rivendi su {best_platform} (~{best.net_profit_eur:.0f} € netti)."
        )

    score = _score(best.net_profit_eur, best.margin_pct, min_profit_eur, listing.bids)
    return ClassicEstimate(
        inferred_resale_eur=inferred,
        landed_cost_eur=landed,
        buyer_premium_eur=premium,
        haircut_eur=haircut,
        max_bid_eur=max(0.0, max_bid),
        break_even_bid_eur=max(0.0, break_even),
        channels=channels,
        best_platform=best_platform,
        score=score,
        is_viable=viable,
        verdict=verdict,
        verdict_reason=reason,
        category_tag=listing.category_tag or category.tag,
        category_name=category.name,
    )


def _score(net: float, margin: float, min_profit: float, bids: int) -> int:
    profit_part = min(40, max(0, (net / max(min_profit, 1)) * 20))
    margin_part = min(25, max(0, margin / 2))
    quiet_part = 15 if bids <= 1 else 6 if bids <= 4 else 0
    return int(min(100, max(0, profit_part + margin_part + quiet_part)))
