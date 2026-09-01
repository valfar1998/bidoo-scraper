"""Astegiudiziarie.it — mobili, filtro data vendita (oggi) prima dei dettagli XML."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from xml.etree import ElementTree as ET

from http_fetch import BROWSER_HEADERS, SessionFetcher
from listing import SourceListing
from money import remaining_from_any

API = "https://webapi.astegiudiziarie.it"
SITE = "https://www.astegiudiziarie.it"

# Tipologie Mobili (GET /api/Tipologie/Mobili). Default: flip box, no veicoli.
DEFAULT_TIPOLOGIE = (9, 11, 12)  # elettronica, orologi/arte, abbigliamento
ITALY_BBOX = {
    "latitudineNW": 47.1,
    "longitudineNW": 6.5,
    "latitudineSE": 36.5,
    "longitudineSE": 18.6,
}


def fetch_listings(fetcher: SessionFetcher) -> list[SourceListing]:
    tipologie = _tipologie()
    max_price = float(os.getenv("ASTEGIUDIZIARIE_MAX_EUR", "400"))
    max_lots = int(os.getenv("ASTEGIUDIZIARIE_MAX_LOTS", "40"))
    start, end = _sale_window()
    fetcher.warm(f"{SITE}/mobili")

    pins = _search_map(fetcher, tipologie, max_price, start, end)
    if not pins:
        print(
            f"[astegiudiziarie] 0 lotti con data vendita "
            f"{start.isoformat()} .. {end.isoformat()} "
            f"(API Search/Map). Niente XML da scaricare."
        )
        return []

    print(
        f"[astegiudiziarie] Map: {len(pins)} pin in "
        f"{start.strftime('%d/%m')}–{end.strftime('%d/%m')} "
        f"(max dettagli {max_lots})."
    )
    pins.sort(key=lambda item: float(item.get("prezzoBase") or 0))
    candidates: list[tuple[str, float, int]] = []
    for pin in pins:
        lot_id = str(pin.get("idLotto") or "")
        if not lot_id:
            continue
        price = float(pin.get("prezzoBase") or 0)
        if price <= 0 or price > max_price:
            continue
        tip = int(pin.get("idTipologia") or 0)
        if tipologie and tip and tip not in tipologie:
            continue
        candidates.append((lot_id, price, tip))
        if len(candidates) >= max_lots:
            break

    # Solo questi XML (non tutto il catalogo). Parallelo = più veloce.
    workers = max(1, min(8, int(os.getenv("ASTEGIUDIZIARIE_XML_WORKERS", "6"))))
    seen: dict[str, SourceListing] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_lot_from_xml, fetcher, lot_id, price, tip): lot_id
            for lot_id, price, tip in candidates
        }
        for fut in as_completed(futures):
            lot_id = futures[fut]
            try:
                detail = fut.result()
            except Exception:
                detail = None
            if detail is None:
                # Senza data non stimare: evita lavoro inutile su titoli generici.
                continue
            if not _within_offer_window(detail, start, end):
                continue
            seen[lot_id] = detail

    print(f"[astegiudiziarie] Lotti con scadenza utile: {len(seen)}/{len(candidates)} XML.")
    return list(seen.values())


def _tipologie() -> list[int]:
    raw = os.getenv("ASTEGIUDIZIARIE_TIPOLOGIE", "")
    if raw.strip():
        return [int(part) for part in raw.split(",") if part.strip().isdigit()]
    return list(DEFAULT_TIPOLOGIE)


def _sale_window() -> tuple[date, date]:
    """Finestra data vendita (Europe/Rome). Default: solo oggi."""
    try:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Europe/Rome")).date()
    except Exception:
        today = datetime.now().astimezone().date()
    # 0 = solo oggi; 1 = oggi+domani (come Astagiudiziaria).
    days = max(0, int(os.getenv("ASTEGIUDIZIARIE_DAYS_AHEAD", "0")))
    return today, today + timedelta(days=days)


def _search_map(
    fetcher: SessionFetcher,
    tipologie: list[int],
    max_price: float,
    start: date,
    end: date,
) -> list[dict]:
    payload = {
        **ITALY_BBOX,
        "idTipologie": tipologie,
        "idCategorie": [],
        "tipoRicerca": 1,
        "storica": False,
        "bandita": False,
        "prezzoDa": 1,
        "prezzoA": max_price,
        # Filtro server-side: evita di scaricare XML su tutto il catalogo.
        "dataVenditaDa": start.isoformat(),
        "dataVenditaA": end.isoformat(),
        "inScadenza": True,
    }
    headers = {
        **BROWSER_HEADERS,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": SITE,
        "Referer": f"{SITE}/mobili",
    }
    try:
        response = fetcher._session.post(
            f"{API}/api/Search/Map",
            headers=headers,
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        print(f"[astegiudiziarie] Search/Map: {exc}")
        return []
    return data if isinstance(data, list) else []


def _within_offer_window(listing: SourceListing, start: date, end: date) -> bool:
    """Tieni se termine offerte o vendita cade nella finestra (dopo XML)."""
    raw = (listing.remaining_text or "").strip()
    if not raw:
        # Map ha già filtrato per dataVendita: OK tenere.
        return True
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/Rome")
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        day = dt.astimezone(tz).date()
        return start <= day <= end
    except ValueError:
        rem = listing.remaining_seconds
        if rem is None:
            return True
        # Entro fine finestra (giorni ahead + resto giornata).
        max_sec = (end - start).days * 86400 + 86400
        return 0 <= rem <= max_sec


def _lot_from_xml(
    fetcher: SessionFetcher,
    lot_id: str,
    price: float,
    tip: int,
) -> SourceListing | None:
    import requests

    headers = {
        **BROWSER_HEADERS,
        "Accept": "application/xml",
        "Origin": SITE,
        "Referer": f"{SITE}/mobili",
    }
    try:
        # requests.get dedicato (Session non è thread-safe nei worker paralleli).
        response = requests.get(
            f"{API}/api/tracciatoXmlMinistero/{lot_id}",
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        xml = response.text or ""
    except Exception:
        return None
    title = _xml_text(xml, ("descLotto", "descBene")) or f"Lotto mobili {lot_id}"
    end_raw = _xml_text(
        xml, ("dataOraTermPresOff", "dataTermineDeposito", "dataOraUdienza")
    )
    remaining = remaining_from_any(end_raw)
    if remaining is not None and remaining > 14 * 86400:
        remaining = None
    tip_tag = {
        9: "elettronica",
        11: "orologi",
        12: "moda",
        6: "auto-moto",
        10: "casa",
    }.get(tip, "")
    return SourceListing(
        source="astegiudiziarie",
        listing_id=lot_id,
        title=title[:180],
        url=f"{SITE}/#!/lotto/{lot_id}",
        current_price_eur=price,
        remaining_seconds=remaining,
        remaining_text=end_raw or "",
        category_tag=tip_tag,
        location=_xml_text(xml, ("textVia",)) or "",
        extra={"tipologia_id": tip},
    )


def _xml_text(xml: str, tags: tuple[str, ...]) -> str:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    wanted = {tag.lower() for tag in tags}
    for element in root.iter():
        name = element.tag.split("}")[-1].lower()
        if name in wanted and element.text and element.text.strip():
            return re.sub(r"\s+", " ", element.text.strip())
    return ""
