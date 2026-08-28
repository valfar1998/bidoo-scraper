"""Client leggero per leggere aste pubbliche da Bidoo (solo monitoraggio)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_BASE_URL = "https://it.bidoo.com/"


@dataclass(frozen=True)
class Auction:
    auction_id: str
    name: str
    slug: str
    retail_value: float
    url: str


@dataclass(frozen=True)
class LiveAuction:
    auction_id: str
    state: str
    expires_at: int
    price_cents: int
    winner: str
    bid_type: str

    @property
    def price_eur(self) -> float:
        return self.price_cents / 100


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def parse_euro_price(text: str) -> float | None:
    match = re.search(r"(\d+[.,]\d{2})", text.replace("\xa0", " "))
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def fetch_auctions(session: requests.Session, base_url: str) -> list[Auction]:
    response = session.get(base_url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    auctions: list[Auction] = []
    seen: set[str] = set()

    for card in soup.select("div[id^='divAsta']"):
        auction_id = card.get("data-id") or card.get("id", "").replace("divAsta", "")
        if not auction_id or auction_id in seen:
            continue

        name_el = card.select_one("a.name")
        name = name_el.get_text(strip=True) if name_el else f"Asta {auction_id}"

        slug = card.get("data-url", "")
        if not slug:
            link = card.select_one("a[href*='auction.php?a=']")
            if link and link.get("href"):
                slug = link["href"].split("a=", 1)[-1]

        retail_el = card.select_one(".reserved-price")
        if not retail_el:
            continue
        retail_value = parse_euro_price(retail_el.get_text(" ", strip=True))
        if retail_value is None:
            continue

        url = urljoin(base_url, f"auction.php?a={slug}") if slug else base_url
        auctions.append(
            Auction(
                auction_id=auction_id,
                name=name,
                slug=slug,
                retail_value=retail_value,
                url=url,
            )
        )
        seen.add(auction_id)

    return auctions


def parse_live_response(payload: str) -> tuple[int, list[LiveAuction]]:
    payload = payload.strip()
    if "*" not in payload:
        raise ValueError("Risposta data.php non valida")

    server_time_str, body = payload.split("*", 1)
    server_time = int(server_time_str.split("{")[0].split("|")[0])

    match = re.search(r"\((.*)\)", body)
    if not match:
        return server_time, []

    live_auctions: list[LiveAuction] = []
    for chunk in match.group(1).split("#"):
        if not chunk.strip():
            continue
        parts = chunk.split(";")
        if len(parts) < 6:
            continue
        live_auctions.append(
            LiveAuction(
                auction_id=parts[0],
                state=parts[1],
                expires_at=int(parts[2]),
                price_cents=int(parts[3]),
                winner=parts[4],
                bid_type=parts[5],
            )
        )

    return server_time, live_auctions


def fetch_live_auctions(
    session: requests.Session,
    base_url: str,
    auction_ids: list[str],
) -> tuple[int, list[LiveAuction]]:
    if not auction_ids:
        return 0, []

    ids_param = ",".join(auction_ids)
    url = urljoin(base_url, f"data.php?LISTID={ids_param}")
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return parse_live_response(response.text)


def seconds_remaining(server_time: int, live: LiveAuction) -> int:
    return max(0, live.expires_at - server_time)
