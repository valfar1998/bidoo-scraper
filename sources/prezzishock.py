from __future__ import annotations

from http_fetch import SessionFetcher
from listing import SourceListing
from php_auction import parse_php_auction_table

BASE = "https://www.prezzishock.it/"
URLS = (
    "https://www.prezzishock.it/auctions_show.php?option=ending&limit=80",
    "https://www.prezzishock.it/auctions_show.php?start=80&limit=80&option=ending",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    seen: dict[str, SourceListing] = {}
    for url in URLS:
        html = fetcher.get_text(url)
        for item in parse_php_auction_table(html, source="prezzishock", base_url=BASE):
            seen[item.listing_id] = item
    return list(seen.values())
