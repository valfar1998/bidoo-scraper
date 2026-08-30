from __future__ import annotations

from http_fetch import SessionFetcher
from listing import SourceListing
from php_auction import parse_php_auction_table

BASE = "https://www.prezzishock.it/"
URLS = (
    "https://www.prezzishock.it/auctions_show.php?option=ending&limit=100",
    "https://www.prezzishock.it/auctions_show.php?start=100&limit=100&option=ending",
    "https://www.prezzishock.it/auctions_show.php?start=200&limit=100&option=ending",
    "https://www.prezzishock.it/auctions_show.php?option=new&limit=80",
    "https://www.prezzishock.it/auctions_show.php?option=featured&limit=40",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    fetcher.warm(BASE)
    seen: dict[str, SourceListing] = {}
    for url in URLS:
        try:
            html = fetcher.get_text(url, referer=BASE)
        except Exception as exc:
            print(f"[prezzishock] skip {url}: {exc}")
            continue
        for item in parse_php_auction_table(html, source="prezzishock", base_url=BASE):
            seen[item.listing_id] = item
    if not seen:
        print("[prezzishock] Catalogo vuoto o bloccato.")
    return list(seen.values())
