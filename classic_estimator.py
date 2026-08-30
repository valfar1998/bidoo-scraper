"""Stima all-in vs rivendita su eBay, Vinted e Subito."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from brands import find_brand, is_premium_brand
from photo_check import inspect_image
from comps import CompRow, match_comp
from feedback import FeedbackStore
from flip_rules import (
    FLIP_CATEGORY_TAGS,
    catawiki_reject_reason,
    has_channel_negatives,
    has_condition_risk,
    infer_flip_tag,
    is_flip_friendly,
    is_unshippable,
    is_vague_title,
    requires_pickup,
    shipping_for_category,
    useful_word_count,
)
from listing import SourceListing
from money import infer_category, looks_like_bulk_lot
from site_profiles import SiteProfile

Channel = Literal["ebay", "vinted", "subito"]
Verdict = Literal["conviene", "evita"]
HARD_PROFIT_FLOOR_EUR = 25.0
MIN_RESALE_GUESS_EUR = 15.0

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

FASHION_TAGS = frozenset({"moda", "sneaker", "borse"})


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
    confidence: int
    is_viable: bool
    verdict: Verdict
    verdict_reason: str
    category_tag: str
    category_name: str
    brand: str | None
    max_buy_eur: float
    pickup_eur: float
    deposit_eur: float
    deal_reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    comps_product: str = ""


def _official_value(listing: SourceListing, inferred: float, comp: CompRow | None) -> float:
    retail = listing.retail_hint_eur
    if 20 <= retail <= 200:
        return retail
    if comp and comp.best_avg > 0:
        return comp.best_avg / 0.55
    return inferred / 0.50 if inferred > 0 else 0.0


def budget_from_score(score: int, category: str, *, pallet: bool, official_eur: float) -> float:
    if pallet:
        cap = float(os.getenv("MAX_PALLET_EUR", "400"))
        ratio = float(os.getenv("MAX_BUY_OF_OFFICIAL_PCT", "40")) / 100
        if official_eur > 0:
            cap = min(cap, official_eur * ratio)
        return cap
    if score < 50:
        cap = 25.0
    elif score <= 70:
        cap = 40.0
    elif score <= 85:
        cap = 60.0
    else:
        cap = 100.0
    if category in FASHION_TAGS:
        cap = min(max(cap, 5.0), 25.0)
    ratio = float(os.getenv("MAX_BUY_OF_OFFICIAL_PCT", "40")) / 100
    if official_eur > 0:
        cap = min(cap, official_eur * ratio)
    return cap


def infer_resale_value(
    listing: SourceListing,
    profile: SiteProfile,
    *,
    brand: str | None,
    comp: CompRow | None,
) -> float:
    category = infer_category(listing.title)
    if comp and not comp.too_cheap and not comp.too_volatile:
        ebay = comp.avg_price_ebay or 0
        vinted = comp.avg_price_vinted or 0
        if ebay or vinted:
            return max(ebay, vinted) * 0.92
    retail = listing.retail_hint_eur
    if 20 <= retail <= 200:
        ratio = min(profile.claimed_retail_factor, max(0.18, category.resale_ratio))
        if brand:
            ratio = min(0.55, ratio + 0.04)
        return retail * ratio
    multiplier = profile.resale_multiplier * (1.08 if brand else 1.0)
    if looks_like_bulk_lot(listing.title) and profile.listing_kind != "pallet":
        multiplier *= 1.10
    return max(listing.current_price_eur * multiplier, listing.current_price_eur * 1.05)


def deposit_for(listing: SourceListing, profile: SiteProfile) -> float:
    extra = listing.extra or {}
    if extra.get("deposit_eur"):
        try:
            return float(extra["deposit_eur"])
        except (TypeError, ValueError):
            pass
    if profile.key == "gobid":
        return float(os.getenv("GOBID_DEPOSIT_EUR", "50"))
    if profile.listing_kind == "judicial":
        return float(os.getenv("JUDICIAL_DEPOSIT_EUR", "30"))
    return 0.0


def pickup_for(listing: SourceListing, profile: SiteProfile) -> float:
    if not requires_pickup(listing, profile):
        return 0.0
    if profile.listing_kind == "pallet":
        return profile.pickup_buffer_eur
    return profile.pickup_buffer_eur or float(os.getenv("PICKUP_COST_EUR", "35"))


def estimate_classic(
    listing: SourceListing,
    profile: SiteProfile,
    *,
    min_profit_eur: float,
    min_margin_pct: float,
    min_headroom_eur: float,
    feedback: FeedbackStore | None = None,
    comps: list[CompRow] | None = None,
) -> ClassicEstimate:
    feedback = feedback or FeedbackStore.load()
    profit_floor = max(min_profit_eur, HARD_PROFIT_FLOOR_EUR)
    brand = find_brand(listing.title)
    flip_tag = infer_flip_tag(listing.title)
    category = infer_category(listing.title)
    category_tag = listing.category_tag or flip_tag or category.tag
    comp = match_comp(listing.title, comps)

    reasons: list[str] = []
    risks: list[str] = []

    if profile.key == "catawiki":
        reject_reason = catawiki_reject_reason(listing)
    else:
        reject_reason = None

    if profile.listing_kind != "pallet" and is_unshippable(listing, profile):
        reject_reason = reject_reason or "Non spedibile (ritiro / pesante / bancale)."
    if useful_word_count(listing.title) < 3 or is_vague_title(listing):
        reject_reason = reject_reason or "Titolo troppo vago (meno di 3 parole utili)."

    photo = inspect_image(listing)
    if photo == "missing":
        reject_reason = reject_reason or "Manca la foto."
    elif photo == "tiny":
        reject_reason = reject_reason or "Foto sotto 300 px / troppo piccola."

    if comp and comp.too_volatile:
        reject_reason = reject_reason or "Prezzi comps troppo volatili (stdev > 40%)."
    if comp and comp.too_cheap:
        reject_reason = reject_reason or "Comps medi sotto 15 €: non copre spedizione/fee."

    if brand and brand in feedback.rejected_brands():
        risks.append(f"Marca {brand} ignorata 3+ volte (penalità score).")

    pieces = int((listing.extra or {}).get("pieces") or 0)
    if profile.listing_kind == "pallet":
        max_pallet = float(os.getenv("MAX_PALLET_EUR", "400"))
        max_piece = float(os.getenv("MAX_COST_PER_PIECE_EUR", "15"))
        if listing.current_price_eur > max_pallet:
            reject_reason = reject_reason or f"Bancale sopra cap {max_pallet:.0f} €."
        if pieces > 0:
            cost_piece = listing.current_price_eur / pieces
            if cost_piece > max_piece:
                reject_reason = reject_reason or f"Costo/pezzo {cost_piece:.1f} € sopra {max_piece:.0f} €."

    inferred = infer_resale_value(listing, profile, brand=brand, comp=comp)
    if inferred < MIN_RESALE_GUESS_EUR:
        reject_reason = reject_reason or "Stima rivendita sotto 15 €: non conviene."
    haircut_pct = profile.lot_haircut
    if looks_like_bulk_lot(listing.title):
        haircut_pct = max(haircut_pct, 0.15)
    if profile.listing_kind == "pallet" and not (listing.extra or {}).get("packing_list"):
        haircut_pct = max(haircut_pct, 0.22)
        risks.append("Niente packing list: stima a pezzo + cap budget")
    haircut = inferred * haircut_pct
    sellable = inferred - haircut

    inbound = listing.shipping_eur if listing.shipping_eur > 0 else profile.inbound_shipping_eur
    premium = listing.current_price_eur * profile.buyer_premium
    pickup = pickup_for(listing, profile)
    deposit = deposit_for(listing, profile)
    landed = listing.current_price_eur + premium + inbound + pickup + deposit

    channels: dict[Channel, ChannelEstimate] = {}
    for channel in ("ebay", "vinted", "subito"):
        resale = sellable * CHANNEL_RESALE_FACTOR[channel]
        fee = resale * FEE_PCT[channel]
        outbound = shipping_for_category(category_tag, profile, channel)
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

    ebay_blocked = has_channel_negatives(listing, "ebay")
    vinted_blocked = has_channel_negatives(listing, "vinted")
    if profile.key == "ebay_source":
        ebay_blocked = True
    if profile.key == "vinted_source":
        vinted_blocked = True
    candidates = []
    if not ebay_blocked:
        candidates.append(channels["ebay"])
    if not vinted_blocked:
        candidates.append(channels["vinted"])
    if not candidates:
        reject_reason = reject_reason or "Keyword negative su eBay e Vinted."
        best = max(channels["ebay"], channels["vinted"], key=lambda item: item.net_profit_eur)
    else:
        best = max(candidates, key=lambda item: item.net_profit_eur)
    best_platform = best.platform

    fee_pct = FEE_PCT[best_platform]
    outbound = shipping_for_category(category_tag, profile, best_platform)
    extras = inbound + pickup + deposit
    max_total = sellable * CHANNEL_RESALE_FACTOR[best_platform] * (1 - fee_pct) - outbound - profit_floor
    max_bid = (max_total - extras) / (1 + profile.buyer_premium)
    break_even = (
        sellable * CHANNEL_RESALE_FACTOR[best_platform] * (1 - fee_pct) - outbound - extras
    ) / (1 + profile.buyer_premium)

    score, extra_reasons, extra_risks = _compose_score(
        listing=listing,
        profile=profile,
        brand=brand,
        category_tag=category_tag,
        best=best,
        min_profit_eur=profit_floor,
        feedback=feedback,
        comp=comp,
        pickup=pickup,
        photo=photo,
    )
    reasons.extend(extra_reasons)
    risks.extend(extra_risks)

    official = _official_value(listing, inferred, comp)
    max_buy = budget_from_score(
        score,
        category_tag,
        pallet=profile.listing_kind == "pallet",
        official_eur=official,
    )
    max_bid = min(max_bid, max_buy)

    if best.net_profit_eur < profit_floor:
        reject_reason = reject_reason or (
            f"Margine netto {best.net_profit_eur:.0f} € sotto 25 €."
        )

    if listing.current_price_eur > max_bid:
        reject_reason = (
            reject_reason
            or (
                f"Prezzo ({listing.current_price_eur:.2f} €) sopra budget/max "
                f"({max(0, max_bid):.2f} €)."
            )
        )

    headroom = max_bid - listing.current_price_eur
    viable = (
        reject_reason is None
        and best.net_profit_eur >= profit_floor
        and best.margin_pct >= min_margin_pct
        and headroom >= min_headroom_eur
        and max_bid > listing.current_price_eur
        and score >= int(os.getenv("MIN_RESALE_SCORE", "50"))
    )

    if reject_reason:
        verdict: Verdict = "evita"
        reason = reject_reason
    elif listing.current_price_eur > max_bid:
        verdict = "evita"
        reason = (
            f"Prezzo attuale ({listing.current_price_eur:.2f} €) sopra il limite "
            f"({max(0, max_bid):.2f} €)."
        )
    elif score < int(os.getenv("MIN_RESALE_SCORE", "50")):
        verdict = "evita"
        reason = f"Score {score}/100 sotto soglia (filtri qualità)."
    elif not viable:
        verdict = "evita"
        reason = (
            f"Vinted/eBay: {best.net_profit_eur:.0f} € ({best.margin_pct:.0f}%). "
            f"Sotto soglia profitto/margine/budget."
        )
    else:
        verb = "Compra" if profile.listing_kind == "pallet" else "Offri"
        verdict = "conviene"
        reason = (
            f"{verb} al max {max(0, max_bid):.2f} €. "
            f"Rivendi su {best_platform} (~{best.net_profit_eur:.0f} € netti)."
        )

    confidence = _confidence(
        listing=listing,
        profile=profile,
        brand=brand,
        comp=comp,
        best=best,
        photo=photo,
    )
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
        confidence=confidence,
        is_viable=viable and verdict == "conviene",
        verdict=verdict,
        verdict_reason=reason,
        category_tag=category_tag,
        category_name=category.name,
        brand=brand,
        max_buy_eur=max_buy,
        pickup_eur=pickup,
        deposit_eur=deposit,
        deal_reasons=reasons,
        risks=risks,
        comps_product=comp.product if comp else "",
    )


def _compose_score(
    *,
    listing: SourceListing,
    profile: SiteProfile,
    brand: str | None,
    category_tag: str,
    best: ChannelEstimate,
    min_profit_eur: float,
    feedback: FeedbackStore,
    comp: CompRow | None,
    pickup: float,
    photo: str,
) -> tuple[int, list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    profit_part = min(40, max(0, (best.net_profit_eur / max(min_profit_eur, 1)) * 20))
    margin_part = min(25, max(0, best.margin_pct / 2))
    quiet_part = 15 if listing.bids <= 1 else 6 if listing.bids <= 4 else 0
    score = int(profit_part + margin_part + quiet_part)

    if brand:
        if is_premium_brand(brand):
            score += 30
            reasons.append(f"Marca premium: {brand}")
        else:
            score += 10
            reasons.append(f"Marca riconosciuta: {brand}")
    if comp and getattr(comp, "reliable", False):
        score += 15
        reasons.append("Comps affidabili (stdev < 25%)")
    if comp and comp.too_volatile:
        score -= 20
        risks.append("Comps volatili (stdev > 40%)")
    if is_flip_friendly(listing, profile):
        score += 8
        reasons.append("Flip-friendly (spedibile in scatola)")
    if profile.listing_kind != "pallet" and category_tag not in FLIP_CATEGORY_TAGS:
        score -= 30
        risks.append("Categoria fuori allowlist flip")
    if pickup > 0 or requires_pickup(listing, profile):
        score -= 15
        risks.append("Ritiro fisico / sede")
        if profile.listing_kind == "pallet":
            score += 8
    if has_condition_risk(listing):
        score -= 12
        risks.append("Condizione: rotto / da testare / pesante")
    if photo == "stock":
        score -= 20
        risks.append("Foto stock")
    if comp and best.net_profit_eur > 0 and listing.current_price_eur < comp.best_avg * 0.7:
        reasons.append("Prezzo molto sotto la media comps")
        score += 6
    if best.net_profit_eur > 40:
        reasons.append("Margine netto > 40 €")
        score += 20
    elif 25 <= best.net_profit_eur <= 30:
        score -= 30
        risks.append("Margine 25–30 €: borderline")
    if best.margin_pct >= 40:
        reasons.append("Margine % > 40%")
        score += 5

    delta, hist_reasons = feedback.score_delta(brand, category_tag)
    score += delta
    reasons.extend(hist_reasons)

    return int(min(100, max(0, score))), reasons, risks


def _confidence(
    *,
    listing: SourceListing,
    profile: SiteProfile,
    brand: str | None,
    comp: CompRow | None,
    best: ChannelEstimate,
    photo: str,
) -> int:
    value = 20
    if is_premium_brand(brand):
        value += 25
    elif brand:
        value += 12
    if comp and getattr(comp, "reliable", False):
        value += 20
    elif comp and not comp.too_volatile:
        value += 8
    if is_flip_friendly(listing, profile):
        value += 12
    else:
        value -= 10
    if best.net_profit_eur > 40:
        value += 18
    elif best.net_profit_eur >= 25:
        value += 8
    if useful_word_count(listing.title) >= 4 and not is_vague_title(listing):
        value += 10
    else:
        value -= 15
    if photo == "stock":
        value -= 10
    return int(min(100, max(0, value)))
