"""Monitor aste classiche: stima eBay / Vinted / Subito e alert Telegram per sito."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dotenv import load_dotenv

from auction_history import AuctionHistory
from classic_estimator import ClassicEstimate, ChannelEstimate, estimate_classic
from filters import is_excluded_auction, is_hyper_competitive, parse_exclude_patterns
from http_fetch import open_fetcher
from listing import SourceListing
from money import is_heavy_item, listing_passes_profile, looks_like_bulk_lot
from monitor import AlertKind, load_alert_state, save_alert_state
from site_profiles import DEFAULT_ENABLED_SOURCES, get_profile
from sources import fetch_source
from telegram_notifier import send_telegram_message


def _parse_kinds(raw: str) -> set[AlertKind]:
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


def _channel_line(est: ChannelEstimate) -> str:
    sign = "+" if est.net_profit_eur >= 0 else ""
    return (
        f"{est.platform.upper():7} {sign}{est.net_profit_eur:.0f} € "
        f"({est.margin_pct:.0f}%) · vendita ~{est.resale_value_eur:.0f} €"
    )


def build_classic_alert(
    *,
    kind: AlertKind,
    listing: SourceListing,
    estimate: ClassicEstimate,
    profile,
) -> str:
    kind_label = {
        AlertKind.DEAL: "Occasione con margine",
        AlertKind.QUIET: "Pochi rilanci + margine",
        AlertKind.NEW: "Nuovo lotto interessante",
    }[kind]
    best = estimate.channels[estimate.best_platform]
    bulk = "\nLotto/stock: scarto stimato già incluso." if looks_like_bulk_lot(listing.title) else ""
    loc = f"\nRitiro: {listing.location}" if listing.location else ""
    if profile.listing_kind == "pallet":
        action = (
            f"Prezzo max bancale: <b>{estimate.max_bid_eur:.2f} €</b>\n"
            f"Se il listino è più alto → non comprare. Canale: <b>{estimate.best_platform}</b>"
        )
    elif profile.listing_kind == "judicial":
        action = (
            f"Offerta max (oltre cauzione/ritiro): <b>{estimate.max_bid_eur:.2f} €</b>\n"
            f"Oltre → STOP. Canale: <b>{estimate.best_platform}</b>"
        )
    else:
        action = (
            f"Offerta max: <b>{estimate.max_bid_eur:.2f} €</b>\n"
            f"Oltre → STOP. Canale: <b>{estimate.best_platform}</b>"
        )
    pieces = listing.extra.get("pieces") if listing.extra else None
    pieces_line = f"\nPezzi in lista: {pieces}" if pieces else ""
    return (
        f"{profile.emoji} <b>{profile.telegram_color_note}</b>\n"
        f"<b>{kind_label}</b>\n\n"
        f"<b>{listing.title}</b>\n"
        f"{estimate.category_name}{loc}{pieces_line}\n\n"
        f"<b>━━ COSA FARE ━━</b>\n"
        f"{action}\n"
        f"{estimate.verdict_reason}{bulk}\n\n"
        f"<b>━━ COSTO ━━</b>\n"
        f"Prezzo ora: {listing.current_price_eur:.2f} €"
        f"{f' + premio {estimate.buyer_premium_eur:.2f} €' if estimate.buyer_premium_eur else ''}\n"
        f"All-in stimato: {estimate.landed_cost_eur:.2f} €\n"
        f"Pareggio: {estimate.break_even_bid_eur:.2f} €\n\n"
        f"<b>━━ RIVENDITA Vinted / eBay ━━</b>\n"
        f"{_channel_line(estimate.channels['ebay'])}\n"
        f"{_channel_line(estimate.channels['vinted'])}\n"
        f"{_channel_line(estimate.channels['subito'])} <i>(extra)</i>\n"
        f"Scelta: <b>{best.platform}</b>  +{best.net_profit_eur:.0f} €\n"
        f"Score: {estimate.score}/100\n\n"
        f"<i>Stima, non prezzo venduto. Controlla comps prima di offrire.</i>\n"
        f"<a href=\"{listing.url}\">Apri lotto</a>"
    )


def pick_classic(
    *,
    listing: SourceListing,
    tracked_before: bool,
    tracked,
    kinds: set[AlertKind],
    min_profit: float,
    min_margin: float,
    min_score: int,
    min_headroom: float,
    skip_hyper: bool,
    skip_heavy: bool,
    exclude_patterns: list[str],
    profile,
) -> tuple[AlertKind, ClassicEstimate] | None:
    if listing.current_price_eur <= 0:
        return None
    if not listing_passes_profile(
        listing.title, profile.extra_exclude, profile.extra_include
    ):
        return None
    if is_excluded_auction(listing.title, listing.listing_id, exclude_patterns):
        return None
    if skip_heavy and is_heavy_item(listing.title):
        return None
    if skip_hyper and is_hyper_competitive(listing.title, listing.listing_id):
        if not looks_like_bulk_lot(listing.title):
            return None

    estimate = estimate_classic(
        listing,
        profile,
        min_profit_eur=min_profit,
        min_margin_pct=min_margin,
        min_headroom_eur=min_headroom,
    )
    if estimate.verdict != "conviene" or estimate.score < min_score:
        return None

    quiet = tracked.is_quiet(
        int(os.getenv("QUIET_MIN_OBSERVATIONS", "3")),
        int(os.getenv("QUIET_MAX_PRICE_DELTA_CENTS", "8")),
    )
    candidates: list[tuple[int, AlertKind]] = []
    if AlertKind.DEAL in kinds and estimate.is_viable:
        candidates.append((3, AlertKind.DEAL))
    if AlertKind.QUIET in kinds and quiet:
        candidates.append((2, AlertKind.QUIET))
    if AlertKind.NEW in kinds and not tracked_before:
        candidates.append((1, AlertKind.NEW))
    if not candidates:
        return None
    kind = max(candidates, key=lambda item: item[0])[1]
    return kind, estimate


def run_source(source: str) -> int:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Configura TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID in .env", file=sys.stderr)
        sys.exit(1)

    profile = get_profile(source)
    kinds = _parse_kinds(os.getenv("ALERT_KINDS", "deal"))
    min_profit = float(os.getenv("MIN_RESALE_PROFIT_EUR", "20"))
    min_margin = float(os.getenv("MIN_RESALE_MARGIN_PCT", "25"))
    min_score = int(os.getenv("MIN_RESALE_SCORE", "50"))
    min_headroom = float(os.getenv("MIN_PRICE_HEADROOM_EUR", "1"))
    skip_hyper = os.getenv("CLASSIC_SKIP_HYPER", "true").lower() in ("1", "true", "yes")
    skip_heavy = os.getenv("SKIP_HEAVY_ITEMS", "true").lower() in ("1", "true", "yes")
    exclude_patterns = parse_exclude_patterns(os.getenv("EXCLUDE_PATTERNS", ""))
    cooldown = int(os.getenv("ALERT_COOLDOWN", "3600"))

    print(f"{profile.emoji} {profile.name} → eBay / Vinted / Subito")
    print(profile.needs)
    print(
        f"Alert {', '.join(k.value for k in sorted(kinds, key=lambda k: k.value))} | "
        f"profitto min {min_profit:.0f} € | margine {min_margin:.0f}% | score {min_score}"
    )

    history = AuctionHistory()
    last_alert = load_alert_state()
    now = time.time()
    sent = 0

    with open_fetcher() as fetcher:
        listings = fetch_source(source, fetcher)
    print(f"[{source}] Catalogo: {len(listings)} lotti.")

    for listing in listings:
        tracked_before = history.get(listing.history_key) is not None
        tracked = history.observe(
            auction_id=listing.history_key,
            name=listing.title,
            slug=listing.listing_id,
            retail_value=listing.retail_hint_eur or listing.current_price_eur,
            url=listing.url,
            category_tag=source,
            price_cents=listing.price_cents,
            remaining=int(listing.extra.get("remaining_s", 0)) if listing.extra else 0,
            now=now,
        )
        picked = pick_classic(
            listing=listing,
            tracked_before=tracked_before,
            tracked=tracked,
            kinds=kinds,
            min_profit=min_profit,
            min_margin=min_margin,
            min_score=min_score,
            min_headroom=min_headroom,
            skip_hyper=skip_hyper,
            skip_heavy=skip_heavy,
            exclude_patterns=exclude_patterns,
            profile=profile,
        )
        if not picked:
            continue
        kind, estimate = picked
        key = f"{source}:{listing.listing_id}"
        if now - last_alert.get(key, 0) < cooldown:
            continue
        message = build_classic_alert(
            kind=kind, listing=listing, estimate=estimate, profile=profile
        )
        send_telegram_message(token, chat_id, message)
        last_alert[key] = now
        sent += 1
        print(
            f"Alert {kind.value} [{source}]: {listing.title[:60]} "
            f"→ {estimate.best_platform} +{estimate.channels[estimate.best_platform].net_profit_eur:.0f} €"
        )

    history.prune(int(os.getenv("HISTORY_MAX_AGE_HOURS", "72")) * 3600, now=now)
    history.save()
    save_alert_state(last_alert)
    print(f"[{source}] Alert inviati: {sent}.")
    return sent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor aste classiche (sola lettura).")
    parser.add_argument("--source", required=True, help="Chiave sito (es. prezzishock)")
    parser.add_argument("--once", action="store_true", help="Un solo giro")
    return parser.parse_args()


def main(source: str | None = None) -> None:
    if source:
        run_source(source)
        return
    args = parse_args()
    run_source(args.source)


def enabled_sources() -> list[str]:
    load_dotenv()
    raw = os.getenv("ENABLED_SOURCES", "")
    if not raw.strip():
        return list(DEFAULT_ENABLED_SOURCES)
    return [item.strip() for item in raw.split(",") if item.strip()]
