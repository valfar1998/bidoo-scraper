"""Inventario post-acquisto: stima vs vendita reale, precision score."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from database import connect, ensure_db
from dry_run import dry_run_skip_write
from haircut_model import (
    RiskCoefficients,
    append_sale_sample,
    regression_haircut_adjustment,
    risk_coefficients_from_data,
)


@dataclass(frozen=True)
class InventoryItem:
    listing_id: str
    title: str
    source: str
    category: str
    brand: str
    url: str
    buy_price_eur: float
    estimated_profit_eur: float
    estimated_resale_eur: float
    recommended_max_bid_eur: float
    status: str
    sold_price_eur: float
    bought_at: float
    sold_at: float


def _ensure_tables() -> None:
    ensure_db()
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS alert_snapshots (
                listing_id TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_id TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                brand TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                buy_price_eur REAL NOT NULL DEFAULT 0,
                estimated_profit_eur REAL NOT NULL DEFAULT 0,
                estimated_resale_eur REAL NOT NULL DEFAULT 0,
                recommended_max_bid_eur REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                sold_price_eur REAL NOT NULL DEFAULT 0,
                bought_at REAL NOT NULL,
                sold_at REAL NOT NULL DEFAULT 0,
                snapshot_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory(status);
            """
        )
        _migrate_inventory_columns(conn)


def _migrate_inventory_columns(conn) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(inventory)").fetchall()}
    additions = {
        "list_price_eur": "REAL NOT NULL DEFAULT 0",
        "listed_at": "REAL NOT NULL DEFAULT 0",
        "last_repriced_at": "REAL NOT NULL DEFAULT 0",
        "last_market_check": "REAL NOT NULL DEFAULT 0",
        "ebay_offer_id": "TEXT NOT NULL DEFAULT ''",
        "ebay_listing_url": "TEXT NOT NULL DEFAULT ''",
    }
    for col, typedef in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE inventory ADD COLUMN {col} {typedef}")


def ensure_inventory_schema() -> None:
    _ensure_tables()


def save_alert_snapshot(
    listing_id: str,
    *,
    listing: dict[str, Any],
    estimate: dict[str, Any],
    profile_key: str,
) -> None:
    if dry_run_skip_write("alert_snapshot"):
        return
    _ensure_tables()
    payload = {
        "listing": listing,
        "estimate": estimate,
        "profile_key": profile_key,
        "ts": time.time(),
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO alert_snapshots(listing_id, snapshot_json, ts)
            VALUES(?, ?, ?)
            """,
            (listing_id, json.dumps(payload), time.time()),
        )


def _snapshot(listing_id: str) -> dict[str, Any] | None:
    _ensure_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT snapshot_json FROM alert_snapshots WHERE listing_id = ?",
            (listing_id,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["snapshot_json"])
    except json.JSONDecodeError:
        return None


def get_alert_snapshot(listing_id: str) -> dict[str, Any] | None:
    return _snapshot(listing_id)


def create_from_bought(listing_id: str, *, title: str = "", buy_price: float | None = None) -> InventoryItem | None:
    _ensure_tables()
    snap = _snapshot(listing_id)
    listing = (snap or {}).get("listing") or {}
    estimate = (snap or {}).get("estimate") or {}
    if not listing and not title:
        return None
    price = buy_price
    if price is None:
        price = float(listing.get("current_price_eur") or estimate.get("landed_cost_eur") or 0)
    item = InventoryItem(
        listing_id=listing_id,
        title=title or str(listing.get("title") or listing_id),
        source=str(listing.get("source") or listing_id.split(":", 1)[0]),
        category=str(estimate.get("category_tag") or listing.get("category_tag") or ""),
        brand=str(estimate.get("brand") or ""),
        url=str(listing.get("url") or ""),
        buy_price_eur=price,
        estimated_profit_eur=float(
            estimate.get("expected_profit_eur") or estimate.get("best_net_profit_eur") or 0
        ),
        estimated_resale_eur=float(estimate.get("inferred_resale_eur") or 0),
        recommended_max_bid_eur=float(
            estimate.get("recommended_max_bid_eur") or estimate.get("max_bid_eur") or 0
        ),
        status="pending",
        sold_price_eur=0.0,
        bought_at=time.time(),
        sold_at=0.0,
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO inventory(
                listing_id, title, source, category, brand, url,
                buy_price_eur, estimated_profit_eur, estimated_resale_eur,
                recommended_max_bid_eur, status, sold_price_eur, bought_at, sold_at,
                snapshot_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, 0, ?)
            """,
            (
                item.listing_id,
                item.title,
                item.source,
                item.category,
                item.brand,
                item.url,
                item.buy_price_eur,
                item.estimated_profit_eur,
                item.estimated_resale_eur,
                item.recommended_max_bid_eur,
                item.bought_at,
                json.dumps(snap or {}),
            ),
        )
    return item


def get_inventory_item(listing_id: str) -> dict[str, Any] | None:
    _ensure_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM inventory WHERE listing_id = ?", (listing_id,)
        ).fetchone()
    return dict(row) if row else None


def update_ebay_listing(
    listing_id: str,
    *,
    offer_id: str,
    listing_url: str,
    list_price_eur: float,
) -> None:
    _ensure_tables()
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            UPDATE inventory
            SET ebay_offer_id = ?, ebay_listing_url = ?, list_price_eur = ?,
                listed_at = CASE WHEN listed_at > 0 THEN listed_at ELSE ? END
            WHERE listing_id = ?
            """,
            (offer_id, listing_url, list_price_eur, now, listing_id),
        )


def record_sale(listing_id: str, sold_price_eur: float) -> InventoryItem | None:
    _ensure_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM inventory WHERE listing_id = ?", (listing_id,)
        ).fetchone()
    if not row:
        return None
    sold_at = time.time()
    actual_profit = sold_price_eur - float(row["buy_price_eur"])
    estimated = float(row["estimated_profit_eur"])
    category = str(row["category"] or "default")
    _update_category_calibration(category, estimated, actual_profit)
    with connect() as conn:
        conn.execute(
            """
            UPDATE inventory
            SET status = 'sold', sold_price_eur = ?, sold_at = ?
            WHERE listing_id = ?
            """,
            (sold_price_eur, sold_at, listing_id),
        )
    return InventoryItem(
        listing_id=listing_id,
        title=str(row["title"]),
        source=str(row["source"]),
        category=category,
        brand=str(row["brand"]),
        url=str(row["url"]),
        buy_price_eur=float(row["buy_price_eur"]),
        estimated_profit_eur=estimated,
        estimated_resale_eur=float(row["estimated_resale_eur"]),
        recommended_max_bid_eur=float(row["recommended_max_bid_eur"]),
        status="sold",
        sold_price_eur=sold_price_eur,
        bought_at=float(row["bought_at"]),
        sold_at=sold_at,
    )


def _load_calibration(category: str) -> dict:
    key = f"calib:{category or 'default'}"
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if not row:
        return {"n": 0, "err_sum": 0.0, "haircut_adj": 0.0, "samples": []}
    try:
        data = json.loads(row["value"])
        if not isinstance(data, dict):
            raise json.JSONDecodeError("not object", "", 0)
        return data
    except json.JSONDecodeError:
        return {"n": 0, "err_sum": 0.0, "haircut_adj": 0.0, "samples": []}


def _save_calibration(category: str, data: dict) -> None:
    key = f"calib:{category or 'default'}"
    data["haircut_adj"] = regression_haircut_adjustment(data)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
            (key, json.dumps(data)),
        )


def _update_category_calibration(category: str, estimated: float, actual: float) -> None:
    if estimated <= 0 and actual <= 0:
        return
    data = _load_calibration(category)
    data = append_sale_sample(data, estimated=estimated, actual=actual)
    _save_calibration(category, data)


def category_haircut_adjustment(category: str) -> float:
    _ensure_tables()
    return regression_haircut_adjustment(_load_calibration(category))


def category_risk_coefficients(category: str) -> RiskCoefficients:
    _ensure_tables()
    return risk_coefficients_from_data(_load_calibration(category))


def precision_metrics() -> dict[str, Any]:
    _ensure_tables()
    with connect() as conn:
        rows = conn.execute(
            "SELECT estimated_profit_eur, sold_price_eur, buy_price_eur, category FROM inventory WHERE status = 'sold'"
        ).fetchall()
    if not rows:
        return {"n": 0, "precision_pct": 0.0, "mae_eur": 0.0}
    errors: list[float] = []
    within_20: list[bool] = []
    for row in rows:
        est = float(row["estimated_profit_eur"])
        actual = float(row["sold_price_eur"]) - float(row["buy_price_eur"])
        err = abs(actual - est)
        errors.append(err)
        within_20.append(err <= max(10.0, abs(est) * 0.2))
    mae = sum(errors) / len(errors)
    precision = sum(1 for ok in within_20 if ok) / len(within_20) * 100
    return {"n": len(rows), "precision_pct": precision, "mae_eur": mae}


def format_sale_report(item: InventoryItem) -> str:
    actual_profit = item.sold_price_eur - item.buy_price_eur
    delta = actual_profit - item.estimated_profit_eur
    sign = "+" if delta >= 0 else ""
    metrics = precision_metrics()
    return (
        f"✅ <b>Venduto</b> <code>{item.listing_id}</code>\n"
        f"Profitto stimato: {item.estimated_profit_eur:.0f} €\n"
        f"Profitto reale: <b>{actual_profit:.0f} €</b> ({sign}{delta:.0f} €)\n"
        f"Precision score: {metrics['precision_pct']:.0f}% su {metrics['n']} vendite "
        f"(MAE {metrics['mae_eur']:.0f} €)"
    )
