"""Parsing prezzi europei e filtri titolo condivisi."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from resale_categories import ResaleCategory

_EURO_RE = re.compile(
    r"(?:EUR|€)\s*([\d.]*\d)(?:[.,](\d{2}))?",
    re.IGNORECASE,
)
_PLAIN_RE = re.compile(r"([\d.]+),(\d{2})")

HEAVY_PATTERNS = (
    r"\btrattore\b",
    r"\bsemirimorchio\b",
    r"\brimorchio\b",
    r"\bautovettura\b",
    r"\bescavator",
    r"\bimmobil",
    r"\bappartament",
    r"\bquota\s+societar",
    r"\bcessione\s+di\s+azienda\b",
    r"\bmuletto\s+elettrico\s+\d",
    r"\bcarrello\s+elevatore\b",
    r"\bcnc\b",
    r"\btornio\b",
    r"\bfresa\s+a\s+controllo\b",
)

LOT_HINT = re.compile(r"\b(lotto|stock|bancale|pallet|rimanenz|svuota\s*magazzino)\b", re.I)

_KEYWORD_RULES: tuple[tuple[tuple[str, ...], ResaleCategory], ...] = (
    (
        ("trapano", "avvitatore", "smerigliatrice", "makita", "dewalt", "utensil"),
        ResaleCategory(
            tag="utensili",
            name="Utensili elettrici",
            competition="medium",
            platform="ebay",
            resale_ratio=0.50,
            bid_factor=1.0,
            min_retail=25,
            max_retail=400,
            notes="Bosch/Makita usati: meglio eBay.",
        ),
    ),
    (
        ("ricambio", "moto", "auto", "dash cam", "scooter"),
        ResaleCategory(
            tag="auto-moto",
            name="Auto/moto e ricambi",
            competition="medium",
            platform="ebay",
            resale_ratio=0.45,
            bid_factor=1.0,
            min_retail=15,
            max_retail=500,
            notes="Ricambi: eBay.",
        ),
    ),
    (
        ("sneaker", "scarpe", "nike", "adidas", "abbigliamento", "borsa", "zaino", "marsupio"),
        ResaleCategory(
            tag="moda",
            name="Moda e accessori",
            competition="medium",
            platform="vinted",
            resale_ratio=0.48,
            bid_factor=1.0,
            min_retail=15,
            max_retail=400,
            notes="Vinted per NWT e sneakers.",
        ),
    ),
    (
        ("profum", "cosmetic", "bellezza", "epilator"),
        ResaleCategory(
            tag="bellezza",
            name="Bellezza e cura persona",
            competition="medium",
            platform="vinted",
            resale_ratio=0.50,
            bid_factor=1.0,
            min_retail=20,
            max_retail=250,
            notes="Sigillati: Vinted/eBay.",
        ),
    ),
    (
        ("lego", "chicco", "giocattol", "infanzia", "passeggin"),
        ResaleCategory(
            tag="prima-infanzia",
            name="Prima infanzia e giocattoli",
            competition="low",
            platform="vinted",
            resale_ratio=0.55,
            bid_factor=1.0,
            min_retail=15,
            max_retail=250,
            notes="Vinted.",
        ),
    ),
    (
        ("orolog", "casio", "fossil", "seiko"),
        ResaleCategory(
            tag="orologi",
            name="Orologi",
            competition="medium",
            platform="ebay",
            resale_ratio=0.45,
            bid_factor=1.0,
            min_retail=25,
            max_retail=400,
            notes="eBay.",
        ),
    ),
    (
        ("videogioc", "playstation", "xbox", "nintendo"),
        ResaleCategory(
            tag="videogiochi",
            name="Videogiochi",
            competition="medium",
            platform="ebay",
            resale_ratio=0.50,
            bid_factor=1.0,
            min_retail=15,
            max_retail=250,
            notes="eBay; Vinted se sigillato.",
        ),
    ),
    (
        ("libro", "libri", "manga", "fumetto"),
        ResaleCategory(
            tag="libri",
            name="Libri e fumetti",
            competition="low",
            platform="vinted",
            resale_ratio=0.42,
            bid_factor=1.0,
            min_retail=10,
            max_retail=80,
            notes="Vinted; evita lotti misti.",
        ),
    ),
    (
        ("cuffie", "smartwatch", "elettrodomestic", "kenwood", "dash cam", "dashcam"),
        ResaleCategory(
            tag="elettronica",
            name="Elettronica e gadget",
            competition="medium",
            platform="ebay",
            resale_ratio=0.48,
            bid_factor=1.0,
            min_retail=20,
            max_retail=400,
            notes="eBay; Vinted se piccolo.",
        ),
    ),
    (
        ("casa", "lampad", "organizer", "arredo", "frigo", "scaldavivande"),
        ResaleCategory(
            tag="casa",
            name="Casa",
            competition="low",
            platform="vinted",
            resale_ratio=0.47,
            bid_factor=1.0,
            min_retail=15,
            max_retail=250,
            notes="Vinted/Subito.",
        ),
    ),
)

FALLBACK_CATEGORY = ResaleCategory(
    tag="generico",
    name="Generico rivendibile",
    competition="medium",
    platform="both",
    resale_ratio=0.45,
    bid_factor=1.0,
    min_retail=10,
    max_retail=800,
    notes="Categoria inferita dal titolo.",
)


def parse_italian_amount(text: str) -> float:
    """3.696,53 → 3696.53; 4.658 → 4658; 676.46 → 676.46."""
    if not text:
        return 0.0
    raw = text.strip().replace("€", "").replace("\xa0", "").replace(" ", "")
    try:
        if "," in raw:
            return float(raw.replace(".", "").replace(",", "."))
        parts = raw.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit():
            return float(raw.replace(".", ""))
        return float(raw)
    except ValueError:
        return 0.0


def listing_passes_profile(title: str, extra_exclude: tuple[str, ...], extra_include: tuple[str, ...]) -> bool:
    text = title.lower()
    if any(token in text for token in extra_exclude):
        return False
    if extra_include and not any(token in text for token in extra_include):
        return False
    return True


def parse_euro(text: str) -> float | None:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").replace(" ", "")
    match = _EURO_RE.search(text) or _EURO_RE.search(cleaned)
    if match:
        whole = match.group(1).replace(".", "").replace(",", "")
        cents = match.group(2) or "00"
        try:
            return float(f"{int(whole)}.{cents}")
        except ValueError:
            return None
    match = _PLAIN_RE.search(text)
    if not match:
        return None
    try:
        return float(f"{match.group(1).replace('.', '')}.{match.group(2)}")
    except ValueError:
        return None


def is_heavy_item(title: str) -> bool:
    text = title.lower()
    return any(re.search(pattern, text, re.I) for pattern in HEAVY_PATTERNS)


def looks_like_bulk_lot(title: str) -> bool:
    return bool(LOT_HINT.search(title))


def infer_category(title: str) -> ResaleCategory:
    text = title.lower()
    for keywords, category in _KEYWORD_RULES:
        if any(keyword in text for keyword in keywords):
            return category
    return FALLBACK_CATEGORY


def remaining_to_seconds(text: str) -> int:
    parsed = remaining_from_any(text)
    return parsed if parsed is not None else 0


def remaining_from_any(value: object, *, now: float | None = None) -> int | None:
    """Secondi alla chiusura, o None se non si capisce."""
    ts = now if now is not None else time.time()
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return remaining_from_any(value[0] if value else None, now=ts)
    if isinstance(value, (int, float)):
        if value > 1_000_000_000:
            return max(0, int(value - ts))
        if value > 10_000_000:
            return max(0, int(value - ts))
        return max(0, int(value))
    text = str(value).strip()
    if not text or re.search(r"terminat", text, re.I):
        return 0 if re.search(r"terminat", text, re.I) else None
    iso = _parse_iso_remaining(text, ts)
    if iso is not None:
        return iso
    italian = _parse_italian_datetime_remaining(text, ts)
    if italian is not None:
        return italian
    lowered = text.lower()
    total = 0
    days = re.search(r"(\d+)\s*g", lowered)
    hours = re.search(r"(\d+)\s*h", lowered)
    minutes = re.search(r"(\d+)\s*m(?!\s*CET)", lowered)
    if days:
        total += int(days.group(1)) * 86400
    if hours:
        total += int(hours.group(1)) * 3600
    if minutes:
        total += int(minutes.group(1)) * 60
    if total > 0:
        return total
    return None


def _parse_iso_remaining(text: str, now: float) -> int | None:
    cleaned = text.replace("Z", "+00:00")
    if "T" not in cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int(parsed.timestamp() - now))
    except ValueError:
        return None


def _parse_italian_datetime_remaining(text: str, now: float) -> int | None:
    match = re.search(
        r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?",
        text,
    )
    if not match:
        return None
    day, month, year, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    try:
        from zoneinfo import ZoneInfo

        parsed = datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo("Europe/Rome"))
    except Exception:
        parsed = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return max(0, int(parsed.timestamp() - now))
