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


def env_queries(name: str, fallback: tuple[str, ...] = DEFAULT_FLIP_QUERIES) -> list[str]:
    raw = os.getenv(name, "")
    if raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return list(fallback)
