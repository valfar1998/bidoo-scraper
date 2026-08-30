"""Annuncio/lottо normalizzato, indipendente dal sito sorgente."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceListing:
    source: str
    listing_id: str
    title: str
    url: str
    current_price_eur: float
    shipping_eur: float = 0.0
    retail_hint_eur: float = 0.0
    buy_now_eur: float | None = None
    bids: int = 0
    remaining_text: str = ""
    location: str = ""
    category_tag: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def price_cents(self) -> int:
        return int(round(self.current_price_eur * 100))

    @property
    def history_key(self) -> str:
        return f"{self.source}:{self.listing_id}"
