"""Filtri flip-friendly: spedibilità, categorie, keyword, Catawiki, soggettivo."""

from __future__ import annotations

import re

from listing import SourceListing
from money import infer_category
from site_profiles import SiteProfile

FLIP_CATEGORY_TAGS: frozenset[str] = frozenset(
    {
        "elettronica",
        "utensili",
        "moda",
        "bellezza",
        "casa",
        "orologi",
        "prima-infanzia",
        "videogiochi",
        "libri",
        "smartwatch",
        "dashcam",
        "lampade",
        "sneaker",
        "borse",
        "profumi",
        "elettrodomestici",
    }
)

UNSHIPPABLE_PATTERNS: tuple[str, ...] = (
    r"\bdivano\b",
    r"\barmadio\b",
    r"\bmobile\b",
    r"\bpanca\b",
    r"\bscrivania\b",
    r"\bfrigo\b",
    r"\bfrigorifero\b",
    r"\blavatrice\b",
    r"\bletto\b",
    r"\bmaterasso\b",
    r"\bcucina componibil",
    r"\blavastoviglie\b",
    r"\bforno\s+(da\s+)?incasso\b",
    r"\bcongelatore\b",
    r"\bclimatizzatore\b",
    r"\bcondizionatore\b",
    r"\bcaldaia\b",
    r"\bscaldabagno\b",
    r"\btrattore\b",
    r"\bautocarro\b",
    r"\bescavator",
    r"\bcnc\b",
    r"\btornio\b",
    r"\bcarrello\s+elevatore\b",
    r"\bmuletto\b",
    r"\bmacchinario\b",
    r"\battrezzatura\s+industriale\b",
    r"\bpressa\s+idraulic",
    r"\bgeneratore\s+diesel\b",
    r"\bcompressore\s+industriale\b",
    r"\bscaffalatura\b",
    r"\binfissi\b",
    r"\bcancello\s+automatic",
    r"\bkg\s*(?:0?[8-9]|[1-9]\d|\d{3,})",
    r"\bpeso\s*(oltre\s*)?(?:[8-9]|[1-9]\d)\s*kg\b",
)

PICKUP_PATTERNS: tuple[str, ...] = (
    r"\britiro\b",
    r"\bsolo\s+ritiro\b",
    r"\britiro\s+in\s+sede\b",
    r"\bda\s+ritirare\b",
    r"\bno\s+spediz",
)

BULK_PATTERNS: tuple[str, ...] = (
    r"\bbancale\b",
    r"\bpallet\b",
    r"\blotto\s+misto\b",
    r"\bsvuota\s*magazzino\b",
)

HEAVY_CONDITION_PATTERNS: tuple[str, ...] = (
    r"\brotto\b",
    r"\bdifettos",
    r"\bnon\s+testat",
    r"\bda\s+testare\b",
    r"\bda\s+verificare\b",
    r"\bnon\s+funzionant",
    r"\bper\s+ricambi\b",
    r"\bas\s+is\b",
    r"\bpesante\b",
    r"\bmancante\b",
    r"\bparti\s+mancanti\b",
    r"\bsolo\s+ritiro\b",
)

CONDITION_HARD_REJECT: tuple[str, ...] = (
    r"\bnon\s+testat",
    r"\bda\s+verificare\b",
    r"\bsolo\s+ritiro\b",
    r"\bmancante\b",
    r"\bdifettos",
)

CATAWIKI_BLOCK_PATTERNS: tuple[str, ...] = (
    r"\barte\b",
    r"\bdipinto\b",
    r"\bolio\s+su\s+tela\b",
    r"\bgioiell",
    r"\bdiamant",
    r"\brolex\b",
    r"\bpatek\b",
    r"\baudemars\b",
    r"\bomega\s+(seamaster|speedmaster|constellation)\b",
    r"\bcartier\b",
    r"\btiffany\b",
    r"\bvacheron\b",
)

VAGUE_PATTERNS: tuple[str, ...] = (
    r"^\s*(lotto|stock|vario|varie|oggetti|articoli|oggetto|articolo)\s*$",
    r"^\s*(lotto|stock|varie|oggetti)\s+\d+\s*$",
    r"^\s*\d+\s*$",
    r"\bcome\s+da\s+foto\b",
    r"\bvedi\s+foto\b",
    r"\bsorpresa\b",
    r"\bmystery\b",
)

EBAY_NEGATIVES: tuple[str, ...] = (
    r"\bnon\s+testat",
    r"\bdifettos",
    r"\bsolo\s+ritiro\b",
    r"\bmancante\b",
    r"\bparti\s+mancanti\b",
    r"\blotto\s+misto\b",
)

VINTED_NEGATIVES: tuple[str, ...] = (
    r"\busato\s+molto\b",
    r"\bmacchie\b",
    r"\btaglia\s+non\s+indicat",
    r"\bsenza\s+etichetta\b",
    r"\bprofumo\s+aperto\b",
    r"\bflacone\s+aperto\b",
)


def _text(listing: SourceListing) -> str:
    return f"{listing.title} {listing.listing_id}".lower()


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def is_unshippable(listing: SourceListing, profile: SiteProfile) -> bool:
    if profile.listing_kind == "pallet":
        return False
    extra = listing.extra or {}
    if extra.get("ships") is False:
        return True
    text = _text(listing)
    if _matches(text, UNSHIPPABLE_PATTERNS):
        return True
    if profile.listing_kind == "classified" and _matches(text, PICKUP_PATTERNS):
        return True
    if profile.listing_kind not in {"judicial", "pallet"} and _matches(text, PICKUP_PATTERNS):
        return True
    if profile.listing_kind != "classified" and _matches(text, BULK_PATTERNS):
        return True
    return False


def requires_pickup(listing: SourceListing, profile: SiteProfile) -> bool:
    extra = listing.extra or {}
    if extra.get("ships") is True:
        return False
    if extra.get("ships") is False:
        return True
    if profile.listing_kind in {"judicial", "pallet"}:
        return True
    return _matches(_text(listing), PICKUP_PATTERNS)


def is_flip_friendly(listing: SourceListing, profile: SiteProfile) -> bool:
    if profile.listing_kind == "pallet":
        return True
    if is_unshippable(listing, profile):
        return False
    tag = infer_category(listing.title).tag
    return tag in FLIP_CATEGORY_TAGS or infer_flip_tag(listing.title) in FLIP_CATEGORY_TAGS


def infer_flip_tag(title: str) -> str:
    text = title.lower()
    mapping = (
        ("smartwatch", ("smartwatch", "smart watch", "garmin", "amazfit")),
        ("dashcam", ("dash cam", "dashcam", "action cam")),
        ("videogiochi", ("videogioc", "nintendo", "switch", "playstation", "xbox")),
        ("libri", ("libro", "libri", "manga", "fumetto")),
        ("lampade", ("lampada", "lampade", "applique", "abat")),
        ("sneaker", ("sneaker", "sneakers")),
        ("borse", ("borsa", "borse", "marsupio")),
        ("profumi", ("profum", "eau de")),
        (
            "elettrodomestici",
            (
                "piccolo elettro",
                "frullatore",
                "mixer",
                "airfryer",
                "air fryer",
                "tostapane",
                "bollitore",
                "robot da cucina",
                "macchina del caffe",
                "macchina del caff",
            ),
        ),
    )
    for tag, keys in mapping:
        if any(key in text for key in keys):
            return tag
    return infer_category(title).tag


def has_condition_risk(listing: SourceListing) -> bool:
    return _matches(_text(listing), HEAVY_CONDITION_PATTERNS)


def has_channel_negatives(listing: SourceListing, channel: str) -> bool:
    text = _text(listing)
    if channel == "ebay":
        return _matches(text, EBAY_NEGATIVES)
    if channel == "vinted":
        return _matches(text, VINTED_NEGATIVES)
    return False


_STOPWORDS = frozenset(
    {
        "il",
        "lo",
        "la",
        "i",
        "gli",
        "le",
        "un",
        "una",
        "di",
        "da",
        "del",
        "della",
        "dei",
        "delle",
        "per",
        "con",
        "tra",
        "fra",
        "sul",
        "sulla",
        "nel",
        "nella",
        "lotto",
        "stock",
        "pezzi",
        "pezzo",
        "euro",
        "nuovo",
        "usato",
        "originale",
        "offerta",
        "asta",
        "vendita",
        "articolo",
        "articoli",
        "oggetto",
        "oggetti",
        "vario",
        "varie",
        "come",
        "foto",
        "vedi",
        "the",
        "and",
        "for",
    }
)


def useful_word_count(title: str) -> int:
    words = [word.lower() for word in re.split(r"\W+", title) if len(word) >= 3]
    return sum(1 for word in words if word not in _STOPWORDS and not word.isdigit())


def has_hard_condition(listing: SourceListing) -> bool:
    return _matches(_text(listing), CONDITION_HARD_REJECT)


def is_vague_title(listing: SourceListing) -> bool:
    title = listing.title.strip()
    if len(title) < 12:
        return True
    if re.fullmatch(r"[\d\s.,€e]+", title, re.I):
        return True
    if useful_word_count(title) < 3:
        return True
    if _matches(title.lower(), VAGUE_PATTERNS):
        return True
    from brands import find_brand

    brand = find_brand(title)
    if brand:
        leftover = re.sub(re.escape(brand), " ", title, flags=re.I)
        leftover = re.sub(r"\W+", " ", leftover).strip()
        useful = useful_word_count(leftover)
        if useful == 0:
            return True
    return False


def catawiki_reject_reason(listing: SourceListing) -> str | None:
    extra = listing.extra or {}
    if extra.get("reserve_met") is False:
        return "Riserva non raggiunta."
    if _matches(_text(listing), CATAWIKI_BLOCK_PATTERNS):
        return "Catawiki: arte / gioielli / orologi premium."
    tag = infer_flip_tag(listing.title)
    if tag not in FLIP_CATEGORY_TAGS:
        return f"Catawiki: categoria '{tag}' fuori allowlist flip."
    low = float(extra.get("estimate_low") or 0)
    high = float(extra.get("estimate_high") or listing.retail_hint_eur or 0)
    if low > 150:
        return f"Stima minima troppo alta ({low:.0f} €) per flip."
    if low and high and high / max(low, 1) > 2.5:
        return f"Stima troppo larga ({low:.0f}–{high:.0f} €)."
    estimate = high or low
    if estimate > 200:
        return f"Stima esperta troppo alta ({estimate:.0f} €) per flip."
    if low and high:
        estimate = (low + high) / 2
    if estimate > 0 and listing.current_price_eur > 0:
        if listing.current_price_eur > 0.60 * estimate:
            return (
                f"Bid {listing.current_price_eur:.0f} € sopra il 60% della stima "
                f"({estimate:.0f} €)."
            )
    return None


def shipping_for_category(tag: str, profile: SiteProfile, channel: str) -> float:
    table = {
        "moda": 5.0,
        "sneaker": 6.0,
        "borse": 6.0,
        "bellezza": 5.0,
        "profumi": 5.0,
        "libri": 4.5,
        "videogiochi": 5.5,
        "elettronica": 7.0,
        "smartwatch": 6.0,
        "orologi": 6.0,
        "utensili": 9.0,
        "casa": 8.0,
        "lampade": 8.0,
        "prima-infanzia": 8.0,
        "dashcam": 7.0,
        "elettrodomestici": 8.0,
    }
    base = table.get(tag)
    if base is None:
        if channel == "ebay":
            return profile.default_outbound_ebay
        if channel == "vinted":
            return profile.default_outbound_vinted
        return profile.default_outbound_subito
    return base
