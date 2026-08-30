"""Parsing prezzi europei e filtri titolo condivisi."""

from __future__ import annotations

import re

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
        ("videogioc", "nintendo", "cuffie", "smartwatch", "elettrodomestic", "kenwood"),
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
    if not text:
        return 0
    lowered = text.lower().replace("asta terminata", "")
    total = 0
    days = re.search(r"(\d+)\s*g", lowered)
    hours = re.search(r"(\d+)\s*h", lowered)
    minutes = re.search(r"(\d+)\s*m", lowered)
    if days:
        total += int(days.group(1)) * 86400
    if hours:
        total += int(hours.group(1)) * 3600
    if minutes:
        total += int(minutes.group(1)) * 60
    if re.search(r"terminat", text, re.I):
        return 0
    return total
