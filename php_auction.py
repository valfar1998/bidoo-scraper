"""Parser per marketplace PHP (PrezziShock, Antiebay)."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from listing import SourceListing
from money import parse_euro, remaining_to_seconds

AUCTION_ID_RE = re.compile(r"(?:name,|,)(\d+),auction_id")
ANTIEBAY_ID_RE = re.compile(r"auction_details,[^,]*,(\d+)\.html", re.I)
BUYOUT_ID_RE = re.compile(r"/(\d+),auction_id,")


def parse_php_auction_table(
    html: str,
    *,
    source: str,
    base_url: str,
) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[SourceListing] = []
    seen: set[str] = set()

    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        title = anchor.get_text(" ", strip=True)
        listing_id = _extract_id(href)
        if not listing_id or not title or listing_id in seen:
            continue
        if "auction_details" not in href and "auction_id" not in href:
            continue
        if "buy_out" in href:
            continue

        row = anchor.find_parent("tr")
        if row is None:
            continue
        cells = [td.get_text(" ", strip=True) for td in row.find_all("td")]
        blob = " | ".join(cells)
        if re.search(r"asta\s+terminat", blob, re.I):
            continue

        price = _price_from_cells(cells)
        if price is None or price <= 0:
            continue

        shipping = _shipping_from_cells(cells)
        bids = _bids_from_text(blob)
        remaining = cells[-1] if cells else ""
        url = href if href.startswith("http") else urljoin(base_url, href.lstrip("/"))
        if url.startswith("//"):
            url = "https:" + href

        remaining_s = remaining_to_seconds(remaining)
        seen.add(listing_id)
        listings.append(
            SourceListing(
                source=source,
                listing_id=listing_id,
                title=title,
                url=url,
                current_price_eur=price,
                shipping_eur=shipping or 0.0,
                bids=bids,
                remaining_text=remaining,
                remaining_seconds=remaining_s or None,
                extra={"remaining_s": remaining_s},
            )
        )
    return listings


def _extract_id(href: str) -> str:
    for pattern in (AUCTION_ID_RE, ANTIEBAY_ID_RE, BUYOUT_ID_RE):
        match = pattern.search(href)
        if match:
            return match.group(1)
    return ""


def _price_from_cells(cells: list[str]) -> float | None:
    for cell in cells:
        if "EUR" not in cell.upper() and "€" not in cell:
            continue
        # "EUR 19,00 0" → prezzo + n. offerte attaccati
        clipped = re.sub(r"(\d)\s+\d+\s*$", r"\1", cell)
        value = parse_euro(clipped)
        if value is not None:
            return value
    return None


def _shipping_from_cells(cells: list[str]) -> float | None:
    for cell in cells:
        if re.search(r"vedi|descrizione|^-$", cell, re.I):
            continue
        if "EUR" in cell.upper() or "€" in cell:
            # skip the current-price column if it also has bid count
            if re.search(r"\d\s+\d+$", cell):
                continue
            value = parse_euro(cell)
            if value is not None and value < 80:
                return value
    return None


def _bids_from_text(text: str) -> int:
    match = re.search(r"(\d+)\s*offerte", text, re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"EUR[\d.,]+\s+(\d+)\s*$", text)
    if match:
        return int(match.group(1))
    return 0
