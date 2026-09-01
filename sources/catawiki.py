"""Catawiki: categorie reali + filtro nativo 'in chiusura oggi'."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from urllib.parse import quote_plus

from http_fetch import SessionFetcher, is_github_hosted
from listing import SourceListing
from money import parse_euro, remaining_from_any

# Query flip di default (override con CATAWIKI_QUERIES).
DEFAULT_QUERIES = (
    "casio",
    "garmin",
    "fossil",
    "seiko",
    "lego",
    "profumo",
    "sneaker nike",
    "borsa",
    "makita",
    "kenwood",
    "xiaomi",
    "lampada",
    "chicco",
)

# ID/slug verificati su catawiki.com/it (i vecchi /c/293-orologi ecc. davano 404).
DEFAULT_CATEGORY_PATHS = (
    "/it/c/333-orologi-da-polso",
    "/it/c/721-moda",
    "/it/c/363-giocattoli-e-modellini",
    "/it/c/347-musica-film-e-fotocamere",
    "/it/c/714-gioielli-e-pietre-preziose",
    "/it/c/725-carte-collezionabili",
)

LOT_RE = re.compile(
    r'href="(https://www\.catawiki\.com/it/l/(\d+)[^"]*)"[^>]*>\s*([^<]{8,160})',
    re.I,
)


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    urls = _search_urls()
    seen: dict[str, SourceListing] = {}
    waf_hits = 0
    not_found = 0
    print(f"[catawiki] {len(urls)} URL (categorie + ricerche, filtro chiusura oggi).")
    fetcher.warm("https://www.catawiki.com/it/")
    for url in urls:
        try:
            html = fetcher.get_text(url, referer="https://www.catawiki.com/it/")
        except Exception as exc:
            msg = str(exc).lower()
            if "404" in msg or "not found" in msg:
                not_found += 1
                print(f"[catawiki] URL non valida (404), salto: {url}")
                continue
            if "has been closed" in msg or "target closed" in msg:
                print(
                    "[catawiki] Finestra Chrome chiusa: non chiudere la finestra "
                    "aperta da Playwright. Rilancio…"
                )
            print(f"[catawiki] blocco WAF: {exc}")
            waf_hits += 1
            if waf_hits >= 2:
                print("[catawiki] WAF ripetuto: stop altre ricerche.")
                break
            continue
        waf_hits = 0
        items = _parse(html)
        if not items:
            print(f"[catawiki] 0 lotti su …{url.split('catawiki.com')[-1][:50]} (passo oltre)")
            continue
        for item in items:
            seen[item.listing_id] = item
        print(f"[catawiki] +{len(items)} da ricerca (totale unici: {len(seen)}).")
    if not_found and not seen:
        print(
            "[catawiki] Molti 404: URL categorie obsolete. "
            "Aggiorna CATAWIKI_CATEGORY_URLS o lascia i default."
        )
    if not seen:
        if is_github_hosted():
            print(
                "[catawiki] Nessun lotto (Akamai). Su GitHub cloud è normale. "
                "Da casa: PLAYWRIGHT_HEADED=true."
            )
        else:
            print(
                "[catawiki] Nessun lotto: Akamai ha bloccato Playwright. "
                "PLAYWRIGHT_HEADED=true e completa il challenge a mano "
                "(non chiudere la finestra Chrome)."
            )
    return list(seen.values())


def _rome_today_yyyymmdd() -> str:
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:
        now = datetime.now().astimezone()
    return now.strftime("%Y%m%d")


def _closing_today_query() -> str:
    """Filtro nativo Catawiki 'In chiusura oggi' (bidding_end_days[]=YYYYMMDD)."""
    if os.getenv("CATAWIKI_CLOSING_TODAY", "true").lower() not in ("1", "true", "yes"):
        return "sort=ending_soon"
    day = _rome_today_yyyymmdd()
    # Non usare urlencode sul valore intero: l'uguale interno deve restare '='.
    return f"filters=bidding_end_days%5B%5D={day}&sort=ending_soon"


def _search_urls() -> list[str]:
    qs = _closing_today_query()
    queries = [
        item.strip()
        for item in os.getenv("CATAWIKI_QUERIES", "").split(",")
        if item.strip()
    ] or list(DEFAULT_QUERIES)
    search_urls = [
        f"https://www.catawiki.com/it/s?q={quote_plus(q)}&{qs}" for q in queries
    ]
    extra = [
        item.strip()
        for item in os.getenv("CATAWIKI_CATEGORY_URLS", "").split(",")
        if item.strip()
    ]
    if not extra and os.getenv("CATAWIKI_USE_CATEGORIES", "true").lower() in (
        "1",
        "true",
        "yes",
    ):
        extra = [f"https://www.catawiki.com{path}?{qs}" for path in DEFAULT_CATEGORY_PATHS]
    else:
        # Se l'utente passa URL senza filtro, aggiungi chiusura oggi.
        fixed: list[str] = []
        for url in extra:
            if "bidding_end_days" in url or "sort=" in url:
                fixed.append(url)
            else:
                sep = "&" if "?" in url else "?"
                fixed.append(f"{url}{sep}{qs}")
        extra = fixed
    # Categorie prima (catalogo largo), poi keyword.
    return list(dict.fromkeys(extra + search_urls))


def _parse(html: str) -> list[SourceListing]:
    listings: list[SourceListing] = []
    if "__NEXT_DATA__" in html:
        listings.extend(_from_next_data(html))
    if listings:
        return listings
    for match in LOT_RE.finditer(html):
        url, lot_id, title = match.group(1), match.group(2), match.group(3).strip()
        listings.append(
            SourceListing(
                source="catawiki",
                listing_id=lot_id,
                title=title,
                url=url.split("?")[0],
                current_price_eur=0.0,
            )
        )
    return listings


def _from_next_data(html: str) -> list[SourceListing]:
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    blobs = json.dumps(payload)
    listings: list[SourceListing] = []
    for lot in _walk_lots(payload):
        listings.append(lot)
    if listings:
        return listings
    for lot_id, title in re.findall(
        r'"id":\s*(\d{5,}).{0,200}"title":\s*"([^"]{8,120})"', blobs
    ):
        listings.append(
            SourceListing(
                source="catawiki",
                listing_id=str(lot_id),
                title=title,
                url=f"https://www.catawiki.com/it/l/{lot_id}",
                current_price_eur=0.0,
            )
        )
    return listings[:80]


def _walk_lots(node: object) -> list[SourceListing]:
    found: list[SourceListing] = []
    if isinstance(node, dict):
        if "current_bid" in node or "highest_bid" in node or "bidding_end_time" in node:
            lot = _lot_from_dict(node)
            if lot:
                found.append(lot)
        for value in node.values():
            found.extend(_walk_lots(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk_lots(item))
    return found


def _money(value: object) -> float:
    if isinstance(value, dict):
        amount = value.get("amount") or value.get("cents") or value.get("value")
        if isinstance(amount, (int, float)) and amount > 500:
            return float(amount) / 100
        if isinstance(amount, (int, float)):
            return float(amount)
        text = str(value.get("formatted") or "")
        return parse_euro(text) or parse_euro(str(value)) or 0.0
    if isinstance(value, (int, float)):
        return float(value) / 100 if value > 500 else float(value)
    return parse_euro(str(value)) or 0.0


def _lot_from_dict(node: dict) -> SourceListing | None:
    lot_id = str(node.get("id") or node.get("lot_id") or "")
    title = str(node.get("title") or node.get("name") or "").strip()
    if not lot_id or not title:
        return None
    bid = node.get("current_bid") or node.get("highest_bid") or node.get("start_bid") or 0
    estimate = node.get("estimated_price") or node.get("estimate") or {}
    retail = 0.0
    if isinstance(estimate, dict):
        retail = _money(estimate.get("max") or estimate.get("high") or estimate)
    slug = node.get("url") or node.get("slug") or f"/it/l/{lot_id}"
    url = slug if str(slug).startswith("http") else f"https://www.catawiki.com{slug}"
    remaining = remaining_from_any(
        node.get("bidding_end_time")
        or node.get("end_time")
        or node.get("close_at")
        or node.get("expires_at")
    )
    low = 0.0
    high = 0.0
    if isinstance(estimate, dict):
        low = _money(estimate.get("min") or estimate.get("low") or 0)
        high = _money(estimate.get("max") or estimate.get("high") or estimate)
    reserve = node.get("reserve_met")
    if reserve is None:
        reserve = node.get("is_reserve_met")
    if isinstance(reserve, str):
        reserve = reserve.lower() in ("1", "true", "yes")
    extra = {
        "estimate_low": low,
        "estimate_high": high or retail,
        "reserve_met": None if reserve is None else bool(reserve),
    }
    return SourceListing(
        source="catawiki",
        listing_id=lot_id,
        title=title,
        url=url,
        current_price_eur=_money(bid),
        retail_hint_eur=retail or high,
        remaining_seconds=remaining,
        extra=extra,
    )
