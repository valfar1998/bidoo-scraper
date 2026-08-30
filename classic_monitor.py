"""Monitor aste classiche: stima eBay / Vinted / Subito e alert Telegram per sito."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dotenv import load_dotenv

from brands import find_brand
from auction_history import AuctionHistory
from classic_estimator import ClassicEstimate, ChannelEstimate, estimate_classic
from comps import load_comps, is_stale
from feedback import FeedbackStore
from filters import is_excluded_auction, is_hyper_competitive, parse_exclude_patterns
from flip_rules import infer_flip_tag, is_unshippable
from http_fetch import open_fetcher
from listing import SourceListing
from money import is_heavy_item, listing_passes_profile, looks_like_bulk_lot, remaining_from_any
from monitor import AlertKind, load_alert_state, save_alert_state
from site_cooldown import record_run, should_skip
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
            f"Prezzo max bancale: <b>{estimate.max_bid_eur:.2f} €</b> "
            f"(cap budget {estimate.max_buy_eur:.0f} €)\n"
            f"Se il listino è più alto → non comprare. Canale: <b>{estimate.best_platform}</b>"
        )
    elif profile.listing_kind == "judicial":
        action = (
            f"Offerta max (oltre cauzione/ritiro): <b>{estimate.max_bid_eur:.2f} €</b>\n"
            f"Oltre → STOP. Canale: <b>{estimate.best_platform}</b>"
        )
    else:
        action = (
            f"Offerta max: <b>{estimate.max_bid_eur:.2f} €</b> "
            f"(budget {estimate.max_buy_eur:.0f} €)\n"
            f"Oltre → STOP. Canale: <b>{estimate.best_platform}</b>"
        )
    extra = listing.extra or {}
    pieces = extra.get("pieces")
    packing = extra.get("packing_list")
    pieces_line = f"\nPezzi in lista: {pieces}" if pieces else ""
    if profile.listing_kind == "pallet":
        pieces_line += (
            "\nPacking list: sì" if packing else "\nPacking list: no (stima a pezzo/budget)"
        )
    remaining = _remaining_seconds(listing)
    remaining_line = f"\nScade tra: {_format_remaining(remaining)}" if remaining is not None else ""
    brand_line = f"\nMarca: {estimate.brand}" if estimate.brand else ""
    why = ""
    if estimate.deal_reasons:
        why = "\n".join(f"• {item}" for item in estimate.deal_reasons[:4])
        why = f"\n<b>━━ PERCHÉ ━━</b>\n{why}\n"
    risk = ""
    if estimate.risks:
        risk = "\n".join(f"• {item}" for item in estimate.risks[:4])
        risk = f"\n<b>━━ RISCHI ━━</b>\n{risk}\n"
    extras_cost = ""
    if estimate.pickup_eur or estimate.deposit_eur:
        extras_cost = (
            f"\nRitiro stimato: {estimate.pickup_eur:.0f} €"
            f"{f' · cauzione {estimate.deposit_eur:.0f} €' if estimate.deposit_eur else ''}"
        )
    comps_note = (
        f"\nComps: {estimate.comps_product}" if estimate.comps_product else ""
    )
    return (
        f"{profile.emoji} <b>{profile.telegram_color_note}</b>\n"
        f"<b>{kind_label}</b>\n\n"
        f"<b>{listing.title}</b>\n"
        f"{estimate.category_name}{brand_line}{loc}{pieces_line}{remaining_line}\n\n"
        f"<b>━━ COSA FARE ━━</b>\n"
        f"{action}\n"
        f"{estimate.verdict_reason}{bulk}\n"
        f"{why}{risk}\n"
        f"<b>━━ COSTO ━━</b>\n"
        f"Prezzo ora: {listing.current_price_eur:.2f} €"
        f"{f' + premio {estimate.buyer_premium_eur:.2f} €' if estimate.buyer_premium_eur else ''}"
        f"{extras_cost}\n"
        f"All-in stimato: {estimate.landed_cost_eur:.2f} €\n"
        f"Pareggio: {estimate.break_even_bid_eur:.2f} €{comps_note}\n\n"
        f"<b>━━ RIVENDITA Vinted / eBay ━━</b>\n"
        f"{_channel_line(estimate.channels['ebay'])}\n"
        f"{_channel_line(estimate.channels['vinted'])}\n"
        f"{_channel_line(estimate.channels['subito'])} <i>(extra)</i>\n"
        f"Scelta: <b>{best.platform}</b>  +{best.net_profit_eur:.0f} €\n"
        f"Score: {estimate.score}/100 · Confidence: <b>{estimate.confidence}/100</b>\n\n"
        f"<i>Stima. Segna ignorato/comprato con record_feedback.py</i>\n"
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
    feedback: FeedbackStore,
    comps,
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
    if profile.listing_kind != "pallet" and is_unshippable(listing, profile):
        return None
    if skip_hyper and is_hyper_competitive(listing.title, listing.listing_id):
        if profile.listing_kind != "pallet" and not looks_like_bulk_lot(listing.title):
            return None

    estimate = estimate_classic(
        listing,
        profile,
        min_profit_eur=min_profit,
        min_margin_pct=min_margin,
        min_headroom_eur=min_headroom,
        feedback=feedback,
        comps=comps,
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

    if should_skip(source):
        print(f"[{source}] Skip: cooldown siti rumorosi (prossimo giro più tardi).")
        return 0

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
    max_hours = _max_hours_for(profile)

    print(f"{profile.emoji} {profile.name} → eBay / Vinted / Subito")
    print(profile.needs)
    window = (
        "bancali (no filtro scadenza)"
        if max_hours is None
        else f"solo scadenza ≤ {max_hours:g}h"
    )
    print(
        f"Alert {', '.join(k.value for k in sorted(kinds, key=lambda k: k.value))} | "
        f"profitto min {min_profit:.0f} € | margine {min_margin:.0f}% | score {min_score} | "
        f"{window}"
    )

    history = AuctionHistory()
    last_alert = load_alert_state()
    comps = load_comps()
    feedback = FeedbackStore.load()
    now = time.time()
    sent = 0
    discarded = 0
    if feedback.adapted():
        print(f"Filtri adattivi attivi ({len(feedback.seen)} lotti visti).")
    if comps:
        stale = sum(1 for row in comps if is_stale(row))
        print(f"Comps locali: {len(comps)} prodotti in data/comps.csv")
        if stale:
            print(f"  {stale} comps > 7 giorni: python update_comps.py")

    with open_fetcher() as fetcher:
        listings = fetch_source(source, fetcher)
    print(f"[{source}] Catalogo: {len(listings)} lotti.")
    listings = _filter_closing_soon(
        listings,
        max_hours,
        keep_unknown=profile.listing_kind == "judicial",
    )
    if max_hours is not None:
        print(f"[{source}] In scadenza ≤ {max_hours:g}h: {len(listings)}.")

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
            remaining=_remaining_seconds(listing) or 0,
            now=now,
        )
        brand = find_brand(listing.title)
        feedback.record_seen(
            listing_id=listing.history_key,
            title=listing.title,
            brand=brand,
            category=listing.category_tag or infer_flip_tag(listing.title),
            source=source,
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
            feedback=feedback,
            comps=comps,
        )
        if not picked:
            discarded += 1
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

    feedback.save()
    record_run(source, discarded=discarded, sent=sent, now=now)
    history.prune(int(os.getenv("HISTORY_MAX_AGE_HOURS", "72")) * 3600, now=now)
    history.save()
    save_alert_state(last_alert)
    print(f"[{source}] Alert inviati: {sent} · scartati: {discarded}.")
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


def _max_hours_for(profile) -> float | None:
    if profile.listing_kind == "pallet":
        return None
    if profile.listing_kind == "judicial":
        return float(os.getenv("MAX_HOURS_TO_END_JUDICIAL", "24"))
    return float(os.getenv("MAX_HOURS_TO_END", "4"))


def _remaining_seconds(listing: SourceListing) -> int | None:
    if listing.remaining_seconds is not None:
        return listing.remaining_seconds
    extra = listing.extra or {}
    if extra.get("remaining_s"):
        return int(extra["remaining_s"])
    return remaining_from_any(listing.remaining_text)


def _filter_closing_soon(
    listings: list[SourceListing],
    max_hours: float | None,
    *,
    keep_unknown: bool = False,
) -> list[SourceListing]:
    if max_hours is None:
        return listings
    window = int(max_hours * 3600)
    kept: list[SourceListing] = []
    for listing in listings:
        remaining = _remaining_seconds(listing)
        if remaining is None:
            if keep_unknown:
                kept.append(listing)
            continue
        if 0 < remaining <= window:
            kept.append(listing)
    return kept


def _format_remaining(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days}g {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def enabled_sources() -> list[str]:
    load_dotenv()
    raw = os.getenv("ENABLED_SOURCES", "")
    if not raw.strip():
        return list(DEFAULT_ENABLED_SOURCES)
    return [item.strip() for item in raw.split(",") if item.strip()]
