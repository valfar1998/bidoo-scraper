"""Query di ricerca condivise per fonti a catalogo (non aste a tempo)."""

from __future__ import annotations

import os

DEFAULT_FLIP_QUERIES = (
    "casio",
    "garmin",
    "makita",
    "xiaomi",
    "lego",
    "kenwood",
    "nike",
    "dyson",
)

# Marche/categorie con comps Vinted in data/comps.csv (no utensili pesanti).
VINTED_FLIP_QUERIES = (
    "casio g-shock",
    "garmin orologio",
    "xiaomi",
    "lego",
    "kenwood",
    "nike sneaker",
    "adidas scarpe",
    "lego technic",
    "nintendo switch",
    "gopro",
    "seiko",
    "fossil orologio",
    "ray-ban",
    "nespresso",
    "dyson",
    "philips",
    "chicco",
    "furla borsa",
)


def env_queries(name: str, fallback: tuple[str, ...] = DEFAULT_FLIP_QUERIES) -> list[str]:
    raw = os.getenv(name, "")
    if raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(fallback)
