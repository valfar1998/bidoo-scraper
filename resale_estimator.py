"""Stima margine di rivendita per aste Bidoo."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from resale_categories import Competition, Platform, ResaleCategory

Verdict = Literal["conviene", "evita"]


@dataclass(frozen=True)
class ResaleEstimate:
    resale_value_eur: float
    bid_cost_eur: float
    total_cost_eur: float
    platform_fee_eur: float
    shipping_eur: float
    net_profit_eur: float
    margin_pct: float
    estimated_bids: int
    platform: Platform
    score: int
    is_viable: bool
    max_auction_price_eur: float
    max_total_investment_eur: float
    max_additional_bids: int
    max_bid_credits_eur: float
    target_profit_eur: float
    autobid_limit_eur: float
    headroom_eur: float
    break_even_auction_price_eur: float
    verdict: Verdict
    verdict_reason: str


def _competition_penalty(competition: Competition) -> float:
    return {"low": 1.0, "medium": 1.35, "high": 2.0}[competition]


def _platform_fee_pct(platform: Platform) -> float:
    if platform == "vinted":
        return 0.05
    if platform == "ebay":
        return 0.12
    return 0.09


def _target_win_price(retail_value: float, competition: Competition) -> float:
    ratio = {"low": 0.06, "medium": 0.09, "high": 0.12}[competition]
    return retail_value * ratio


def estimate_bids_to_win(
    *,
    retail_value: float,
    current_price_eur: float,
    category: ResaleCategory,
) -> int:
    target = _target_win_price(retail_value, category.competition)
    gap_eur = max(0.0, target - current_price_eur)
    base_bids = int(gap_eur / 0.01)
    adjusted = int(base_bids * category.bid_factor * _competition_penalty(category.competition))
    return max(15, adjusted)


def brand_resale_boost(name: str, slug: str) -> float:
    text = f"{name} {slug}".lower()
    boosts = (
        ("lego", 0.08),
        ("kenwood", 0.06),
        ("smeg", 0.06),
        ("rowenta", 0.05),
        ("braun", 0.05),
        ("philips", 0.04),
        ("chicco", 0.05),
        ("peg perego", 0.05),
        ("fossil", 0.05),
        ("casio", 0.04),
        ("garmin", 0.04),
        ("nutribullet", 0.05),
        ("kitchenaid", 0.06),
        ("dyson", -0.05),
        ("apple", -0.10),
        ("iphone", -0.10),
        ("playstation", -0.10),
        ("ps5", -0.10),
    )
    boost = 0.0
    for keyword, value in boosts:
        if keyword in text:
            boost += value
    return max(-0.15, min(0.12, boost))


def _bids_between_prices(from_price_eur: float, to_price_eur: float) -> int:
    if to_price_eur <= from_price_eur:
        return 0
    return int(round((to_price_eur - from_price_eur) / 0.01))


def compute_price_caps(
    *,
    resale_value_eur: float,
    current_price_eur: float,
    platform: Platform,
    bid_cost_per_bid: float,
    shipping_eur: float,
    target_profit_eur: float,
) -> tuple[float, float, int]:
    """Ritorna (prezzo asta max, investimento totale max asta+puntate, puntate max)."""
    platform_fee = resale_value_eur * _platform_fee_pct(platform)
    max_total_investment = (
        resale_value_eur - platform_fee - shipping_eur - target_profit_eur
    )

    # max_p: prezzo asta finale tale che asta + puntate (da prezzo attuale) = investimento max
    # max_total = max_p + 100 * (max_p - current) * bid_cost
    bid_slope = 100 * bid_cost_per_bid
    numerator = max_total_investment + bid_slope * current_price_eur
    denominator = 1 + bid_slope
    max_auction_price = numerator / denominator if denominator > 0 else 0.0
    max_additional_bids = _bids_between_prices(current_price_eur, max_auction_price)

    return max_auction_price, max_total_investment, max_additional_bids


def _round_down_price(value: float) -> float:
    return math.floor(value * 100) / 100


def classify_action(
    *,
    current_price_eur: float,
    is_viable: bool,
    max_auction_price_eur: float,
    min_price_headroom_eur: float,
    net_profit_eur: float,
    target_profit_eur: float,
) -> tuple[Verdict, str]:
    headroom = max_auction_price_eur - current_price_eur
    autobid = _round_down_price(max_auction_price_eur)

    if current_price_eur > max_auction_price_eur:
        return (
            "evita",
            f"Prezzo asta ({current_price_eur:.2f} €) già sopra il limite ({autobid:.2f} €).",
        )
    if not is_viable:
        return (
            "evita",
            f"Margine stimato insufficiente ({net_profit_eur:.0f} €, "
            f"soglia {target_profit_eur:.0f} €).",
        )
    if headroom < min_price_headroom_eur:
        return (
            "evita",
            f"Solo {headroom:.2f} € di margine prima del limite — rischio troppo alto.",
        )
    return (
        "conviene",
        f"Imposta AutoPuntata a max {autobid:.2f} € e non superare "
        f"{int(headroom / 0.01)} rilanci (~{headroom:.2f} € di salita prezzo).",
    )


def estimate_resale(
    *,
    retail_value: float,
    current_price_eur: float,
    category: ResaleCategory,
    name: str,
    slug: str,
    bid_cost_per_bid: float,
    min_profit_eur: float,
    min_margin_pct: float,
    shipping_eur: float,
    min_price_headroom_eur: float = 0.50,
    quiet_bonus: bool = False,
) -> ResaleEstimate:
    brand_boost = brand_resale_boost(name, slug)
    resale_ratio = min(0.70, max(0.30, category.resale_ratio + brand_boost))
    resale_value = retail_value * resale_ratio

    estimated_bids = estimate_bids_to_win(
        retail_value=retail_value,
        current_price_eur=current_price_eur,
        category=category,
    )
    bid_cost = estimated_bids * bid_cost_per_bid
    total_cost = current_price_eur + bid_cost
    platform_fee = resale_value * _platform_fee_pct(category.platform)
    net_profit = resale_value - total_cost - platform_fee - shipping_eur
    margin_pct = (net_profit / total_cost * 100) if total_cost > 0 else 0.0

    score = _resale_score(
        net_profit=net_profit,
        margin_pct=margin_pct,
        category=category,
        brand_boost=brand_boost,
        quiet_bonus=quiet_bonus,
        min_profit_eur=min_profit_eur,
    )

    is_viable = net_profit >= min_profit_eur and margin_pct >= min_margin_pct

    max_auction_price, max_total_investment, max_additional_bids = compute_price_caps(
        resale_value_eur=resale_value,
        current_price_eur=current_price_eur,
        platform=category.platform,
        bid_cost_per_bid=bid_cost_per_bid,
        shipping_eur=shipping_eur,
        target_profit_eur=min_profit_eur,
    )
    break_even_price, _, _ = compute_price_caps(
        resale_value_eur=resale_value,
        current_price_eur=current_price_eur,
        platform=category.platform,
        bid_cost_per_bid=bid_cost_per_bid,
        shipping_eur=shipping_eur,
        target_profit_eur=0.0,
    )
    autobid_limit = _round_down_price(max_auction_price)
    headroom = max_auction_price - current_price_eur
    max_bid_credits = max_additional_bids * bid_cost_per_bid
    verdict, verdict_reason = classify_action(
        current_price_eur=current_price_eur,
        is_viable=is_viable,
        max_auction_price_eur=max_auction_price,
        min_price_headroom_eur=min_price_headroom_eur,
        net_profit_eur=net_profit,
        target_profit_eur=min_profit_eur,
    )

    return ResaleEstimate(
        resale_value_eur=resale_value,
        bid_cost_eur=bid_cost,
        total_cost_eur=total_cost,
        platform_fee_eur=platform_fee,
        shipping_eur=shipping_eur,
        net_profit_eur=net_profit,
        margin_pct=margin_pct,
        estimated_bids=estimated_bids,
        platform=category.platform,
        score=score,
        is_viable=is_viable,
        max_auction_price_eur=max_auction_price,
        max_total_investment_eur=max_total_investment,
        max_additional_bids=max_additional_bids,
        max_bid_credits_eur=max_bid_credits,
        target_profit_eur=min_profit_eur,
        autobid_limit_eur=autobid_limit,
        headroom_eur=headroom,
        break_even_auction_price_eur=break_even_price,
        verdict=verdict,
        verdict_reason=verdict_reason,
    )


def _resale_score(
    *,
    net_profit: float,
    margin_pct: float,
    category: ResaleCategory,
    brand_boost: float,
    quiet_bonus: bool,
    min_profit_eur: float,
) -> int:
    profit_component = min(40, max(0, (net_profit / max(min_profit_eur, 1)) * 20))
    margin_component = min(25, max(0, margin_pct / 2))
    competition_component = {"low": 15, "medium": 8, "high": 0}[category.competition]
    brand_component = min(20, max(0, (brand_boost + 0.05) * 100))
    quiet_component = 10 if quiet_bonus else 0
    total = profit_component + margin_component + competition_component + brand_component + quiet_component
    return int(min(100, max(0, total)))
