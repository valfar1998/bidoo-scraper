"""Repricing dinamico per lotti in giacenza (markdown su comps freschi)."""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comp_embeddings import match_comp_semantic
from comps import load_comps
from database import connect, ensure_db
from inventory import ensure_inventory_schema
from sources.ebay_api import search_auctions
from telegram_notifier import escape_html, send_telegram_message
from telegram_topics import resolve_ops_topic


@dataclass(frozen=True)
class RepriceSuggestion:
    listing_id: str
    title: str
    days_pending: int
    list_price_eur: float
    market_avg_eur: float
    suggested_price_eur: float
    reason: str
    markdown_pct: float


def _stale_days() -> int:
    return int(os.getenv("REPRICER_STALE_DAYS", "14"))


def _critical_days() -> int:
    return int(os.getenv("REPRICER_CRITICAL_DAYS", "30"))


def _markdown_pct_stale() -> float:
    return float(os.getenv("REPRICER_STALE_MARKDOWN_PCT", "10"))


def _markdown_pct_market_drop() -> float:
    return float(os.getenv("REPRICER_MARKET_DROP_MARKDOWN_PCT", "12"))


def _market_drop_threshold() -> float:
    return float(os.getenv("REPRICER_MARKET_DROP_THRESHOLD_PCT", "8"))


def list_pending_items() -> list[dict[str, Any]]:
    ensure_inventory_schema()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT listing_id, title, category, brand, buy_price_eur,
                   estimated_resale_eur, list_price_eur, bought_at, last_market_check
            FROM inventory WHERE status = 'pending'
            ORDER BY bought_at ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _current_list_price(row: dict[str, Any]) -> float:
    listed = float(row.get("list_price_eur") or 0)
    if listed > 0:
        return listed
    return float(row.get("estimated_resale_eur") or 0)


def _fetch_market_avg(title: str, brand: str = "") -> tuple[float, str]:
    query = f"{brand} {title}".strip()[:80] if brand else title[:80]
    comp, score = match_comp_semantic(query, load_comps())
    comp_price = float(comp.best_avg if comp else 0)
    live_prices: list[float] = []
    try:
        items = search_auctions(query, limit=20, extra_filter="buyingOptions:{FIXED_PRICE}")
        for item in items:
            price = item.get("price") or {}
            val = price.get("value")
            if val is not None:
                live_prices.append(float(val))
    except Exception as exc:
        print(f"[repricer] Browse API: {exc}")
    live_avg = sum(live_prices) / len(live_prices) if live_prices else 0.0
    if comp_price > 0 and live_avg > 0:
        return (comp_price + live_avg) / 2, f"comps+live (sim={score:.2f})"
    if live_avg > 0:
        return live_avg, "live eBay"
    if comp_price > 0:
        return comp_price, f"comps (sim={score:.2f})"
    return 0.0, "nessun dato"


def analyze_item(row: dict[str, Any], *, now: float | None = None) -> RepriceSuggestion | None:
    now = now or time.time()
    bought_at = float(row.get("bought_at") or now)
    days = int((now - bought_at) / 86400)
    list_price = _current_list_price(row)
    if list_price <= 0:
        return None

    market_avg, market_src = _fetch_market_avg(
        str(row.get("title") or ""),
        str(row.get("brand") or ""),
    )
    suggested = list_price
    reason = ""
    markdown = 0.0

    if days >= _stale_days():
        markdown = max(markdown, _markdown_pct_stale())
        reason = f"Giacenza {days} giorni (soglia {_stale_days()})"

    if market_avg > 0 and list_price > 0:
        drop_pct = (list_price - market_avg) / list_price * 100
        if drop_pct >= _market_drop_threshold():
            markdown = max(markdown, _markdown_pct_market_drop())
            reason = (
                f"{reason}; " if reason else ""
            ) + f"Mercato sceso ({market_src}: {market_avg:.0f} € vs listino {list_price:.0f} €)"

    if days >= _critical_days():
        markdown = max(markdown, _markdown_pct_stale() + 5)
        reason = (
            f"{reason}; " if reason else ""
        ) + f"Giacenza critica ≥{_critical_days()} gg — libera capitale"

    if markdown <= 0:
        return None

    suggested = round(max(5.0, list_price * (1 - markdown / 100)), 2)
    if suggested >= list_price * 0.98:
        return None

    return RepriceSuggestion(
        listing_id=str(row["listing_id"]),
        title=str(row.get("title") or row["listing_id"]),
        days_pending=days,
        list_price_eur=list_price,
        market_avg_eur=market_avg,
        suggested_price_eur=suggested,
        reason=reason,
        markdown_pct=markdown,
    )


def record_reprice(listing_id: str, new_price_eur: float) -> bool:
    ensure_inventory_schema()
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE inventory
            SET list_price_eur = ?, last_repriced_at = ?, last_market_check = ?
            WHERE listing_id = ? AND status = 'pending'
            """,
            (new_price_eur, time.time(), time.time(), listing_id),
        )
    return cur.rowcount > 0  # type: ignore[attr-defined]


def _touch_market_check(listing_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE inventory SET last_market_check = ? WHERE listing_id = ?",
            (time.time(), listing_id),
        )


def format_suggestion_html(s: RepriceSuggestion) -> str:
    return (
        f"📉 <b>Repricing suggerito</b>\n"
        f"<code>{escape_html(s.listing_id)}</code>\n"
        f"{escape_html(s.title[:70])}\n\n"
        f"Listino attuale: <b>{s.list_price_eur:.2f} €</b>\n"
        f"Mercato medio: {s.market_avg_eur:.0f} €\n"
        f"Prezzo suggerito: <b>{s.suggested_price_eur:.2f} €</b> "
        f"(-{s.markdown_pct:.0f}%)\n"
        f"Giacenza: {s.days_pending} gg\n"
        f"Motivo: {escape_html(s.reason)}\n\n"
        f"Applica: <code>/reprice {escape_html(s.listing_id)} {s.suggested_price_eur:.2f}</code>"
    )


def run_repricer_job(*, dry_run: bool = False) -> list[RepriceSuggestion]:
    ensure_db()
    suggestions: list[RepriceSuggestion] = []
    for row in list_pending_items():
        item = analyze_item(row)
        if item:
            suggestions.append(item)
            _touch_market_check(item.listing_id)
    if dry_run:
        return suggestions

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    thread_id = resolve_ops_topic("repricing")
    if token and chat_id and suggestions:
        header = (
            f"🔄 <b>Repricing settimanale</b> — {len(suggestions)} lotto/i da rivedere\n"
            f"{datetime.now().strftime('%d/%m/%Y')}\n"
        )
        send_telegram_message(token, chat_id, header, message_thread_id=thread_id)
        for item in suggestions[:15]:
            send_telegram_message(
                token, chat_id, format_suggestion_html(item), message_thread_id=thread_id
            )
    return suggestions


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="Repricing dinamico inventario pending.")
    parser.add_argument("--dry-run", action="store_true", help="Solo analisi, no Telegram")
    args = parser.parse_args()
    items = run_repricer_job(dry_run=args.dry_run)
    print(f"[repricer] {len(items)} suggerimenti")
    for item in items:
        print(
            f"  {item.listing_id}: {item.list_price_eur:.0f} → "
            f"{item.suggested_price_eur:.0f} € ({item.reason})"
        )


if __name__ == "__main__":
    main()
