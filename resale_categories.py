"""Categorie Bidoo orientate alla rivendita su Vinted/eBay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin

DEFAULT_BASE_URL = "https://it.bidoo.com/"

Competition = Literal["low", "medium", "high"]
Platform = Literal["vinted", "ebay", "both"]


@dataclass(frozen=True)
class ResaleCategory:
    tag: str
    name: str
    competition: Competition
    platform: Platform
    resale_ratio: float
    bid_factor: float
    min_retail: float
    max_retail: float
    notes: str

    def url(self, base_url: str = DEFAULT_BASE_URL) -> str:
        return urljoin(base_url, f"?tag={self.tag}")


# Categorie selezionate: meno battute su Bidoo, buona domanda su Vinted/eBay.
# Evitate: smartphone, console, Apple, tablet (troppa competizione).
DEFAULT_RESALE_CATEGORIES: tuple[ResaleCategory, ...] = (
    ResaleCategory(
        tag="prima-infanzia",
        name="Prima infanzia e giocattoli",
        competition="low",
        platform="vinted",
        resale_ratio=0.55,
        bid_factor=1.1,
        min_retail=25,
        max_retail=150,
        notes="LEGO, Chicco, peg Perego: alta rotazione su Vinted.",
    ),
    ResaleCategory(
        tag="animali_domestici",
        name="Animali domestici",
        competition="low",
        platform="both",
        resale_ratio=0.50,
        bid_factor=1.0,
        min_retail=20,
        max_retail=120,
        notes="Accessori pet di marca: nicchia poco seguita.",
    ),
    ResaleCategory(
        tag="elettrodomestici",
        name="Piccoli elettrodomestici",
        competition="medium",
        platform="both",
        resale_ratio=0.48,
        bid_factor=1.6,
        min_retail=50,
        max_retail=350,
        notes="Kenwood, Smeg, Rowenta: filtra smartphone/console.",
    ),
    ResaleCategory(
        tag="bellezza",
        name="Bellezza e cura persona",
        competition="medium",
        platform="vinted",
        resale_ratio=0.52,
        bid_factor=1.4,
        min_retail=35,
        max_retail=200,
        notes="Piastre, epilatori mid-range; evita Dyson top di gamma.",
    ),
    ResaleCategory(
        tag="orologi",
        name="Orologi e accessori",
        competition="medium",
        platform="ebay",
        resale_ratio=0.45,
        bid_factor=1.5,
        min_retail=40,
        max_retail=250,
        notes="Fossil, Casio, Seiko entry: meglio eBay che Vinted.",
    ),
    ResaleCategory(
        tag="sport",
        name="Sport e fitness",
        competition="low",
        platform="both",
        resale_ratio=0.50,
        bid_factor=1.2,
        min_retail=30,
        max_retail=180,
        notes="Accessori fitness, non console o wearable premium.",
    ),
    ResaleCategory(
        tag="casa",
        name="Casa e arredo piccolo",
        competition="low",
        platform="vinted",
        resale_ratio=0.47,
        bid_factor=1.1,
        min_retail=35,
        max_retail=200,
        notes="Piccoli elettrodomestici casa, aspirapolvere compatti.",
    ),
)

CATEGORIES_BY_TAG: dict[str, ResaleCategory] = {
    category.tag: category for category in DEFAULT_RESALE_CATEGORIES
}

# Default stretto: solo categorie a bassa competizione (meno notifiche inutili).
STRICT_DEFAULT_TAGS: tuple[str, ...] = (
    "prima-infanzia",
    "animali_domestici",
    "sport",
    "casa",
)


def parse_category_tags(raw: str, *, strict_default: bool = True) -> list[str]:
    if not raw.strip():
        if strict_default:
            return list(STRICT_DEFAULT_TAGS)
        return [category.tag for category in DEFAULT_RESALE_CATEGORIES]
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def resolve_categories(
    raw_tags: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> list[ResaleCategory]:
    tags = parse_category_tags(raw_tags)
    selected: list[ResaleCategory] = []
    for tag in tags:
        category = CATEGORIES_BY_TAG.get(tag)
        if category:
            selected.append(category)
            continue
        selected.append(
            ResaleCategory(
                tag=tag,
                name=tag.replace("_", " ").replace("-", " ").title(),
                competition="medium",
                platform="both",
                resale_ratio=0.45,
                bid_factor=1.5,
                min_retail=30,
                max_retail=200,
                notes="Categoria personalizzata.",
            )
        )
    return selected
