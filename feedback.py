"""Storico personale: visto / ignorato / comprato / venduto → filtri adattivi."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

FEEDBACK_FILE = Path(__file__).resolve().parent / ".feedback.json"
ADAPT_AFTER = 20
IGNORE_BRAND_THRESHOLD = 3


@dataclass
class FeedbackStore:
    seen: list[dict]
    ignored: list[dict]
    bought: list[dict]
    sold: list[dict]
    path: Path = FEEDBACK_FILE

    @classmethod
    def load(cls, path: Path = FEEDBACK_FILE) -> FeedbackStore:
        if not path.exists():
            return cls([], [], [], [], path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls([], [], [], [], path)
        return cls(
            seen=list(raw.get("seen") or []),
            ignored=list(raw.get("ignored") or []),
            bought=list(raw.get("bought") or []),
            sold=list(raw.get("sold") or []),
            path=path,
        )

    def save(self) -> None:
        payload = {
            "seen": self.seen[-400:],
            "ignored": self.ignored[-200:],
            "bought": self.bought[-200:],
            "sold": self.sold[-200:],
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def adapted(self) -> bool:
        return len(self.seen) >= ADAPT_AFTER

    def record_seen(
        self,
        *,
        listing_id: str,
        title: str,
        brand: str | None,
        category: str,
        source: str,
    ) -> None:
        if any(item.get("id") == listing_id for item in self.seen[-80:]):
            return
        self.seen.append(
            {
                "id": listing_id,
                "title": title,
                "brand": brand or "",
                "category": category,
                "source": source,
                "ts": time.time(),
            }
        )

    def record(
        self,
        action: str,
        *,
        listing_id: str,
        title: str = "",
        brand: str | None = None,
        category: str = "",
        source: str = "",
    ) -> None:
        event = {
            "id": listing_id,
            "title": title,
            "brand": brand or "",
            "category": category,
            "source": source,
            "ts": time.time(),
        }
        if action == "ignored":
            self.ignored.append(event)
        elif action == "bought":
            self.bought.append(event)
        elif action == "sold":
            self.sold.append(event)
        else:
            raise ValueError(f"Azione sconosciuta: {action}")

    def find(self, listing_id: str) -> dict | None:
        listing_id = listing_id.strip()
        for bucket in (self.seen, self.ignored, self.bought, self.sold):
            for item in reversed(bucket):
                if item.get("id") == listing_id:
                    return item
        return None

    def sold_brands(self) -> set[str]:
        return {item.get("brand", "") for item in self.sold if item.get("brand")}

    def sold_categories(self) -> set[str]:
        return {item.get("category", "") for item in self.sold if item.get("category")}

    def ignored_brand_counts(self) -> Counter[str]:
        return Counter(item.get("brand", "") for item in self.ignored if item.get("brand"))

    def rejected_brands(self) -> set[str]:
        if not self.adapted():
            return set()
        return {
            brand
            for brand, count in self.ignored_brand_counts().items()
            if brand and count >= IGNORE_BRAND_THRESHOLD
        }

    def score_delta(self, brand: str | None, category: str) -> tuple[int, list[str]]:
        delta = 0
        reasons: list[str] = []
        if brand and brand in self.sold_brands():
            delta += 12
            reasons.append("Hai già venduto questa marca")
        if category and category in self.sold_categories():
            delta += 8
            reasons.append("Hai già venduto questa categoria")
        ignored = self.ignored_brand_counts()
        if self.adapted() and brand and ignored.get(brand, 0) >= IGNORE_BRAND_THRESHOLD:
            delta -= 18
            reasons.append("Hai ignorato questa marca 3+ volte")
        return delta, reasons
