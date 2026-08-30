from __future__ import annotations

from http_fetch import SessionFetcher
from listing import SourceListing
from php_auction import parse_php_auction_table

BASE = "https://www.antiebay.it/"
URLS = (
    "https://www.antiebay.it/auctions_show.php?option=ending&limit=80",
    "https://www.antiebay.it/auctions_show.php?start=80&limit=80&option=ending",
    "https://www.antiebay.it/auctions_show.php?option=new&limit=40",
    "https://www.antiebay.it/auctions_show.php?option=featured&limit=40",
    "https://www.antiebay.it/categories.php?parent_id=1867",
    "https://www.antiebay.it/categories.php?parent_id=1887",
    "https://www.antiebay.it/categories.php?parent_id=1895",
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    fetcher.warm(BASE)
    seen: dict[str, SourceListing] = {}
    for url in URLS:
        try:
            html = fetcher.get_text(url)
        except Exception as exc:
            print(f"[antiebay] skip {url}: {exc}")
            continue
        for item in parse_php_auction_table(html, source="antiebay", base_url=BASE):
            seen[item.listing_id] = item
    if not seen:
        print(
            "[antiebay] Nessuna asta aperta nel catalogo pubblico "
            "(molte schede risultano 'ASTA TERMINATA')."
        )
    return list(seen.values())
