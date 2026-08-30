from __future__ import annotations

import re

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any

HOME = "https://www.industrialdiscount.it/"


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    html = fetcher.get_text(HOME)
    soup = BeautifulSoup(html, "html.parser")
    auction_urls = []
    for anchor in soup.select("a[href*='/aste/']"):
        href = anchor.get("href") or ""
        if "/aste/" in href and href.count("/") >= 2:
            full = href if href.startswith("http") else "https://www.industrialdiscount.it" + href
            if full not in auction_urls:
                auction_urls.append(full)

    seen: dict[str, SourceListing] = {}
    for url in auction_urls[:8]:
        try:
            page = fetcher.get_text(url)
        except Exception as exc:
            print(f"[industrial_discount] {exc}")
            continue
        for item in _parse_lots(page):
            seen[item.listing_id] = item
    if not seen:
        for item in _parse_lots(html):
            seen[item.listing_id] = item
    return list(seen.values())


def _parse_lots(html: str) -> list[SourceListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[SourceListing] = []
    for anchor in soup.select("a[href*='/lotti/']"):
        href = anchor.get("href") or ""
        parent = anchor.find_parent(["article", "div", "li", "tr"])
        title = _card_title(anchor, parent)
        listing_id = href.rstrip("/").split("/")[-1]
        slug_title = _title_from_slug(listing_id)
        if len(title) < 8 or re.match(r"^(vedi lotto|da |asta )", title, re.I) or "CET" in title:
            title = slug_title or title
        if len(title) < 8:
            continue
        blob = parent.get_text(" ", strip=True) if parent else title
        price = parse_euro(blob) or _parse_from_da(blob)
        remaining = remaining_from_any(blob)
        listings.append(
            SourceListing(
                source="industrial_discount",
                listing_id=listing_id,
                title=title[:180],
                url=href if href.startswith("http") else "https://www.industrialdiscount.it" + href,
                current_price_eur=price or 0.0,
                remaining_seconds=remaining,
            )
        )
    return listings


def _card_title(anchor, parent) -> str:
    candidates: list[str] = []
    nodes = parent.select("a") if parent is not None else [anchor]
    for node in nodes:
        text = node.get_text(" ", strip=True)
        if len(text) < 8:
            continue
        if re.match(
            r"^(da |asta |vedi |fai |aggiungi|-?\d|lotto )",
            text,
            re.I,
        ):
            continue
        if "€" in text or "eur" in text.lower():
            continue
        candidates.append(text)
    if candidates:
        return max(candidates, key=len)[:180]
    return _guess_title(parent.get_text(" ", strip=True) if parent else anchor.get_text(" ", strip=True))


def _guess_title(blob: str) -> str:
    parts = [part.strip() for part in re.split(r"\s{2,}|[|\n]", blob) if len(part.strip()) > 8]
    for part in parts:
        if not re.match(r"^(da |asta |lotto |-?\d)", part, re.I):
            return part[:180]
    return blob[:180]


def _title_from_slug(listing_id: str) -> str:
    slug = re.sub(r"-\d+$", "", listing_id)
    pretty = slug.replace("-", " ").strip()
    return pretty[:180]


def _parse_from_da(text: str) -> float | None:
    match = re.search(r"da\s+([\d.]+)", text, re.I)
    if not match:
        return None
    raw = match.group(1).replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None

