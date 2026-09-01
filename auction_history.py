"""Storico osservazioni aste per rilevare novità e bassa competizione."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from database import connect, db_enabled, ensure_db

HISTORY_FILE = Path(__file__).resolve().parent / ".auction_history.json"


@dataclass(frozen=True)
class AuctionObservation:
    ts: float
    price_cents: int
    remaining: int


@dataclass
class TrackedAuction:
    auction_id: str
    name: str
    slug: str
    retail_value: float
    url: str
    category_tag: str
    first_seen: float
    last_seen: float
    observations: list[AuctionObservation]

    @property
    def observation_count(self) -> int:
        return len(self.observations)

    @property
    def price_delta_cents(self) -> int:
        if len(self.observations) < 2:
            return 0
        prices = [item.price_cents for item in self.observations]
        return max(prices) - min(prices)

    def is_quiet(self, min_observations: int, max_price_delta_cents: int) -> bool:
        if self.observation_count < min_observations:
            return False
        return self.price_delta_cents <= max_price_delta_cents

    def is_new(self) -> bool:
        return self.observation_count <= 1


class AuctionHistory:
    def __init__(self, path: Path = HISTORY_FILE) -> None:
        self.path = path
        self._items: dict[str, TrackedAuction] = {}
        self._load()

    def _load(self) -> None:
        ensure_db()
        if db_enabled():
            self._load_db()
            return
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, TypeError, ValueError):
            return

        for auction_id, payload in raw.get("auctions", {}).items():
            observations = [
                AuctionObservation(
                    ts=float(item["ts"]),
                    price_cents=int(item["price_cents"]),
                    remaining=int(item["remaining"]),
                )
                for item in payload.get("observations", [])
            ]
            self._items[auction_id] = TrackedAuction(
                auction_id=str(auction_id),
                name=str(payload.get("name", "")),
                slug=str(payload.get("slug", "")),
                retail_value=float(payload.get("retail_value", 0)),
                url=str(payload.get("url", "")),
                category_tag=str(payload.get("category_tag", "")),
                first_seen=float(payload.get("first_seen", 0)),
                last_seen=float(payload.get("last_seen", 0)),
                observations=observations,
            )

    def _load_db(self) -> None:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT auction_id, name, slug, retail_value, url, category_tag,
                       first_seen, last_seen, observations_json
                FROM auction_history
                """
            ).fetchall()
        for row in rows:
            try:
                observations = [
                    AuctionObservation(
                        ts=float(item["ts"]),
                        price_cents=int(item["price_cents"]),
                        remaining=int(item["remaining"]),
                    )
                    for item in json.loads(row["observations_json"] or "[]")
                ]
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                observations = []
            self._items[str(row["auction_id"])] = TrackedAuction(
                auction_id=str(row["auction_id"]),
                name=str(row["name"]),
                slug=str(row["slug"]),
                retail_value=float(row["retail_value"]),
                url=str(row["url"]),
                category_tag=str(row["category_tag"]),
                first_seen=float(row["first_seen"]),
                last_seen=float(row["last_seen"]),
                observations=observations,
            )

    def save(self) -> None:
        if db_enabled():
            with connect() as conn:
                conn.execute("DELETE FROM auction_history")
                for auction_id, item in self._items.items():
                    conn.execute(
                        """
                        INSERT INTO auction_history(
                            auction_id, name, slug, retail_value, url, category_tag,
                            first_seen, last_seen, observations_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            auction_id,
                            item.name,
                            item.slug,
                            item.retail_value,
                            item.url,
                            item.category_tag,
                            item.first_seen,
                            item.last_seen,
                            json.dumps(
                                [
                                    {
                                        "ts": obs.ts,
                                        "price_cents": obs.price_cents,
                                        "remaining": obs.remaining,
                                    }
                                    for obs in item.observations[-20:]
                                ]
                            ),
                        ),
                    )
            return
        payload = {
            "auctions": {
                auction_id: {
                    "name": item.name,
                    "slug": item.slug,
                    "retail_value": item.retail_value,
                    "url": item.url,
                    "category_tag": item.category_tag,
                    "first_seen": item.first_seen,
                    "last_seen": item.last_seen,
                    "observations": [
                        {
                            "ts": obs.ts,
                            "price_cents": obs.price_cents,
                            "remaining": obs.remaining,
                        }
                        for obs in item.observations[-20:]
                    ],
                }
                for auction_id, item in self._items.items()
            }
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def observe(
        self,
        *,
        auction_id: str,
        name: str,
        slug: str,
        retail_value: float,
        url: str,
        category_tag: str,
        price_cents: int,
        remaining: int,
        now: float | None = None,
    ) -> TrackedAuction:
        ts = now if now is not None else time.time()
        existing = self._items.get(auction_id)
        observation = AuctionObservation(ts=ts, price_cents=price_cents, remaining=remaining)

        if existing is None:
            tracked = TrackedAuction(
                auction_id=auction_id,
                name=name,
                slug=slug,
                retail_value=retail_value,
                url=url,
                category_tag=category_tag,
                first_seen=ts,
                last_seen=ts,
                observations=[observation],
            )
            self._items[auction_id] = tracked
            return tracked

        existing.name = name
        existing.slug = slug
        existing.retail_value = retail_value
        existing.url = url
        existing.category_tag = category_tag
        existing.last_seen = ts
        existing.observations.append(observation)
        if len(existing.observations) > 20:
            existing.observations = existing.observations[-20:]
        return existing

    def get(self, auction_id: str) -> TrackedAuction | None:
        return self._items.get(auction_id)

    def prune(self, max_age_seconds: int, now: float | None = None) -> int:
        ts = now if now is not None else time.time()
        stale = [
            auction_id
            for auction_id, item in self._items.items()
            if ts - item.last_seen > max_age_seconds
        ]
        for auction_id in stale:
            del self._items[auction_id]
        return len(stale)
