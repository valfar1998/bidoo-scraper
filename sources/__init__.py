"""Registry dei cataloghi pubblici."""

from __future__ import annotations

from collections.abc import Callable

from http_fetch import SessionFetcher
from listing import SourceListing

from . import (
    antiebay,
    astagiudiziaria,
    bstock,
    catawiki,
    ebay_source,
    gobid,
    industrial_discount,
    merkandi,
    prezzishock,
    remundo,
    stocklots24,
    subito,
    surplex,
    vinted_source,
    wallapop,
)

Fetcher = Callable[[SessionFetcher], list[SourceListing]]

FETCHERS: dict[str, Fetcher] = {
    "prezzishock": prezzishock.fetch_listings,
    "antiebay": antiebay.fetch_listings,
    "catawiki": catawiki.fetch_listings,
    "astagiudiziaria": astagiudiziaria.fetch_listings,
    "gobid": gobid.fetch_listings,
    "surplex": surplex.fetch_listings,
    "industrial_discount": industrial_discount.fetch_listings,
    "remundo": remundo.fetch_listings,
    "bstock": bstock.fetch_listings,
    "merkandi": merkandi.fetch_listings,
    "stocklots24": stocklots24.fetch_listings,
    "ebay_source": ebay_source.fetch_listings,
    "wallapop": wallapop.fetch_listings,
    "vinted_source": vinted_source.fetch_listings,
    "subito": subito.fetch_listings,
}


def fetch_source(source: str, fetcher: SessionFetcher) -> list[SourceListing]:
    try:
        func = FETCHERS[source]
    except KeyError as exc:
        raise ValueError(f"Nessun adapter per {source}") from exc
    return func(fetcher)
