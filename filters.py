"""Regole di filtro per individuare aste prodotto interessanti."""

from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    r"\bpuntate\b",
    r"^\d+\s*puntate",
    r"\bbuon[oi]\b",
    r"\bvoucher\b",
    r"\bcrediti\b",
    r"\bforziere\b",
    r"\bbidpack\b",
    r"_puntate_",
)


@dataclass(frozen=True)
class PriceTier:
    min_retail: float
    max_ratio: float


def parse_exclude_patterns(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_excluded_auction(
    name: str,
    slug: str,
    extra_patterns: list[str] | None = None,
) -> bool:
    text = f"{name} {slug}".lower().replace("_", " ")
    patterns = list(DEFAULT_EXCLUDE_PATTERNS) + (extra_patterns or [])
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


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
