"""Routing alert Telegram su topic (forum) per categoria e rischio."""

from __future__ import annotations

import os
from typing import Any


def topics_enabled() -> bool:
    return os.getenv("TELEGRAM_TOPICS_ENABLED", "false").lower() in ("1", "true", "yes")


def _topic_id(env_key: str) -> int | None:
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def resolve_alert_topic(
    *,
    category_tag: str = "",
    listing_kind: str = "",
    confidence: int = 50,
    is_viable: bool = True,
    risks: list[str] | None = None,
    score: int = 50,
) -> int | None:
    """Restituisce message_thread_id per supergruppo Telegram, o None per chat default."""
    if not topics_enabled():
        return None

    risks = risks or []
    risky = (
        not is_viable
        or confidence < int(os.getenv("TELEGRAM_TOPIC_RISKY_CONFIDENCE", "55"))
        or score < int(os.getenv("TELEGRAM_TOPIC_RISKY_SCORE", "55"))
        or len(risks) >= 2
    )
    if risky:
        return _topic_id("TELEGRAM_TOPIC_RISKY")

    if listing_kind == "pallet":
        topic = _topic_id("TELEGRAM_TOPIC_PALLET")
        if topic:
            return topic

    electronics = {
        item.strip().lower()
        for item in os.getenv(
            "TELEGRAM_TOPIC_ELECTRONICS_CATEGORIES",
            "elettronica,smartwatch,orologi,videogiochi",
        ).split(",")
        if item.strip()
    }
    if (category_tag or "").lower() in electronics and confidence >= int(
        os.getenv("TELEGRAM_TOPIC_ELECTRONICS_MIN_CONFIDENCE", "70")
    ):
        topic = _topic_id("TELEGRAM_TOPIC_ELECTRONICS")
        if topic:
            return topic

    fashion = {
        item.strip().lower()
        for item in os.getenv(
            "TELEGRAM_TOPIC_FASHION_CATEGORIES", "moda,sneaker,borse,abbigliamento"
        ).split(",")
        if item.strip()
    }
    if (category_tag or "").lower() in fashion:
        topic = _topic_id("TELEGRAM_TOPIC_FASHION")
        if topic:
            return topic

    return _topic_id("TELEGRAM_TOPIC_DEFAULT")


def resolve_ops_topic(kind: str = "inventory") -> int | None:
    """Topic per messaggi ops (inventario, repricing, tax)."""
    if not topics_enabled():
        return None
    mapping = {
        "inventory": "TELEGRAM_TOPIC_INVENTORY",
        "repricing": "TELEGRAM_TOPIC_REPRICING",
        "tax": "TELEGRAM_TOPIC_TAX",
        "ops": "TELEGRAM_TOPIC_OPS",
    }
    key = mapping.get(kind, "TELEGRAM_TOPIC_OPS")
    return _topic_id(key) or _topic_id("TELEGRAM_TOPIC_DEFAULT")


def topic_config_summary() -> dict[str, Any]:
    keys = [
        "TELEGRAM_TOPIC_ELECTRONICS",
        "TELEGRAM_TOPIC_PALLET",
        "TELEGRAM_TOPIC_RISKY",
        "TELEGRAM_TOPIC_FASHION",
        "TELEGRAM_TOPIC_INVENTORY",
        "TELEGRAM_TOPIC_REPRICING",
        "TELEGRAM_TOPIC_TAX",
        "TELEGRAM_TOPIC_DEFAULT",
    ]
    return {key: _topic_id(key) for key in keys if _topic_id(key)}
