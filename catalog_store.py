"""Catalogo lotti scoperti (discovery) per smart polling."""

from __future__ import annotations

import json
import time
from typing import Any

from database import connect, ensure_db
from dry_run import dry_run_skip_write
from listing import SourceListing


def _ensure_table() -> None:
    ensure_db()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_listings (
                listing_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                current_price_eur REAL NOT NULL DEFAULT 0,
                ends_at REAL,
                payload_json TEXT NOT NULL,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL,
                watch_priority INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_catalog_source_ends
                ON catalog_listings(source, ends_at);
            """
        )


def _listing_to_payload(listing: SourceListing) -> dict[str, Any]:
    return {
        "source": listing.source,
        "listing_id": listing.listing_id,
        "title": listing.title,
        "url": listing.url,
        "current_price_eur": listing.current_price_eur,
        "shipping_eur": listing.shipping_eur,
        "retail_hint_eur": listing.retail_hint_eur,
        "buy_now_eur": listing.buy_now_eur,
        "bids": listing.bids,
        "remaining_text": listing.remaining_text,
        "remaining_seconds": listing.remaining_seconds,
        "location": listing.location,
        "category_tag": listing.category_tag,
        "extra": listing.extra,
    }


def _payload_to_listing(payload: dict[str, Any]) -> SourceListing:
    return SourceListing(
        source=str(payload.get("source") or ""),
        listing_id=str(payload.get("listing_id") or ""),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or ""),
        current_price_eur=float(payload.get("current_price_eur") or 0),
        shipping_eur=float(payload.get("shipping_eur") or 0),
        retail_hint_eur=float(payload.get("retail_hint_eur") or 0),
        buy_now_eur=payload.get("buy_now_eur"),
        bids=int(payload.get("bids") or 0),
        remaining_text=str(payload.get("remaining_text") or ""),
        remaining_seconds=payload.get("remaining_seconds"),
        location=str(payload.get("location") or ""),
        category_tag=str(payload.get("category_tag") or ""),
        extra=dict(payload.get("extra") or {}),
    )


def upsert_listing(listing: SourceListing, *, ends_at: float | None = None) -> None:
    _ensure_table()
    now = time.time()
    listing_id = listing.history_key
    if ends_at is None and listing.remaining_seconds is not None:
        ends_at = now + max(0, listing.remaining_seconds)
    payload = _listing_to_payload(listing)
    priority = 1 if ends_at and ends_at - now <= 7200 else 0
    with connect() as conn:
        existing = conn.execute(
            "SELECT first_seen FROM catalog_listings WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
        first_seen = float(existing["first_seen"]) if existing else now
        conn.execute(
            """
            INSERT OR REPLACE INTO catalog_listings(
                listing_id, source, title, url, current_price_eur, ends_at,
                payload_json, first_seen, last_seen, watch_priority
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                listing_id,
                listing.source,
                listing.title,
                listing.url,
                listing.current_price_eur,
                ends_at,
                json.dumps(payload),
                first_seen,
                now,
                priority,
            ),
        )


def upsert_many(listings: list[SourceListing]) -> int:
    if dry_run_skip_write("catalog upsert", count=len(listings)):
        return len(listings)
    for listing in listings:
        upsert_listing(listing)
    return len(listings)


def listings_closing_within(source: str, hours: float) -> list[SourceListing]:
    _ensure_table()
    deadline = time.time() + hours * 3600
    now = time.time()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT payload_json FROM catalog_listings
            WHERE source = ?
              AND (ends_at IS NULL OR (ends_at > ? AND ends_at <= ?))
            ORDER BY ends_at ASC NULLS LAST
            """,
            (source, now, deadline),
        ).fetchall()
    result: list[SourceListing] = []
    for row in rows:
        try:
            result.append(_payload_to_listing(json.loads(row["payload_json"])))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return result


def prune_stale(*, max_age_hours: float = 168) -> int:
    if dry_run_skip_write("catalog prune"):
        return 0
    _ensure_table()
    cutoff = time.time() - max_age_hours * 3600
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM catalog_listings WHERE last_seen < ? AND (ends_at IS NULL OR ends_at < ?)",
            (cutoff, time.time()),
        )
        return cur.rowcount
