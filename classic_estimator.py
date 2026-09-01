"""Stima all-in vs rivendita su eBay, Vinted e Subito."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from brands import find_brand, is_premium_brand
from bidding_velocity import VelocityResult, analyze_velocity
from comps import CompRow
from market_lookup import resolve_comp
from feedback import FeedbackStore
from flip_rules import (
    FLIP_CATEGORY_TAGS,
    catawiki_reject_reason,
    has_channel_negatives,
    has_condition_risk,
    has_hard_condition,
    infer_flip_tag,
    is_flip_friendly,
    is_unshippable,
    is_vague_title,
    requires_pickup,
    useful_word_count,
)
from listing import SourceListing
from money import infer_category, looks_like_bulk_lot
from shipping_matrix import inbound_shipping_eur, outbound_shipping_eur, pickup_cost_eur
from site_profiles import SiteProfile
from inventory import category_risk_coefficients

if TYPE_CHECKING:
    from auction_history import TrackedAuction

Channel = Literal["ebay", "vinted", "subito"]
Verdict = Literal["conviene", "evita"]
HARD_PROFIT_FLOOR_EUR = 25.0
MIN_RESALE_GUESS_EUR = 15.0


def dynamic_roi_enabled() -> bool:
    return os.getenv("USE_DYNAMIC_ROI", "true").lower() in ("1", "true", "yes")


def min_net_roi_pct() -> float:
    try:
        return float(os.getenv("MIN_NET_ROI_PCT", "35"))
    except ValueError:
        return 35.0


def min_expected_profit_eur() -> float:
    try:
        return float(os.getenv("MIN_EXPECTED_PROFIT_EUR", "50"))
    except ValueError:
        return 50.0


def passes_dynamic_roi(best: ChannelEstimate, *, landed_cost: float) -> tuple[bool, str]:
    profit = best.net_profit_eur
    margin = best.margin_pct
    roi_floor = min_net_roi_pct()
    profit_floor = min_expected_profit_eur()
    if profit < profit_floor:
        return False, f"Profitto atteso {profit:.0f} € sotto {profit_floor:.0f} €."
    if margin < roi_floor:
        return False, f"ROI netto {margin:.0f}% sotto {roi_floor:.0f}%."
    return True, ""

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


def compute_recommended_max_bid(
    *,
    listing: SourceListing,
    profile: SiteProfile,
    sellable: float,
    platform: Channel,
    inbound: float,
    pickup: float,
    deposit: float,
    roi_pct: float | None = None,
    min_profit_eur: float | None = None,
) -> tuple[float, float, float]:
    """Max bid per ROI target. Ritorna (max_bid, profitto_netto, roi_%)."""
    roi_pct = min_net_roi_pct() if roi_pct is None else roi_pct
    min_profit_eur = min_expected_profit_eur() if min_profit_eur is None else min_profit_eur
    factor = CHANNEL_RESALE_FACTOR[platform]
    fee_pct = FEE_PCT[platform]
    outbound = outbound_shipping_eur(listing, profile, platform)
    premium_rate = profile.buyer_premium
    extras = inbound + pickup + deposit
    gross = sellable * factor * (1 - fee_pct) - outbound
    coef = 1 + premium_rate
    r = roi_pct / 100.0
    denom = coef * (1 + r)
    bid_roi = (gross - extras * (1 + r)) / denom if denom > 0 else 0.0
    bid_profit = (gross - extras - min_profit_eur) / coef if coef > 0 else 0.0
    max_bid = max(0.0, min(bid_roi, bid_profit))
    landed = max_bid * coef + extras
    resale = sellable * factor
    fee = resale * fee_pct
    net = resale - landed - fee - outbound
    roi = (net / landed * 100) if landed > 0 else 0.0
    return max_bid, net, roi


CATEGORY_PROFIT_FLOOR: dict[str, float] = {
    "moda": 20.0,
    "sneaker": 20.0,
    "borse": 20.0,
    "profumi": 20.0,
    "videogiochi": 20.0,
    "elettronica": 25.0,
    "smartwatch": 25.0,
    "orologi": 25.0,
    "utensili": 15.0,
    "casa": 15.0,
    "lampade": 15.0,
    "libri": 15.0,
    "prima-infanzia": 20.0,
}
CATEGORY_BUDGET_CAP: dict[str, float] = {
    "moda": 25.0,
    "sneaker": 25.0,
    "borse": 25.0,
    "elettronica": 60.0,
    "smartwatch": 60.0,
    "orologi": 60.0,
    "utensili": 40.0,
    "profumi": 30.0,
    "casa": 20.0,
    "lampade": 20.0,
    "libri": 25.0,
    "videogiochi": 40.0,
}
GOBID_DEPOSIT_BY_TAG: dict[str, float] = {
    "moda": 20.0,
    "sneaker": 20.0,
    "borse": 25.0,
    "elettronica": 50.0,
    "utensili": 40.0,
    "orologi": 40.0,
    "videogiochi": 30.0,
    "casa": 35.0,
    "profumi": 20.0,
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
    recommended_max_bid_eur: float
    profit_at_max_bid_eur: float
    roi_at_max_bid_pct: float
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
        if dynamic_roi_enabled():
            ratio = float(os.getenv("MAX_BUY_OF_OFFICIAL_PCT", "40")) / 100
            cap = float(os.getenv("MAX_PALLET_EUR", "2000"))
            if official_eur > 0:
                cap = min(cap, official_eur * ratio)
            return cap
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
    cat_cap = CATEGORY_BUDGET_CAP.get(category)
    if cat_cap is not None:
        cap = min(cap, cat_cap)
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
    if profile.key == "ebay_source":
        if comp and comp.avg_price_vinted > 0 and not comp.too_cheap and not comp.too_volatile:
            factor = 0.95 if (comp.product or "").startswith("live:") else 0.92
            return comp.avg_price_vinted * factor
        if comp and comp.avg_price_ebay > 0 and not comp.too_cheap and not comp.too_volatile:
            return comp.avg_price_ebay * 0.78
        return 0.0
    if profile.key == "vinted_source":
        if comp and comp.avg_price_ebay > 0 and not comp.too_cheap and not comp.too_volatile:
            factor = 0.92 if (comp.product or "").startswith("live:") else 0.88
            return comp.avg_price_ebay * factor
        return 0.0
    if comp and not comp.too_cheap and not comp.too_volatile:
        ebay = comp.avg_price_ebay or 0
        vinted = comp.avg_price_vinted or 0
        if ebay or vinted:
            return max(ebay, vinted) * 0.92
    if profile.listing_kind == "classified":
        return 0.0
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
    tag = infer_flip_tag(listing.title)
    if profile.key == "gobid":
        return GOBID_DEPOSIT_BY_TAG.get(tag, float(os.getenv("GOBID_DEPOSIT_EUR", "50")))
    if profile.listing_kind == "judicial":
        return float(os.getenv("JUDICIAL_DEPOSIT_EUR", "30"))
    return 0.0


def pickup_for(listing: SourceListing, profile: SiteProfile) -> float:
    return pickup_cost_eur(
        listing, profile, requires_pickup=requires_pickup(listing, profile)
    )


def estimate_classic(
    listing: SourceListing,
    profile: SiteProfile,
    *,
    min_profit_eur: float,
    min_margin_pct: float,
    min_headroom_eur: float,
    feedback: FeedbackStore | None = None,
    comps: list[CompRow] | None = None,
    fetcher=None,
    tracked: "TrackedAuction | None" = None,
    remaining_seconds: int | None = None,
) -> ClassicEstimate:
    feedback = feedback or FeedbackStore.load()
    brand = find_brand(listing.title)
    flip_tag = infer_flip_tag(listing.title)
    category = infer_category(listing.title)
    category_tag = listing.category_tag or flip_tag or category.tag
    profit_floor = CATEGORY_PROFIT_FLOOR.get(
        category_tag, max(min_profit_eur, HARD_PROFIT_FLOOR_EUR)
    )
    profit_floor = max(profit_floor, min_profit_eur) if min_profit_eur > HARD_PROFIT_FLOOR_EUR else profit_floor
    if category_tag not in CATEGORY_PROFIT_FLOOR:
        profit_floor = max(min_profit_eur, HARD_PROFIT_FLOOR_EUR)

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

    comp, comp_note = resolve_comp(listing, profile, comps, fetcher)
    if comp and comp.product.startswith("live:"):
        reasons.append(comp_note)
    elif comp:
        comp_note = comp.product

    if comp and comp.too_volatile:
        reject_reason = reject_reason or "Prezzi comps troppo volatili (stdev > 40%)."
    if comp and comp.too_cheap:
        reject_reason = reject_reason or "Comps medi sotto 15 €: non copre spedizione/fee."

    if profile.listing_kind != "pallet" and category_tag not in FLIP_CATEGORY_TAGS:
        reject_reason = reject_reason or f"Categoria '{category_tag}' non flip-friendly."
    if has_hard_condition(listing):
        reject_reason = reject_reason or "Condizione sospetta (non testato / difettoso / mancante)."
    if brand and brand in feedback.blacklisted_brands():
        reject_reason = reject_reason or f"Marca {brand} in blacklist (ignorata 5+ volte)."

    if profile.listing_kind == "classified":
        if not comp or comp.too_cheap or comp.too_volatile:
            reject_reason = reject_reason or (
                "Annuncio usato: senza comps affidabili non stimiamo margine finto."
            )
        elif listing.current_price_eur > comp.best_avg * 0.70:
            reject_reason = reject_reason or (
                f"Prezzo {listing.current_price_eur:.0f} € non sotto comps "
                f"({comp.best_avg:.0f} €): serve ≤70%."
            )

    if profile.key == "ebay_source":
        if not comp or comp.avg_price_vinted <= 0:
            reject_reason = reject_reason or (
                "Nessun prezzo Vinted live per questo titolo (ricerca mercato vuota)."
            )
        elif comp.too_volatile or comp.too_cheap:
            reject_reason = reject_reason or "Comp Vinted troppo volatile o sotto 15 €."
        elif has_channel_negatives(listing, "vinted"):
            reject_reason = reject_reason or "Titolo non adatto a Vinted (keyword negative)."
        elif listing.current_price_eur > comp.avg_price_vinted * 0.62:
            reject_reason = reject_reason or (
                f"Prezzo asta {listing.current_price_eur:.0f} € troppo alto vs Vinted "
                f"({comp.avg_price_vinted:.0f} €): serve margine."
            )

    if profile.key == "vinted_source":
        if not comp or comp.avg_price_ebay <= 0:
            reject_reason = reject_reason or (
                "Nessun prezzo eBay venduti live per questo titolo (ricerca mercato vuota)."
            )
        elif comp.too_volatile or comp.too_cheap:
            reject_reason = reject_reason or "Comp eBay troppo volatile o sotto 15 €."
        elif has_channel_negatives(listing, "ebay"):
            reject_reason = reject_reason or "Titolo non adatto a eBay (keyword negative)."
        elif listing.current_price_eur > comp.avg_price_ebay * 0.55:
            reject_reason = reject_reason or (
                f"Prezzo Vinted {listing.current_price_eur:.0f} € troppo alto vs eBay venduti "
                f"({comp.avg_price_ebay:.0f} €): serve margine."
            )

    pieces = int((listing.extra or {}).get("pieces") or 0)
    if profile.listing_kind == "pallet":
        max_piece = float(os.getenv("MAX_COST_PER_PIECE_EUR", "15"))
        if not dynamic_roi_enabled():
            max_pallet = float(os.getenv("MAX_PALLET_EUR", "400"))
            if listing.current_price_eur > max_pallet:
                reject_reason = reject_reason or f"Bancale sopra cap {max_pallet:.0f} €."
        if pieces > 0:
            cost_piece = listing.current_price_eur / pieces
            if cost_piece > max_piece:
                reject_reason = reject_reason or f"Costo/pezzo {cost_piece:.1f} € sopra {max_piece:.0f} €."

    inferred = infer_resale_value(listing, profile, brand=brand, comp=comp)
    unbundle = (listing.extra or {}).get("unbundle") or {}
    if unbundle.get("total_max_eur", 0) > inferred:
        inferred = float(unbundle["total_max_eur"])
        reasons.append(
            f"Rivendita da manifest ({unbundle.get('source', '?')}): "
            f"{len(unbundle.get('items', []))} articoli"
        )
    if inferred < MIN_RESALE_GUESS_EUR:
        reject_reason = reject_reason or "Stima rivendita sotto 15 €: non conviene."
    risk = category_risk_coefficients(category_tag)
    haircut_pct = profile.lot_haircut + risk.haircut_adj
    if looks_like_bulk_lot(listing.title):
        haircut_pct = max(haircut_pct, 0.15)
    if profile.listing_kind == "pallet" and not (listing.extra or {}).get("packing_list"):
        haircut_pct = max(haircut_pct, 0.22)
        risks.append("Niente packing list: stima a pezzo + cap budget")
    haircut = inferred * haircut_pct
    sellable = inferred - haircut

    inbound = inbound_shipping_eur(listing, profile)
    premium = listing.current_price_eur * profile.buyer_premium
    pickup = pickup_for(listing, profile)
    deposit = deposit_for(listing, profile)
    landed = listing.current_price_eur + premium + inbound + pickup + deposit

    channels: dict[Channel, ChannelEstimate] = {}
    for channel in ("ebay", "vinted", "subito"):
        resale = sellable * CHANNEL_RESALE_FACTOR[channel]
        fee = resale * FEE_PCT[channel]
        outbound = outbound_shipping_eur(listing, profile, channel)
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
    subito_blocked = False
    if profile.key == "ebay_source":
        ebay_blocked = True
        subito_blocked = True
    if profile.key == "vinted_source":
        vinted_blocked = True
    candidates = []
    if not ebay_blocked:
        candidates.append(channels["ebay"])
    if not vinted_blocked:
        candidates.append(channels["vinted"])
    if not subito_blocked and "subito" in channels:
        candidates.append(channels["subito"])
    if not candidates:
        reject_reason = reject_reason or "Keyword negative su eBay e Vinted."
        best = max(channels["ebay"], channels["vinted"], key=lambda item: item.net_profit_eur)
    else:
        best = max(candidates, key=lambda item: item.net_profit_eur)
    best_platform = best.platform

    fee_pct = FEE_PCT[best_platform]
    outbound = outbound_shipping_eur(listing, profile, best_platform)
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
    recommended_max_bid, profit_at_max, roi_at_max = compute_recommended_max_bid(
        listing=listing,
        profile=profile,
        sellable=sellable,
        platform=best_platform,
        inbound=inbound,
        pickup=pickup,
        deposit=deposit,
        roi_pct=min_net_roi_pct() + risk.roi_penalty_pct,
    )
    recommended_max_bid = min(recommended_max_bid, max_buy) * risk.bid_discount
    if risk.bid_discount < 1.0:
        profit_at_max *= risk.bid_discount
        roi_at_max = (profit_at_max / max(landed, 0.01)) * 100 if landed > 0 else roi_at_max

    if best.net_profit_eur < profit_floor and not dynamic_roi_enabled():
        reject_reason = reject_reason or (
            f"Margine netto {best.net_profit_eur:.0f} € sotto {profit_floor:.0f} €."
        )
    if dynamic_roi_enabled():
        ok_roi, roi_reason = passes_dynamic_roi(best, landed_cost=landed)
        if not ok_roi:
            reject_reason = reject_reason or roi_reason

    from capital_allocator import check_allocation

    alloc = check_allocation(category_tag, brand, listing.current_price_eur)
    if not alloc.ok:
        reject_reason = reject_reason or alloc.reason
        risks.append(alloc.reason)

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
        and headroom >= min_headroom_eur
        and max_bid > listing.current_price_eur
        and score >= int(os.getenv("MIN_RESALE_SCORE", "50"))
        and (
            profile.key not in ("ebay_source", "vinted_source")
            or (profile.key == "ebay_source" and best_platform == "vinted")
            or (profile.key == "vinted_source" and best_platform == "ebay")
        )
    )
    if dynamic_roi_enabled():
        ok_roi, _ = passes_dynamic_roi(best, landed_cost=landed)
        viable = viable and ok_roi
    else:
        viable = viable and best.net_profit_eur >= profit_floor and best.margin_pct >= min_margin_pct

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

    velocity = analyze_velocity(tracked, remaining_seconds=remaining_seconds)
    if velocity.note:
        risks.append(velocity.note)

    confidence = _confidence(
        listing=listing,
        profile=profile,
        brand=brand,
        comp=comp,
        best=best,
        velocity=velocity,
    )
    return ClassicEstimate(
        inferred_resale_eur=inferred,
        landed_cost_eur=landed,
        buyer_premium_eur=premium,
        haircut_eur=haircut,
        max_bid_eur=max(0.0, max_bid),
        break_even_bid_eur=max(0.0, break_even),
        recommended_max_bid_eur=max(0.0, recommended_max_bid),
        profit_at_max_bid_eur=profit_at_max,
        roi_at_max_bid_pct=roi_at_max,
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
        comps_product=comp_note or (comp.product if comp else ""),
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
) -> tuple[int, list[str], list[str]]:
    reasons: list[str] = []
    risks: list[str] = []
    profit_part = min(40, max(0, (best.net_profit_eur / max(min_profit_eur, 1)) * 20))
    margin_part = min(25, max(0, best.margin_pct / 2))
    quiet_part = 15 if listing.bids <= 1 else 6 if listing.bids <= 4 else 0
    score = int(profit_part + margin_part + quiet_part)

    if brand:
        if is_premium_brand(brand) or brand in feedback.premium_brands():
            score += 30
            reasons.append(f"Marca premium: {brand}")
        else:
            score += 10
            reasons.append(f"Marca riconosciuta: {brand}")
    if comp and getattr(comp, "super_reliable", False):
        score += 25
        reasons.append("Comps super affidabili (stdev < 15%)")
    elif comp and getattr(comp, "reliable", False):
        score += 15
        reasons.append("Comps affidabili (stdev < 25%)")
    if comp and comp.too_volatile:
        score -= 20
        risks.append("Comps volatili (stdev > 40%)")
    if is_flip_friendly(listing, profile):
        score += 8
        reasons.append("Categoria flip-friendly e spedibile")
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
    velocity: VelocityResult | None = None,
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
    if velocity and velocity.is_hot:
        value -= velocity.confidence_penalty
    return int(min(100, max(0, value)))
