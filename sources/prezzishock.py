from __future__ import annotations

from http_fetch import SessionFetcher
from listing import SourceListing
from php_auction import parse_php_auction_table

BASE = "https://www.prezzishock.it/"
URLS = (
    "https://www.prezzishock.it/auctions_show.php?option=ending&limit=50",
    "https://www.prezzishock.it/auctions_show.php?option=new&limit=50",
    "https://www.prezzishock.it/categories.php?parent_id=3176",  # elettronica
    "https://www.prezzishock.it/categories.php?parent_id=3181",  # informatica
    "https://www.prezzishock.it/categories.php?parent_id=3192",  # videogiochi
    "https://www.prezzishock.it/categories.php?parent_id=3167",  # casa
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    seen: dict[str, SourceListing] = {}
    for url in URLS:
        html = fetcher.get_text(url)
        for item in parse_php_auction_table(html, source="prezzishock", base_url=BASE):
            seen[item.listing_id] = item
    return list(seen.values())
