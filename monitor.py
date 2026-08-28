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

from bidoo_client import (
    Auction,
    LiveAuction,
    _session,
    fetch_auctions,
    fetch_live_auctions,
    seconds_remaining,
)
from telegram_notifier import send_telegram_message


@dataclass
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    min_retail_value: float
    max_price_ratio: float
    max_timer_seconds: int
    poll_interval: int
    alert_cooldown: int
    bidoo_url: str


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

    return Settings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        min_retail_value=float(os.getenv("MIN_RETAIL_VALUE", "35")),
        max_price_ratio=float(os.getenv("MAX_PRICE_RATIO", "0.35")),
        max_timer_seconds=int(os.getenv("MAX_TIMER_SECONDS", "300")),
        poll_interval=int(os.getenv("POLL_INTERVAL", "15")),
        alert_cooldown=int(os.getenv("ALERT_COOLDOWN", "600")),
        bidoo_url=os.getenv("BIDOO_URL", "https://it.bidoo.com/"),
    )


def format_timer(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def build_alert(
    auction: Auction,
    live: LiveAuction,
    remaining: int,
    threshold_eur: float,
    discount_pct: float,
) -> str:
    return (
        f"🔔 <b>Occasione Bidoo</b>\n\n"
        f"<b>{auction.name}</b>\n"
        f"Valore: {auction.retail_value:.2f} €\n"
        f"Prezzo asta: {live.price_eur:.2f} € "
        f"({discount_pct:.0f}% del valore, soglia {threshold_eur:.2f} €)\n"
        f"Timer: {format_timer(remaining)}\n"
        f"Stato: {live.state}\n"
        f"<a href=\"{auction.url}\">Apri asta</a>"
    )


def should_alert(
    auction: Auction,
    live: LiveAuction,
    remaining: int,
    settings: Settings,
) -> tuple[bool, float, float]:
    if live.state != "ON":
        return False, 0.0, 0.0

    if auction.retail_value <= settings.min_retail_value:
        return False, 0.0, 0.0

    threshold = auction.retail_value * settings.max_price_ratio
    if live.price_eur >= threshold:
        return False, threshold, 0.0

    if remaining > settings.max_timer_seconds:
        return False, threshold, 0.0

    discount_pct = (live.price_eur / auction.retail_value) * 100
    return True, threshold, discount_pct


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


def run_check(settings: Settings, last_alert: dict[str, float]) -> int:
    session = _session()
    now = time.time()
    sent = 0

    auctions = fetch_auctions(session, settings.bidoo_url)
    print(f"Catalogo: {len(auctions)} aste.")

    retail_filtered = [
        a for a in auctions if a.retail_value > settings.min_retail_value
    ]
    if not retail_filtered:
        print("Nessuna asta sopra la soglia di valore in questa pagina.")
        return 0

    server_time, live_items = fetch_live_auctions(
        session,
        settings.bidoo_url,
        [a.auction_id for a in retail_filtered],
    )
    live_by_id = {item.auction_id: item for item in live_items}

    for auction in retail_filtered:
        live = live_by_id.get(auction.auction_id)
        if not live:
            continue

        remaining = seconds_remaining(server_time, live)
        ok, threshold, discount_pct = should_alert(
            auction, live, remaining, settings
        )
        if not ok:
            continue

        last_sent = last_alert.get(auction.auction_id, 0)
        if now - last_sent < settings.alert_cooldown:
            continue

        message = build_alert(auction, live, remaining, threshold, discount_pct)
        send_telegram_message(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            message,
        )
        last_alert[auction.auction_id] = now
        sent += 1
        print(
            f"Alert inviato: {auction.name} "
            f"({live.price_eur:.2f} €, timer {format_timer(remaining)})"
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings()
    last_alert = load_alert_state()

    print("Monitor Bidoo avviato.")
    print(
        f"Regole: valore > {settings.min_retail_value} €, "
        f"prezzo < {settings.max_price_ratio * 100:.0f}% del valore, "
        f"timer <= {settings.max_timer_seconds}s"
    )

    if args.once:
        try:
            sent = run_check(settings, last_alert)
            print(f"Controllo completato. Alert inviati: {sent}.")
        except Exception as exc:
            print(f"Errore: {exc}", file=sys.stderr)
            sys.exit(1)
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
