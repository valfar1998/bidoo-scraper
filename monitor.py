#!/usr/bin/env python3
"""Monitor Bidoo con alert Telegram (solo lettura, nessuna puntata)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bidoo_errors import CloudflareBlockedError
from bidoo_client import (
    Auction,
    LiveAuction,
    fetch_auctions,
    fetch_live_auctions,
    open_fetch_context,
    seconds_remaining,
)
from filters import is_excluded_auction, max_price_ratio_for_retail, parse_exclude_patterns
from telegram_notifier import send_telegram_message


@dataclass
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    min_retail_value: float
    min_savings_eur: float
    high_value_threshold: float
    max_price_ratio_high: float
    max_price_ratio_mid: float
    max_price_ratio: float
    max_timer_seconds: int
    poll_interval: int
    alert_cooldown: int
    bidoo_url: str
    monitor_mode: str
    exclude_patterns: list[str]
    bid_cost_estimate: float


def _mode_defaults(mode: str) -> tuple[int, int]:
    if mode == "snipe":
        return 60, 15
    return 300, 30


def load_settings(mode_override: str | None = None) -> Settings:
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

    monitor_mode = (mode_override or os.getenv("MONITOR_MODE", "radar")).lower()
    if monitor_mode not in ("radar", "snipe"):
        print("MONITOR_MODE deve essere 'radar' o 'snipe'.", file=sys.stderr)
        sys.exit(1)

    default_timer, default_poll = _mode_defaults(monitor_mode)

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        min_retail_value=float(os.getenv("MIN_RETAIL_VALUE", "50")),
        min_savings_eur=float(os.getenv("MIN_SAVINGS_EUR", "30")),
        high_value_threshold=float(os.getenv("HIGH_VALUE_THRESHOLD", "100")),
        max_price_ratio_high=float(os.getenv("MAX_PRICE_RATIO_HIGH", "0.15")),
        max_price_ratio_mid=float(os.getenv("MAX_PRICE_RATIO_MID", "0.25")),
        max_price_ratio=float(os.getenv("MAX_PRICE_RATIO", "0.35")),
        max_timer_seconds=int(
            os.getenv("MAX_TIMER_SECONDS", str(default_timer))
        ),
        poll_interval=int(os.getenv("POLL_INTERVAL", str(default_poll))),
        alert_cooldown=int(os.getenv("ALERT_COOLDOWN", "600")),
        bidoo_url=os.getenv("BIDOO_URL", "https://it.bidoo.com/"),
        monitor_mode=monitor_mode,
        exclude_patterns=parse_exclude_patterns(
            os.getenv("EXCLUDE_PATTERNS", "")
        ),
        bid_cost_estimate=float(os.getenv("BID_COST_ESTIMATE", "0.20")),
    )


def format_timer(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def price_ratio(settings: Settings, retail_value: float) -> float:
    return max_price_ratio_for_retail(
        retail_value,
        min_retail_value=settings.min_retail_value,
        high_value_threshold=settings.high_value_threshold,
        ratio_high=settings.max_price_ratio_high,
        ratio_mid=settings.max_price_ratio_mid,
        ratio_default=settings.max_price_ratio,
    )


def build_alert(
    auction: Auction,
    live: LiveAuction,
    remaining: int,
    threshold_eur: float,
    discount_pct: float,
    savings_eur: float,
    settings: Settings,
) -> str:
    bids_to_threshold = max(0, int((threshold_eur - live.price_eur) / 0.01))
    est_bid_cost = bids_to_threshold * settings.bid_cost_estimate
    est_total = live.price_eur + est_bid_cost

    return (
        f"🔔 <b>Occasione Bidoo</b> ({settings.monitor_mode})\n\n"
        f"<b>{auction.name}</b>\n"
        f"Valore: {auction.retail_value:.2f} €\n"
        f"Prezzo asta: {live.price_eur:.2f} € "
        f"({discount_pct:.0f}% del valore)\n"
        f"Risparmio nominale: {savings_eur:.2f} € "
        f"(soglia prezzo {threshold_eur:.2f} €)\n"
        f"Timer: {format_timer(remaining)}\n"
        f"Stima se rilanci fino alla soglia: ~{est_total:.0f} € "
        f"(+{bids_to_threshold} rilanci × {settings.bid_cost_estimate:.2f} €)\n"
        f"<i>Ricorda: il costo reale include le puntate che usi tu.</i>\n"
        f"<a href=\"{auction.url}\">Apri asta</a>"
    )


def should_alert(
    auction: Auction,
    live: LiveAuction,
    remaining: int,
    settings: Settings,
) -> tuple[bool, float, float, float]:
    if live.state != "ON":
        return False, 0.0, 0.0, 0.0

    if is_excluded_auction(
        auction.name, auction.slug, settings.exclude_patterns
    ):
        return False, 0.0, 0.0, 0.0

    if auction.retail_value <= settings.min_retail_value:
        return False, 0.0, 0.0, 0.0

    savings_eur = auction.retail_value - live.price_eur
    if savings_eur < settings.min_savings_eur:
        return False, 0.0, 0.0, savings_eur

    ratio = price_ratio(settings, auction.retail_value)
    threshold = auction.retail_value * ratio
    if live.price_eur >= threshold:
        return False, threshold, 0.0, savings_eur

    if remaining > settings.max_timer_seconds:
        return False, threshold, 0.0, savings_eur

    discount_pct = (live.price_eur / auction.retail_value) * 100
    return True, threshold, discount_pct, savings_eur


STATE_FILE = Path(__file__).resolve().parent / ".alert_state.json"


def load_alert_state() -> dict[str, float]:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def save_alert_state(last_alert: dict[str, float]) -> None:
    STATE_FILE.write_text(
        json.dumps(last_alert, indent=2),
        encoding="utf-8",
    )


def filter_catalog(auctions: list[Auction], settings: Settings) -> list[Auction]:
    selected: list[Auction] = []
    skipped_excluded = 0
    skipped_value = 0

    for auction in auctions:
        if is_excluded_auction(
            auction.name, auction.slug, settings.exclude_patterns
        ):
            skipped_excluded += 1
            continue
        if auction.retail_value <= settings.min_retail_value:
            skipped_value += 1
            continue
        selected.append(auction)

    print(
        f"Filtro catalogo: {len(selected)} candidate, "
        f"{skipped_excluded} escluse (puntate/buoni), "
        f"{skipped_value} sotto {settings.min_retail_value} €."
    )
    return selected


def run_check(settings: Settings, last_alert: dict[str, float]) -> int:
    now = time.time()
    sent = 0

    with open_fetch_context() as fetch:
        auctions = fetch_auctions(fetch, settings.bidoo_url)
        print(f"Catalogo: {len(auctions)} aste totali.")

        candidates = filter_catalog(auctions, settings)
        if not candidates:
            print("Nessuna asta prodotto sopra la soglia in questa pagina.")
            return 0

        server_time, live_items = fetch_live_auctions(
            fetch,
            settings.bidoo_url,
            [a.auction_id for a in candidates],
        )
        live_by_id = {item.auction_id: item for item in live_items}

        for auction in candidates:
            live = live_by_id.get(auction.auction_id)
            if not live:
                continue

            remaining = seconds_remaining(server_time, live)
            ok, threshold, discount_pct, savings_eur = should_alert(
                auction, live, remaining, settings
            )
            if not ok:
                continue

            last_sent = last_alert.get(auction.auction_id, 0)
            if now - last_sent < settings.alert_cooldown:
                continue

            message = build_alert(
                auction,
                live,
                remaining,
                threshold,
                discount_pct,
                savings_eur,
                settings,
            )
            send_telegram_message(
                settings.telegram_bot_token,
                settings.telegram_chat_id,
                message,
            )
            last_alert[auction.auction_id] = now
            sent += 1
            print(
                f"Alert inviato: {auction.name} "
                f"({live.price_eur:.2f} €, risparmio {savings_eur:.0f} €, "
                f"timer {format_timer(remaining)})"
            )

    save_alert_state(last_alert)
    return sent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Bidoo con alert Telegram (solo lettura)."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Esegue un solo controllo e termina (ideale per automazione ogni N minuti).",
    )
    parser.add_argument(
        "--mode",
        choices=("radar", "snipe"),
        help="radar: timer fino a 5 min (cloud). snipe: timer fino a 60 s (locale).",
    )
    return parser.parse_args()


def print_rules(settings: Settings) -> None:
    print(f"Modalità: {settings.monitor_mode}")
    print(
        f"Regole: valore > {settings.min_retail_value} €, "
        f"risparmio nominale >= {settings.min_savings_eur} €, "
        f"prezzo sotto soglia % (>{settings.high_value_threshold} €: "
        f"{settings.max_price_ratio_high * 100:.0f}%, "
        f"mid: {settings.max_price_ratio_mid * 100:.0f}%), "
        f"timer <= {settings.max_timer_seconds}s, "
        f"escluse puntate/buoni"
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
            "Il monitor funziona da casa con run-check.ps1 o un runner self-hosted.",
            file=sys.stderr,
        )
        sys.exit(0)
    sys.exit(1)


def main() -> None:
    args = parse_args()
    settings = load_settings(mode_override=args.mode)
    last_alert = load_alert_state()

    print("Monitor Bidoo avviato.")
    print_rules(settings)

    if args.once:
        try:
            sent = run_check(settings, last_alert)
            print(f"Controllo completato. Alert inviati: {sent}.")
        except Exception as exc:
            handle_run_error(exc)
        return

    while True:
        try:
            sent = run_check(settings, last_alert)
            if sent == 0:
                print("Nessun alert da inviare in questo ciclo.")
        except KeyboardInterrupt:
            print("\nMonitor fermato.")
            break
        except Exception as exc:
            print(f"Errore: {exc}", file=sys.stderr)

        time.sleep(settings.poll_interval)


if __name__ == "__main__":
    main()
