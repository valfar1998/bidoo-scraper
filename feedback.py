"""Storico personale: visto / ignorato / comprato / venduto → filtri adattivi."""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from database import connect, db_enabled, ensure_db

FEEDBACK_FILE = Path(__file__).resolve().parent / ".feedback.json"
ADAPT_AFTER = 20
IGNORE_BRAND_THRESHOLD = 3
BLACKLIST_BRAND_THRESHOLD = 5
PREMIUM_SOLD_THRESHOLD = 3
_BUCKETS = ("seen", "ignored", "bought", "sold", "estimate_error")


@dataclass
class FeedbackStore:
    seen: list[dict]
    ignored: list[dict]
    bought: list[dict]
    sold: list[dict]
    estimate_error: list[dict]
    path: Path = FEEDBACK_FILE

    @classmethod
    def load(cls, path: Path = FEEDBACK_FILE) -> FeedbackStore:
        ensure_db()
        if db_enabled():
            return cls._load_db(path)
        if not path.exists():
            return cls([], [], [], [], [], path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls([], [], [], [], [], path)
        return cls(
            seen=list(raw.get("seen") or []),
            ignored=list(raw.get("ignored") or []),
            bought=list(raw.get("bought") or []),
            sold=list(raw.get("sold") or []),
            estimate_error=list(raw.get("estimate_error") or []),
            path=path,
        )

    @classmethod
    def _load_db(cls, path: Path) -> FeedbackStore:
        buckets: dict[str, list[dict]] = {name: [] for name in _BUCKETS}
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT bucket, listing_id, title, brand, category, source, ts
                FROM feedback_events
                ORDER BY ts ASC
                """
            ).fetchall()
        for row in rows:
            bucket = str(row["bucket"])
            if bucket not in buckets:
                continue
            buckets[bucket].append(
                {
                    "id": str(row["listing_id"]),
                    "title": str(row["title"]),
                    "brand": str(row["brand"]),
                    "category": str(row["category"]),
                    "source": str(row["source"]),
                    "ts": float(row["ts"]),
                }
            )
        return cls(
            seen=buckets["seen"][-400:],
            ignored=buckets["ignored"][-200:],
            bought=buckets["bought"][-200:],
            sold=buckets["sold"][-200:],
            estimate_error=buckets["estimate_error"][-200:],
            path=path,
        )

    def save(self) -> None:
        payload = {
            "seen": self.seen[-400:],
            "ignored": self.ignored[-200:],
            "bought": self.bought[-200:],
            "sold": self.sold[-200:],
            "estimate_error": self.estimate_error[-200:],
        }
        if db_enabled():
            with connect() as conn:
                conn.execute("DELETE FROM feedback_events")
                for bucket, items in payload.items():
                    for item in items:
                        conn.execute(
                            """
                            INSERT INTO feedback_events(
                                bucket, listing_id, title, brand, category, source, ts
                            ) VALUES(?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                bucket,
                                str(item.get("id", "")),
                                str(item.get("title", "")),
                                str(item.get("brand", "")),
                                str(item.get("category", "")),
                                str(item.get("source", "")),
                                float(item.get("ts", time.time())),
                            ),
                        )
            return
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
        elif action == "estimate_error":
            self.estimate_error.append(event)
        else:
            raise ValueError(f"Azione sconosciuta: {action}")

    def find(self, listing_id: str) -> dict | None:
        listing_id = listing_id.strip()
        for bucket in (self.seen, self.ignored, self.bought, self.sold, self.estimate_error):
            for item in reversed(bucket):
                if item.get("id") == listing_id:
                    return item
        return None

    def sold_brand_counts(self) -> Counter[str]:
        return Counter(item.get("brand", "") for item in self.sold if item.get("brand"))

    def sold_brands(self) -> set[str]:
        return set(self.sold_brand_counts())

    def blacklisted_brands(self) -> set[str]:
        return {
            brand
            for brand, count in self.ignored_brand_counts().items()
            if brand and count >= BLACKLIST_BRAND_THRESHOLD
        }

    def premium_brands(self) -> set[str]:
        return {
            brand
            for brand, count in self.sold_brand_counts().items()
            if brand and count >= PREMIUM_SOLD_THRESHOLD
        }

    def bought_brand_counts(self) -> Counter[str]:
        return Counter(item.get("brand", "") for item in self.bought if item.get("brand"))

    def sold_categories(self) -> set[str]:
        return {item.get("category", "") for item in self.sold if item.get("category")}

    def ignored_brand_counts(self) -> Counter[str]:
        return Counter(item.get("brand", "") for item in self.ignored if item.get("brand"))

    def rejected_brands(self) -> set[str]:
        return {
            brand
            for brand, count in self.ignored_brand_counts().items()
            if brand and count >= IGNORE_BRAND_THRESHOLD
        }

    def estimate_error_brand_counts(self) -> Counter[str]:
        return Counter(
            item.get("brand", "") for item in self.estimate_error if item.get("brand")
        )

    def score_delta(self, brand: str | None, category: str) -> tuple[int, list[str]]:
        delta = 0
        reasons: list[str] = []
        err_n = self.estimate_error_brand_counts().get(brand or "", 0)
        if brand and err_n >= 2:
            delta -= 15
            reasons.append("Stima errata segnalata 2+ volte su questa marca")
        sold_n = self.sold_brand_counts().get(brand or "", 0)
        if brand and sold_n >= PREMIUM_SOLD_THRESHOLD:
            delta += 30
            reasons.append("Marca premium (venduta 3+ volte)")
        elif brand and sold_n >= 1:
            delta += 30
            reasons.append("Hai già venduto questa marca")
        if category and category in self.sold_categories():
            delta += 8
            reasons.append("Hai già venduto questa categoria")
        bought = self.bought_brand_counts()
        if brand and bought.get(brand, 0) >= 2:
            delta += 20
            reasons.append("Hai comprato questa marca 2+ volte")
        ignored = self.ignored_brand_counts()
        if brand and ignored.get(brand, 0) >= BLACKLIST_BRAND_THRESHOLD:
            delta -= 40
            reasons.append("Marca in blacklist (ignorata 5+ volte)")
        elif brand and ignored.get(brand, 0) >= IGNORE_BRAND_THRESHOLD:
            delta -= 20
            reasons.append("Hai ignorato questa marca 3+ volte")
        return delta, reasons
