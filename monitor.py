#!/usr/bin/env python3
"""Monitor Bidoo per rivendita Vinted/eBay (solo lettura, nessuna puntata)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

from auction_history import AuctionHistory
from bidoo_client import (
    Auction,
    LiveAuction,
    fetch_auctions,
    fetch_live_auctions,
    open_fetch_context,
    seconds_remaining,
)
from bidoo_errors import CloudflareBlockedError
from filters import (
    fits_category_retail_band,
    is_excluded_auction,
    is_hyper_competitive,
    parse_exclude_patterns,
)
from resale_categories import DEFAULT_BASE_URL, ResaleCategory, resolve_categories
from resale_estimator import ResaleEstimate, estimate_resale
from telegram_notifier import send_telegram_message


class AlertKind(str, Enum):
    NEW = "new"
    QUIET = "quiet"
    DEAL = "deal"


@dataclass
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    categories: list[ResaleCategory]
    base_url: str
    bid_cost_estimate: float
    min_resale_profit_eur: float
    min_resale_margin_pct: float
    min_resale_score: int
    shipping_cost_eur: float
    quiet_min_observations: int
    quiet_max_price_delta_cents: int
    min_price_headroom_eur: float
    min_retail_value: float
    history_max_age_hours: int
    poll_interval: int
    alert_cooldown: int
    exclude_patterns: list[str]
    alert_kinds: set[AlertKind]


STATE_FILE = Path(__file__).resolve().parent / ".alert_state.json"


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print(
            "Configura TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID in .env "
            "(vedi .env.example).",
            file=sys.stderr,
        )
        sys.exit(1)

    alert_kinds = _parse_alert_kinds(os.getenv("ALERT_KINDS", "deal"))

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        categories=resolve_categories(
            os.getenv("RESALE_CATEGORIES", ""),
            base_url=os.getenv("BIDOO_BASE_URL", DEFAULT_BASE_URL),
        ),
        base_url=os.getenv("BIDOO_BASE_URL", DEFAULT_BASE_URL),
        bid_cost_estimate=float(os.getenv("BID_COST_ESTIMATE", "0.20")),
        min_resale_profit_eur=float(os.getenv("MIN_RESALE_PROFIT_EUR", "25")),
        min_resale_margin_pct=float(os.getenv("MIN_RESALE_MARGIN_PCT", "25")),
        min_resale_score=int(os.getenv("MIN_RESALE_SCORE", "50")),
        shipping_cost_eur=float(os.getenv("SHIPPING_COST_EUR", "8")),
        quiet_min_observations=int(os.getenv("QUIET_MIN_OBSERVATIONS", "3")),
        quiet_max_price_delta_cents=int(os.getenv("QUIET_MAX_PRICE_DELTA_CENTS", "8")),
        min_price_headroom_eur=float(os.getenv("MIN_PRICE_HEADROOM_EUR", "0.50")),
        min_retail_value=float(os.getenv("MIN_RETAIL_VALUE", "40")),
        history_max_age_hours=int(os.getenv("HISTORY_MAX_AGE_HOURS", "72")),
        poll_interval=int(os.getenv("POLL_INTERVAL", "300")),
        alert_cooldown=int(os.getenv("ALERT_COOLDOWN", "3600")),
        exclude_patterns=parse_exclude_patterns(os.getenv("EXCLUDE_PATTERNS", "")),
        alert_kinds=alert_kinds,
    )


def _parse_alert_kinds(raw: str) -> set[AlertKind]:
    kinds: set[AlertKind] = set()
    for item in raw.split(","):
        key = item.strip().lower()
        if not key:
            continue
        try:
            kinds.add(AlertKind(key))
        except ValueError:
            print(f"Tipo alert sconosciuto ignorato: {key}", file=sys.stderr)
    return kinds or {AlertKind.DEAL}


def _state_key(auction_id: str) -> str:
    return f"auction:{auction_id}"


def load_alert_state() -> dict[str, float]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(key): float(value) for key, value in data.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_alert_state(last_alert: dict[str, float]) -> None:
    STATE_FILE.write_text(json.dumps(last_alert, indent=2), encoding="utf-8")


def _platform_label(platform: str) -> str:
    return {"vinted": "Vinted", "ebay": "eBay", "both": "Vinted/eBay"}.get(
        platform, platform
    )


def build_alert(
    *,
    kind: AlertKind,
    auction: Auction,
    live: LiveAuction,
    category: ResaleCategory,
    estimate: ResaleEstimate,
    price_delta_cents: int = 0,
    observation_count: int = 1,
) -> str:
    kind_label = {
        AlertKind.DEAL: "Occasione con margine",
        AlertKind.QUIET: "Asta tranquilla + margine",
        AlertKind.NEW: "Nuova asta interessante",
    }[kind]

    quiet_note = ""
    if kind == AlertKind.QUIET:
        quiet_note = (
            f"\nPochi rilanci: +{price_delta_cents} cent in "
            f"{observation_count} controlli."
        )

    return (
        f"✅ <b>Bidoo · {kind_label}</b>\n\n"
        f"<b>{auction.name}</b>\n"
        f"{category.name} → rivendi su {_platform_label(estimate.platform)}\n\n"
        f"<b>━━ COSA FARE ━━</b>\n"
        f"1. AutoPuntata max: <b>{estimate.autobid_limit_eur:.2f} €</b>\n"
        f"2. Budget puntate max: <b>~{estimate.max_bid_credits_eur:.0f} €</b> "
        f"({estimate.max_additional_bids} rilanci)\n"
        f"3. Oltre {estimate.autobid_limit_eur:.2f} € → <b>STOP</b>\n"
        f"{estimate.verdict_reason}\n\n"
        f"<b>━━ NUMERI ━━</b>\n"
        f"Prezzo asta ora: {live.price_eur:.2f} €\n"
        f"Rivendita stimata: ~{estimate.resale_value_eur:.0f} €\n"
        f"Guadagno stimato: <b>+{estimate.net_profit_eur:.0f} €</b> "
        f"({estimate.margin_pct:.0f}%)\n"
        f"Limite pareggio: {estimate.break_even_auction_price_eur:.2f} € "
        f"(sotto questo perdi soldi)\n"
        f"Score: {estimate.score}/100"
        f"{quiet_note}\n\n"
        f"<i>Valore Bidoo ({auction.retail_value:.0f} €) è solo indicativo. "
        f"Controlla prezzi venduti su {_platform_label(estimate.platform)}.</i>\n"
        f"<a href=\"{auction.url}\">Apri asta</a>"
    )


def pick_alert(
    *,
    auction: Auction,
    live: LiveAuction,
    category: ResaleCategory,
    tracked_before: bool,
    tracked,
    settings: Settings,
) -> tuple[AlertKind, ResaleEstimate] | None:
    if live.state != "ON":
        return None

    if is_excluded_auction(auction.name, auction.slug, settings.exclude_patterns):
        return None
    if is_hyper_competitive(auction.name, auction.slug):
        return None
    if auction.retail_value < settings.min_retail_value:
        return None
    if not fits_category_retail_band(auction.retail_value, category):
        return None

    quiet = tracked.is_quiet(
        settings.quiet_min_observations,
        settings.quiet_max_price_delta_cents,
    )
    is_new = not tracked_before

    estimate = estimate_resale(
        retail_value=auction.retail_value,
        current_price_eur=live.price_eur,
        category=category,
        name=auction.name,
        slug=auction.slug,
        bid_cost_per_bid=settings.bid_cost_estimate,
        min_profit_eur=settings.min_resale_profit_eur,
        min_margin_pct=settings.min_resale_margin_pct,
        shipping_eur=settings.shipping_cost_eur,
        min_price_headroom_eur=settings.min_price_headroom_eur,
        quiet_bonus=quiet,
    )

    if estimate.verdict != "conviene":
        return None
    if estimate.score < settings.min_resale_score:
        return None

    candidates: list[tuple[int, AlertKind]] = []
    if AlertKind.DEAL in settings.alert_kinds and estimate.is_viable:
        candidates.append((3, AlertKind.DEAL))
    if AlertKind.QUIET in settings.alert_kinds and quiet:
        candidates.append((2, AlertKind.QUIET))
    if AlertKind.NEW in settings.alert_kinds and is_new:
        candidates.append((1, AlertKind.NEW))

    if not candidates:
        return None

    kind = max(candidates, key=lambda item: item[0])[1]
    return kind, estimate


def scan_category(
    fetch,
    *,
    category: ResaleCategory,
    settings: Settings,
    history: AuctionHistory,
    last_alert: dict[str, float],
    now: float,
) -> int:
    sent = 0
    url = category.url(settings.base_url)
    auctions = fetch_auctions(fetch, url)
    print(f"[{category.name}] Catalogo: {len(auctions)} aste.")

    candidates = [
        auction
        for auction in auctions
        if not is_excluded_auction(auction.name, auction.slug, settings.exclude_patterns)
        and not is_hyper_competitive(auction.name, auction.slug)
        and auction.retail_value >= settings.min_retail_value
        and fits_category_retail_band(auction.retail_value, category)
    ]
    print(f"[{category.name}] Candidate rivendita: {len(candidates)}.")

    if not candidates:
        return 0

    server_time, live_items = fetch_live_auctions(
        fetch,
        settings.base_url,
        [auction.auction_id for auction in candidates],
    )
    live_by_id = {item.auction_id: item for item in live_items}

    for auction in candidates:
        live = live_by_id.get(auction.auction_id)
        if not live:
            continue

        remaining = seconds_remaining(server_time, live)
        tracked_before = history.get(auction.auction_id) is not None
        tracked = history.observe(
            auction_id=auction.auction_id,
            name=auction.name,
            slug=auction.slug,
            retail_value=auction.retail_value,
            url=auction.url,
            category_tag=category.tag,
            price_cents=live.price_cents,
            remaining=remaining,
            now=now,
        )

        picked = pick_alert(
            auction=auction,
            live=live,
            category=category,
            tracked_before=tracked_before,
            tracked=tracked,
            settings=settings,
        )
        if not picked:
            continue

        kind, estimate = picked
        key = _state_key(auction.auction_id)
        if now - last_alert.get(key, 0) < settings.alert_cooldown:
            continue

        message = build_alert(
            kind=kind,
            auction=auction,
            live=live,
            category=category,
            estimate=estimate,
            price_delta_cents=tracked.price_delta_cents,
            observation_count=tracked.observation_count,
        )
        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            message,
        )
        last_alert[key] = now
        sent += 1
        print(
            f"Alert {kind.value}: {auction.name} "
            f"(+{estimate.net_profit_eur:.0f} €, limite {estimate.autobid_limit_eur:.2f} €)"
        )

    return sent


def run_check(settings: Settings, last_alert: dict[str, float], history: AuctionHistory) -> int:
    now = time.time()
    sent = 0

    with open_fetch_context() as fetch:
        for category in settings.categories:
            sent += scan_category(
                fetch,
                category=category,
                settings=settings,
                history=history,
                last_alert=last_alert,
                now=now,
            )

    pruned = history.prune(settings.history_max_age_hours * 3600, now=now)
    if pruned:
        print(f"Storico ripulito: {pruned} aste obsolete rimosse.")
    history.save()
    save_alert_state(last_alert)
    return sent


def print_rules(settings: Settings) -> None:
    print("Monitor rivendita Bidoo → Vinted/eBay")
    print(
        f"Categorie: {', '.join(category.name for category in settings.categories)}"
    )
    print(
        f"Alert: {', '.join(kind.value for kind in sorted(settings.alert_kinds, key=lambda k: k.value))} | "
        f"solo se CONVIENE | margine min {settings.min_resale_margin_pct:.0f}% | "
        f"profitto min {settings.min_resale_profit_eur:.0f} € | "
        f"score min {settings.min_resale_score} | "
        f"valore min {settings.min_retail_value:.0f} €"
    )
    print(
        f"Tranquilla: >= {settings.quiet_min_observations} osservazioni, "
        f"delta prezzo <= {settings.quiet_max_price_delta_cents} cent | "
        f"cooldown {settings.alert_cooldown // 60} min"
    )


def should_soft_fail() -> bool:
    return os.getenv("BIDOO_SOFT_FAIL", "").lower() in ("1", "true", "yes")


def is_cloudflare_block(exc: Exception) -> bool:
    if isinstance(exc, CloudflareBlockedError):
        return True
    message = str(exc).lower()
    return (
        "ci siamo quasi" in message
        or "cloudflare" in message
        or "impossibile caricare le aste" in message
    )


def handle_run_error(exc: Exception) -> None:
    print(f"Errore: {exc}", file=sys.stderr)
    if should_soft_fail() and is_cloudflare_block(exc):
        print(
            "Avviso: Cloudflare blocca questo server. "
            "Usa il Pianificatore Windows da casa (run-check.ps1).",
            file=sys.stderr,
        )
        sys.exit(0)
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Bidoo per rivendita su Vinted/eBay (solo lettura)."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Esegue un solo giro e termina (ideale ogni 5 minuti).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    last_alert = load_alert_state()
    history = AuctionHistory()

    print_rules(settings)

    if args.once:
        try:
            sent = run_check(settings, last_alert, history)
            print(f"Controllo completato. Alert inviati: {sent}.")
        except Exception as exc:
            handle_run_error(exc)
        return

    print(f"Loop continuo ogni {settings.poll_interval}s (Ctrl+C per fermare).")
    while True:
        try:
            sent = run_check(settings, last_alert, history)
            if sent == 0:
                print("Nessun alert in questo ciclo.")
        except KeyboardInterrupt:
            print("\nMonitor fermato.")
            break
        except Exception as exc:
            print(f"Errore: {exc}", file=sys.stderr)

        time.sleep(settings.poll_interval)


if __name__ == "__main__":
    main()
