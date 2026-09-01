"""Allocazione capitale attivo e limiti esposizione per categoria/brand."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from database import connect, ensure_db
from inventory import ensure_inventory_schema


@dataclass(frozen=True)
class AllocationCheck:
    ok: bool
    reason: str
    active_capital_eur: float
    max_capital_eur: float
    category_exposure_eur: float
    category_limit_eur: float
    remaining_total_eur: float
    remaining_category_eur: float


def _max_active_capital() -> float:
    try:
        return float(os.getenv("MAX_ACTIVE_CAPITAL_EUR", "2000"))
    except ValueError:
        return 2000.0


def _category_exposure_pct(category: str) -> float:
    untested = {
        item.strip().lower()
        for item in os.getenv(
            "UNTESTED_CATEGORIES", "elettronica,smartwatch,utensili"
        ).split(",")
        if item.strip()
    }
    default = float(os.getenv("MAX_CATEGORY_EXPOSURE_PCT", "50"))
    untested_pct = float(os.getenv("MAX_UNTESTED_CATEGORY_EXPOSURE_PCT", "30"))
    if (category or "").lower() in untested and not _category_has_sales(category):
        return untested_pct
    return default


def _category_has_sales(category: str) -> bool:
    ensure_db()
    ensure_inventory_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM inventory WHERE status = 'sold' AND category = ?",
            (category or "",),
        ).fetchone()
    return bool(row and int(row["n"]) > 0)


def active_capital_eur() -> float:
    ensure_db()
    ensure_inventory_schema()
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(buy_price_eur), 0) AS total FROM inventory WHERE status = 'pending'"
        ).fetchone()
    return float(row["total"] if row else 0)


def category_exposure_eur(category: str) -> float:
    ensure_db()
    ensure_inventory_schema()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(buy_price_eur), 0) AS total
            FROM inventory WHERE status = 'pending' AND category = ?
            """,
            (category or "",),
        ).fetchone()
    return float(row["total"] if row else 0)


def portfolio_summary() -> dict[str, Any]:
    ensure_db()
    ensure_inventory_schema()
    max_cap = _max_active_capital()
    active = active_capital_eur()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT category, SUM(buy_price_eur) AS exposure, COUNT(*) AS n
            FROM inventory WHERE status = 'pending'
            GROUP BY category ORDER BY exposure DESC
            """
        ).fetchall()
    by_cat = [
        {
            "category": str(row["category"] or "—"),
            "exposure_eur": float(row["exposure"]),
            "n": int(row["n"]),
            "limit_eur": max_cap * _category_exposure_pct(str(row["category"])) / 100,
        }
        for row in rows
    ]
    return {
        "active_capital_eur": active,
        "max_capital_eur": max_cap,
        "remaining_eur": max(0.0, max_cap - active),
        "utilization_pct": (active / max_cap * 100) if max_cap > 0 else 0.0,
        "by_category": by_cat,
        "pending_count": sum(item["n"] for item in by_cat),
    }


def check_allocation(
    category: str,
    brand: str | None,
    proposed_buy_eur: float,
) -> AllocationCheck:
    if os.getenv("CAPITAL_ALLOCATOR", "true").lower() not in ("1", "true", "yes"):
        max_cap = _max_active_capital()
        return AllocationCheck(
            ok=True,
            reason="",
            active_capital_eur=active_capital_eur(),
            max_capital_eur=max_cap,
            category_exposure_eur=0.0,
            category_limit_eur=max_cap,
            remaining_total_eur=max_cap,
            remaining_category_eur=max_cap,
        )

    max_cap = _max_active_capital()
    active = active_capital_eur()
    cat_exp = category_exposure_eur(category)
    cat_pct = _category_exposure_pct(category)
    cat_limit = max_cap * cat_pct / 100.0
    remaining_total = max_cap - active
    remaining_cat = cat_limit - cat_exp

    if active + proposed_buy_eur > max_cap:
        return AllocationCheck(
            ok=False,
            reason=(
                f"Capitale attivo saturo ({active:.0f}/{max_cap:.0f} €). "
                f"Budget residuo: {max(0, remaining_total):.0f} €."
            ),
            active_capital_eur=active,
            max_capital_eur=max_cap,
            category_exposure_eur=cat_exp,
            category_limit_eur=cat_limit,
            remaining_total_eur=max(0.0, remaining_total),
            remaining_category_eur=max(0.0, remaining_cat),
        )

    if cat_exp + proposed_buy_eur > cat_limit:
        tested = _category_has_sales(category)
        label = "non testata" if not tested else category
        return AllocationCheck(
            ok=False,
            reason=(
                f"Capitale di categoria saturo ({label}: {cat_exp:.0f}/{cat_limit:.0f} €, "
                f"max {cat_pct:.0f}% portafoglio)."
            ),
            active_capital_eur=active,
            max_capital_eur=max_cap,
            category_exposure_eur=cat_exp,
            category_limit_eur=cat_limit,
            remaining_total_eur=max(0.0, remaining_total),
            remaining_category_eur=max(0.0, remaining_cat),
        )

    brand_cap_pct = float(os.getenv("MAX_BRAND_EXPOSURE_PCT", "25"))
    if brand:
        ensure_inventory_schema()
        with connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(buy_price_eur), 0) AS total
                FROM inventory WHERE status = 'pending' AND brand = ?
                """,
                (brand,),
            ).fetchone()
        brand_exp = float(row["total"] if row else 0)
        brand_limit = max_cap * brand_cap_pct / 100.0
        if brand_exp + proposed_buy_eur > brand_limit:
            return AllocationCheck(
                ok=False,
                reason=(
                    f"Esposizione brand {brand} satura "
                    f"({brand_exp:.0f}/{brand_limit:.0f} €)."
                ),
                active_capital_eur=active,
                max_capital_eur=max_cap,
                category_exposure_eur=cat_exp,
                category_limit_eur=cat_limit,
                remaining_total_eur=max(0.0, remaining_total),
                remaining_category_eur=max(0.0, remaining_cat),
            )

    return AllocationCheck(
        ok=True,
        reason="",
        active_capital_eur=active,
        max_capital_eur=max_cap,
        category_exposure_eur=cat_exp,
        category_limit_eur=cat_limit,
        remaining_total_eur=max(0.0, remaining_total - proposed_buy_eur),
        remaining_category_eur=max(0.0, remaining_cat - proposed_buy_eur),
    )


def format_portfolio_telegram() -> str:
    summary = portfolio_summary()
    lines = [
        "💰 <b>Portafoglio attivo</b>",
        f"Investito: <b>{summary['active_capital_eur']:.0f} €</b> / "
        f"{summary['max_capital_eur']:.0f} € "
        f"({summary['utilization_pct']:.0f}%)",
        f"Disponibile: <b>{summary['remaining_eur']:.0f} €</b> · "
        f"{summary['pending_count']} lotti pending",
        "",
        "<b>Per categoria</b>",
    ]
    for item in summary["by_category"][:8]:
        lines.append(
            f"• {item['category']}: {item['exposure_eur']:.0f} € "
            f"(cap {item['limit_eur']:.0f} €, {item['n']} lotti)"
        )
    if not summary["by_category"]:
        lines.append("• Nessun lotto in inventario pending")
    return "\n".join(lines)
