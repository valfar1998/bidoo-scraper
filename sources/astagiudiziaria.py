"""Astagiudiziaria: ricerca mobili con scadenza dinamica (oggi → max domani)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from http_fetch import SessionFetcher
from listing import SourceListing
from money import parse_euro, remaining_from_any

# Categorie rivendibili (niente auto/moto: le avevi escluso).
DEFAULT_CATEGORIES = (
    "ARTE -OREFICERIA - OROLOGERIA- ANTIQUARIATO",
    "INFORMATICA E ELETTRONICA",
    "ARREDAMENTO - ELETTRODOMESTICI",
    "ABBIGLIAMENTO E CALZATURE",
    "ALTRA CATEGORIA",
)

INSERZIONE_RE = re.compile(r"/inserzioni/[^\"'#?\s]+-(\d{5,})", re.I)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    override = [u.strip() for u in os.getenv("ASTAGIUDIZIARIA_URLS", "").split(",") if u.strip()]
    seen: dict[str, SourceListing] = {}
    fetcher.warm("https://www.astagiudiziaria.com/")

    if override:
        urls = override
        print(f"[astagiudiziaria] Uso ASTAGIUDIZIARIA_URLS ({len(urls)} URL).")
    else:
        start, end = _sale_window()
        days = max(0, int(os.getenv("ASTAGIUDIZIARIA_DAYS_AHEAD", "1")))
        print(
            f"[astagiudiziaria] Scadenza dal {start.strftime('%d/%m/%Y')} "
            f"al {end.strftime('%d/%m/%Y')} (oggi + {days}g)."
        )
        max_pages = max(1, int(os.getenv("ASTAGIUDIZIARIA_PAGES", "3")))
        urls = [_search_url(start, end, page=p) for p in range(1, max_pages + 1)]

    for url in urls:
        try:
            html = fetcher.get_text(url, referer="https://www.astagiudiziaria.com/")
        except Exception as exc:
            print(f"[astagiudiziaria] {exc}")
            continue
        items = _parse(html)
        if not items:
            # Pagine successive vuote: stop.
            if "page=" in url and "page=1" not in url.split("&")[0]:
                break
            continue
        for item in items:
            seen[item.listing_id] = item
        print(f"[astagiudiziaria] +{len(items)} (totale unici: {len(seen)}).")

    if not seen:
        print(
            "[astagiudiziaria] Nessun lotto in scadenza nella finestra. "
            "Se serve più giorno: ASTAGIUDIZIARIA_DAYS_AHEAD=2. "
            "MyAsta resta gratis per alert email."
        )
    return list(seen.values())


def _sale_window() -> tuple[datetime, datetime]:
    """Inizio oggi 00:00 → fine di (oggi + DAYS_AHEAD) 23:59:59, Europe/Rome."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:
        now = datetime.now().astimezone()
    days = max(0, int(os.getenv("ASTAGIUDIZIARIA_DAYS_AHEAD", "1")))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days + 1) - timedelta(seconds=1)
    return start, end


def _categories() -> list[str]:
    raw = os.getenv("ASTAGIUDIZIARIA_CATEGORIES", "").strip()
    if raw:
        return [c.strip() for c in raw.split("|") if c.strip()]
    return list(DEFAULT_CATEGORIES)


def _search_url(start: datetime, end: datetime, *, page: int = 1) -> str:
    """Stessa forma della ricerca UI: filter[data_vendita_search] = unix Rome."""
    rpp = max(20, min(100, int(os.getenv("ASTAGIUDIZIARIA_RPP", "50"))))
    params: list[tuple[str, str]] = [
        ("filter[genre][0]", "MOBILI"),
    ]
    for i, cat in enumerate(_categories()):
        params.append((f"filter[category][{i}]", cat))
    params.extend(
        [
            ("filter[status][0]", "In vendita"),
            ("filter[data_vendita_search][0]", str(int(start.timestamp()))),
            ("filter[data_vendita_search][1]", str(int(end.timestamp()))),
            ("filter[visibile_su][0]", "1"),
            ("filter[position]", ""),
            ("query", ""),
            ("page", str(page)),
            ("rpp", str(rpp)),
        ]
    )
    return "https://www.astagiudiziaria.com/ricerca/mobili?" + urlencode(params)


def _parse(html: str) -> list[SourceListing]:
    if not html or "nessun risultato" in html.lower():
        return []
    soup = BeautifulSoup(html, "html.parser")
    for nav in soup.select("nav, header, footer, .breadcrumb"):
        nav.decompose()
    listings: list[SourceListing] = []
    seen_ids: set[str] = set()

    for a in soup.select('a[href*="/inserzioni/"]'):
        href = a.get("href") or ""
        match = INSERZIONE_RE.search(href)
        if not match:
            continue
        listing_id = match.group(1)
        if listing_id in seen_ids:
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 8:
            continue
        parent = a.find_parent(["article", "div", "li", "tr"])
        blob = parent.get_text(" ", strip=True) if parent else title
        # Preferisci offerta minima / prezzo base dal card.
        price = (
            parse_euro(_after(blob, "Offerta Minima"))
            or parse_euro(_after(blob, "Prezzo base"))
            or parse_euro(blob)
            or parse_euro(title)
            or 0.0
        )
        if price <= 0:
            continue
        seen_ids.add(listing_id)
        url = href if href.startswith("http") else "https://www.astagiudiziaria.com" + href
        listings.append(
            SourceListing(
                source="astagiudiziaria",
                listing_id=listing_id,
                title=title[:180],
                url=url.split("?")[0],
                current_price_eur=price,
                remaining_seconds=remaining_from_any(blob),
            )
        )

    if listings:
        return listings[:100]

    for script in soup.select('script[type="application/ld+json"]'):
        listings.extend(_from_ldjson(script.string or ""))
    return listings[:80]


def _after(text: str, label: str) -> str:
    idx = text.lower().find(label.lower())
    if idx < 0:
        return ""
    return text[idx : idx + 40]


def _from_ldjson(raw: str) -> list[SourceListing]:
    import json

    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    found: list[SourceListing] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("headline") or "").strip()
        url = str(item.get("url") or "")
        offers = item.get("offers") or {}
        price = 0.0
        if isinstance(offers, dict):
            try:
                price = float(offers.get("price") or 0)
            except (TypeError, ValueError):
                price = parse_euro(str(offers.get("price") or "")) or 0.0
        if not title or not url or price <= 0:
            continue
        match = INSERZIONE_RE.search(url)
        listing_id = match.group(1) if match else re.sub(r"\W+", "-", url)[-40:]
        found.append(
            SourceListing(
                source="astagiudiziaria",
                listing_id=listing_id,
                title=title[:180],
                url=url,
                current_price_eur=price,
            )
        )
    return found
