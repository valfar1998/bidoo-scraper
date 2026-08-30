"""Regole di filtro per aste rivendibili su Vinted/eBay."""

from __future__ import annotations

import re
from dataclasses import dataclass

from resale_categories import ResaleCategory


DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    r"\bpuntate\b",
    r"^\d+\s*puntate",
    r"\bbuon[oi]\b",
    r"\bvoucher\b",
    r"\bcrediti\b",
    r"\bforziere\b",
    r"\bbidpack\b",
    r"_puntate_",
    r"\blotteria\b",
    r"\bestrazione\b",
)

# Prodotti iper-competitivi su Bidoo: difficile vincere con margine.
HYPER_COMPETITIVE_PATTERNS: tuple[str, ...] = (
    r"\biphone\b",
    r"\bipad\b",
    r"\bmacbook\b",
    r"\bairpods?\b",
    r"\bplaystation\b",
    r"\bps5\b",
    r"\bps4\b",
    r"\bxbox\b",
    r"\bnintendo\s*switch\b",
    r"\bsamsung\s*galaxy\b",
    r"\bdyson\s*supersonic\b",
    r"\bpixel\s*\d+",
    r"\bdyson\s*(v\d+|airwrap|supersonic)\b",
    r"\brolex\b",
    r"\blouis\s*vuitton\b",
    r"\bgucci\b",
    r"\bapple\s*watch\b",
    r"\bsmartphone\b",
    r"\btablet\b",
    r"\btv\b",
    r"\btelevisore\b",
    r"\bconsole\b",
)


@dataclass(frozen=True)
class PriceTier:
    min_retail: float
    max_ratio: float


def parse_exclude_patterns(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]


def _matches_any(text: str, patterns: tuple[str, ...] | list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def is_excluded_auction(
    name: str,
    slug: str,
    extra_patterns: list[str] | None = None,
) -> bool:
    text = f"{name} {slug}".lower().replace("_", " ")
    patterns = list(DEFAULT_EXCLUDE_PATTERNS) + (extra_patterns or [])
    return _matches_any(text, patterns)


def is_hyper_competitive(name: str, slug: str, price_eur: float = 0.0) -> bool:
    if 0 < price_eur < 20:
        return False
    text = f"{name} {slug}".lower().replace("_", " ")
    return _matches_any(text, HYPER_COMPETITIVE_PATTERNS)


def fits_category_retail_band(
    retail_value: float,
    category: ResaleCategory,
) -> bool:
    return category.min_retail <= retail_value <= category.max_retail


def max_price_ratio_for_retail(
    retail_value: float,
    *,
    min_retail_value: float,
    high_value_threshold: float,
    ratio_high: float,
    ratio_mid: float,
    ratio_default: float,
) -> float:
    if retail_value >= high_value_threshold:
        return ratio_high
    if retail_value >= min_retail_value:
        return ratio_mid
    return ratio_default
