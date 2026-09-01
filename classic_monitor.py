"""Monitor aste classiche: stima eBay / Vinted / Subito e alert Telegram per sito."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

from dataclasses import replace

from dotenv import load_dotenv

from brands import find_brand
from auction_history import AuctionHistory
from catalog_store import listings_closing_within, upsert_many
from classic_estimator import ClassicEstimate, ChannelEstimate, estimate_classic, min_net_roi_pct
from comps import load_comps, is_stale
from database import record_run_stat
from dry_run import dry_run_banner, is_dry_run
from feedback import FeedbackStore
from filters import is_excluded_auction, is_hyper_competitive, parse_exclude_patterns
from flip_rules import infer_flip_tag, is_unshippable
from http_fetch import open_fetcher
from listing import SourceListing
from market_lookup import ensure_vinted_session, lookup_channels, market_lookup_enabled
from money import is_heavy_item, listing_passes_profile, looks_like_bulk_lot, remaining_from_any
from monitor import AlertKind, load_alert_state, save_alert_state
from inventory import save_alert_snapshot
from lot_unbundler import format_unbundle_dict, unbundle_lot, unbundle_to_dict
from photo_check import inspect_image
from scraper_health import record_scraper_failure, record_scraper_success
from site_cooldown import record_run, remaining_skip_hours, scrape_cooldown_hours, should_skip
from site_profiles import DEFAULT_ENABLED_SOURCES, get_profile
from sources import fetch_source
from telegram_bot import build_feedback_keyboard
from telegram_notifier import escape_html, send_telegram_message
from telegram_topics import resolve_alert_topic
from vision_estimator import analyze_listing_image, apply_vision_to_listing, vision_enabled


def _safe_console(text: str) -> str:
    """Evita crash su Windows cp1252 quando il profilo ha emoji."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


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
    loc = f"\nRitiro: {escape_html(listing.location)}" if listing.location else ""
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
    brand_line = f"\nMarca: {escape_html(estimate.brand)}" if estimate.brand else ""
    why = ""
    if estimate.deal_reasons:
        why = "\n".join(f"• {escape_html(item)}" for item in estimate.deal_reasons[:6])
        why = f"\n<b>━━ PERCHÉ È BUONO ━━</b>\n{why}\n"
    risk = ""
    if estimate.risks:
        risk = "\n".join(f"• {escape_html(item)}" for item in estimate.risks[:6])
        risk = f"\n<b>━━ PERCHÉ POTREBBE ESSERE RISCHIOSO ━━</b>\n{risk}\n"
    extras_cost = ""
    if estimate.pickup_eur or estimate.deposit_eur:
        extras_cost = (
            f"\nRitiro stimato: {estimate.pickup_eur:.0f} €"
            f"{f' · cauzione {estimate.deposit_eur:.0f} €' if estimate.deposit_eur else ''}"
        )
    comps_note = (
        f"\nComps: {escape_html(estimate.comps_product)}" if estimate.comps_product else ""
    )
    vision = (listing.extra or {}).get("vision") or {}
    vision_line = ""
    if vision:
        bits = []
        if vision.get("brand"):
            bits.append(f"marca {vision['brand']}")
        if vision.get("model"):
            bits.append(f"modello {vision['model']}")
        if vision.get("ean"):
            bits.append(f"EAN {vision['ean']}")
        if vision.get("total_retail_eur"):
            bits.append(f"listino OCR ~{vision['total_retail_eur']:.0f} €")
        if vision.get("packaging_intact") is False:
            bits.append("packaging non integro")
        if bits:
            vision_line = f"\nVision AI: {escape_html(', '.join(bits))}"
    unbundle = extra.get("unbundle") or {}
    unbundle_line = ""
    if unbundle.get("items"):
        unbundle_line = f"\n{format_unbundle_dict(unbundle)}"
    capital_line = ""
    try:
        from capital_allocator import check_allocation

        alloc = check_allocation(estimate.category_tag, estimate.brand, listing.current_price_eur)
        if not alloc.ok:
            capital_line = f"\n⚠️ <b>{escape_html(alloc.reason)}</b>"
        elif alloc.remaining_total_eur < alloc.max_capital_eur * 0.15:
            capital_line = (
                f"\n💰 Capitale attivo: {alloc.active_capital_eur:.0f}/"
                f"{alloc.max_capital_eur:.0f} € "
                f"(residuo {alloc.remaining_total_eur:.0f} €)"
            )
    except Exception:
        pass
    title = escape_html(listing.title)
    category_name = escape_html(estimate.category_name)
    verdict = escape_html(estimate.verdict_reason)
    roi_target = min_net_roi_pct()
    max_bid_block = (
        f"\n<b>━━ MAX BID (rilancio) ━━</b>\n"
        f"🎯 <b>Max Bid consigliato: {estimate.recommended_max_bid_eur:.2f} €</b>\n"
        f"   (garantisce ROI ≥ {roi_target:.0f}% e profitto ≥ soglia)\n"
        f"Profitto stimato a quel prezzo: {estimate.profit_at_max_bid_eur:.0f} € "
        f"(ROI {estimate.roi_at_max_bid_pct:.0f}%)\n"
        f"Prezzo ora: {listing.current_price_eur:.2f} € · "
        f"Margine rilancio: {estimate.recommended_max_bid_eur - listing.current_price_eur:.2f} €\n"
        f"Pareggio: {estimate.break_even_bid_eur:.2f} €"
    )
    return (
        f"{profile.emoji} <b>{escape_html(profile.telegram_color_note)}</b>\n"
        f"<b>{kind_label}</b>\n\n"
        f"<b>{title}</b>\n"
        f"{category_name}{brand_line}{loc}{pieces_line}{remaining_line}\n\n"
        f"<b>━━ COSA FARE ━━</b>\n"
        f"{action}\n"
        f"{verdict}{bulk}\n"
        f"{max_bid_block}\n"
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
        f"Score: {estimate.score}/100 · Confidence: <b>{estimate.confidence}/100</b> "
        f"<i>(marca, comps, spedibilità, margine, titolo)</i>{vision_line}{unbundle_line}{capital_line}\n\n"
        f"<i>Tocca i pulsanti sotto per feedback rapido</i>\n"
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
    fetcher=None,
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
    if skip_hyper and is_hyper_competitive(
        listing.title, listing.listing_id, listing.current_price_eur
    ):
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
        fetcher=fetcher,
        tracked=tracked,
        remaining_seconds=_remaining_seconds(listing),
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


def run_source(source: str, *, mode: str = "full") -> int:
    load_dotenv()
    dry_run_banner(f"run_source:{source}")
    mode = (mode or os.getenv("MONITOR_MODE", "full")).strip().lower()
    if mode == "discovery":
        with open_fetcher() as fetcher:
            listings = fetch_source(source, fetcher)
        upsert_many(listings)
        print(f"[discovery:{source}] Catalogati {len(listings)} lotti.")
        record_scraper_success(source)
        return 0

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Configura TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID in .env", file=sys.stderr)
        sys.exit(1)

    if should_skip(source):
        hrs = remaining_skip_hours(source)
        when = f" (~{hrs:.1f}h rimanenti)" if hrs > 0 else ""
        print(f"[{source}] Skip: cooldown siti rumorosi{when}.")
        return 0

    profile = get_profile(source)
    kinds = _parse_kinds(os.getenv("ALERT_KINDS", "deal"))
    min_profit = float(os.getenv("MIN_RESALE_PROFIT_EUR", "25"))
    min_margin = float(os.getenv("MIN_RESALE_MARGIN_PCT", "25"))
    min_score = int(os.getenv("MIN_RESALE_SCORE", "50"))
    min_headroom = float(os.getenv("MIN_PRICE_HEADROOM_EUR", "1"))
    skip_hyper = os.getenv("CLASSIC_SKIP_HYPER", "true").lower() in ("1", "true", "yes")
    skip_heavy = os.getenv("SKIP_HEAVY_ITEMS", "true").lower() in ("1", "true", "yes")
    exclude_patterns = parse_exclude_patterns(os.getenv("EXCLUDE_PATTERNS", ""))
    cooldown = int(os.getenv("ALERT_COOLDOWN", "3600"))
    max_hours = _max_hours_for(profile)

    print(_safe_console(f"{profile.emoji} {profile.name} -> eBay / Vinted / Subito"))
    print(profile.needs)
    window = (
        "annunci (no filtro scadenza)"
        if profile.listing_kind == "classified"
        else "bancali (no filtro scadenza)"
        if max_hours is None
        else f"solo scadenza oggi (<= {max_hours:.1f}h a mezzanotte)"
        if profile.key == "catawiki"
        and os.getenv("CATAWIKI_CLOSING_TODAY", "true").lower() in ("1", "true", "yes")
        else f"solo scadenza <= {max_hours:g}h"
    )
    print(
        _safe_console(
            f"Alert {', '.join(k.value for k in sorted(kinds, key=lambda k: k.value))} | "
            f"profitto min {min_profit:.0f} EUR | margine {min_margin:.0f}% | score {min_score} | "
            f"{window}"
        )
    )

    listings: list[SourceListing] = []
    try:
        history = AuctionHistory()
        last_alert = load_alert_state()
        comps = load_comps()
        feedback = FeedbackStore.load()
        now = time.time()
        sent = 0
        discarded = 0
        waf_blocked = 0
        theoretical_margin = 0.0
        if feedback.adapted():
            print(f"Filtri adattivi attivi ({len(feedback.seen)} lotti visti).")
        if comps:
            stale = sum(1 for row in comps if is_stale(row))
            print(f"Comps locali: {len(comps)} prodotti in data/comps.csv")
            if stale:
                print(f"  {stale} comps > 7 giorni: python update_comps.py")

        with open_fetcher() as fetcher:
            try:
                fresh = fetch_source(source, fetcher)
            except Exception as exc:
                msg = str(exc).lower()
                if "cloudflare" in msg or "challenge" in msg or "blocked" in msg or "403" in msg:
                    waf_blocked = 1
                raise
            if mode == "sniper":
                window_h = float(os.getenv("SNIPER_WINDOW_HOURS", "2"))
                catalog = listings_closing_within(source, window_h)
                fresh_map = {item.history_key: item for item in fresh}
                if catalog:
                    listings = [fresh_map.get(item.history_key, item) for item in catalog]
                else:
                    listings = _filter_closing_soon(
                        fresh,
                        window_h,
                        keep_unknown=profile.listing_kind == "judicial",
                    )
                print(f"[sniper:{source}] Lotti in chiusura <= {window_h:g}h: {len(listings)}.")
            else:
                listings = fresh
            if mode == "full":
                upsert_many(fresh)
            print(f"[{source}] Catalogo: {len(listings)} lotti.")
            keep_unknown = profile.listing_kind == "judicial" or (
                profile.key == "catawiki"
                and os.getenv("CATAWIKI_CLOSING_TODAY", "true").lower() in ("1", "true", "yes")
            )
            listings = _filter_closing_soon(
                listings,
                max_hours,
                keep_unknown=keep_unknown,
            )
            if max_hours is not None and mode != "sniper":
                print(_safe_console(f"[{source}] In scadenza <= {max_hours:g}h: {len(listings)}."))

            lookup_fetcher = fetcher if market_lookup_enabled(source) else None
            if lookup_fetcher:
                use_vinted, _ = lookup_channels(source)
                if use_vinted:
                    ensure_vinted_session(lookup_fetcher)
                print("[market] Ricerca live Vinted/eBay per stima rivendita (cache 24h).")

            for listing in listings:
                photo = inspect_image(listing)
                if photo in ("missing", "tiny"):
                    discarded += 1
                    continue
                if vision_enabled():
                    analysis = analyze_listing_image(listing)
                    if analysis:
                        listing = apply_vision_to_listing(listing, analysis)
                extra = dict(listing.extra or {})
                vision = extra.get("vision") or {}
                manifest = list(vision.get("manifest_lines") or extra.get("packing_list") or [])
                if manifest or profile.listing_kind == "pallet":
                    ub = unbundle_lot(
                        manifest_lines=manifest,
                        title=listing.title,
                        total_retail_eur=float(vision.get("total_retail_eur") or 0),
                        vision_extra=vision,
                    )
                    if ub.items:
                        extra["unbundle"] = unbundle_to_dict(ub)
                        listing = replace(listing, extra=extra)
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
                    fetcher=lookup_fetcher,
                )
                if not picked:
                    discarded += 1
                    continue
                kind, estimate = picked
                theoretical_margin += max(
                    0.0, estimate.channels[estimate.best_platform].net_profit_eur
                )
                key = f"{source}:{listing.listing_id}"
                if now - last_alert.get(key, 0) < cooldown:
                    continue
                message = build_classic_alert(
                    kind=kind, listing=listing, estimate=estimate, profile=profile
                )
                keyboard = None
                if os.getenv("TELEGRAM_INLINE_KEYBOARD", "true").lower() in ("1", "true", "yes"):
                    keyboard = build_feedback_keyboard(listing.history_key)
                thread_id = resolve_alert_topic(
                    category_tag=estimate.category_tag,
                    listing_kind=profile.listing_kind,
                    confidence=estimate.confidence,
                    is_viable=estimate.is_viable,
                    risks=estimate.risks,
                    score=estimate.score,
                )
                send_telegram_message(
                    token,
                    chat_id,
                    message,
                    reply_markup=keyboard,
                    message_thread_id=thread_id,
                )
                best = estimate.channels[estimate.best_platform]
                save_alert_snapshot(
                    listing.history_key,
                    listing={
                        "source": listing.source,
                        "listing_id": listing.listing_id,
                        "title": listing.title,
                        "url": listing.url,
                        "current_price_eur": listing.current_price_eur,
                        "category_tag": listing.category_tag,
                        "manifest_lines": (listing.extra or {}).get("vision", {}).get("manifest_lines")
                        or (listing.extra or {}).get("packing_list")
                        or [],
                        "packing_list": (listing.extra or {}).get("packing_list") or [],
                        "unbundle": (listing.extra or {}).get("unbundle"),
                    },
                    estimate={
                        "inferred_resale_eur": estimate.inferred_resale_eur,
                        "landed_cost_eur": estimate.landed_cost_eur,
                        "recommended_max_bid_eur": estimate.recommended_max_bid_eur,
                        "max_bid_eur": estimate.max_bid_eur,
                        "expected_profit_eur": best.net_profit_eur,
                        "best_net_profit_eur": best.net_profit_eur,
                        "category_tag": estimate.category_tag,
                        "brand": estimate.brand,
                        "best_platform": estimate.best_platform,
                        "roi_at_max_bid_pct": estimate.roi_at_max_bid_pct,
                    },
                    profile_key=profile.key,
                )
                last_alert[key] = now
                sent += 1
                print(
                    _safe_console(
                        f"Alert {kind.value} [{source}]: {listing.title[:60]} "
                        f"-> {estimate.best_platform} +"
                        f"{estimate.channels[estimate.best_platform].net_profit_eur:.0f} EUR"
                    )
                )

        if not is_dry_run():
            feedback.save()
            record_run(source, discarded=discarded, sent=sent, now=now)
            record_run_stat(
                source,
                fetched=len(listings),
                discarded=discarded,
                sent=sent,
                waf_blocked=waf_blocked,
                theoretical_margin_eur=theoretical_margin,
                now=now,
            )
            history.prune(int(os.getenv("HISTORY_MAX_AGE_HOURS", "72")) * 3600, now=now)
            history.save()
            save_alert_state(last_alert)
        else:
            print(f"[DRY_RUN] Simulati {sent} alert · scartati {discarded} (nessuna persistenza).")
        print(_safe_console(f"[{source}] Alert inviati: {sent} · scartati: {discarded}."))
        record_scraper_success(source)
        return sent
    except Exception as exc:
        record_scraper_failure(source, str(exc))
        raise


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
    if profile.listing_kind in {"pallet", "classified"}:
        return None
    window = scrape_cooldown_hours()
    window_s = str(int(window)) if window == int(window) else str(window)
    if profile.key == "catawiki":
        if os.getenv("CATAWIKI_CLOSING_TODAY", "true").lower() in ("1", "true", "yes"):
            return _hours_until_midnight_rome()
        return float(os.getenv("MAX_HOURS_TO_END_CATAWIKI", os.getenv("MAX_HOURS_TO_END", window_s)))
    if profile.listing_kind == "judicial":
        return float(os.getenv("MAX_HOURS_TO_END_JUDICIAL", "24"))
    if profile.key == "ebay_source":
        return float(os.getenv("MAX_HOURS_TO_END_EBAY", os.getenv("MAX_HOURS_TO_END", window_s)))
    return float(os.getenv("MAX_HOURS_TO_END", window_s))


def _hours_until_midnight_rome() -> float:
    """Ore rimanenti fino a mezzanotte Europe/Rome (finestra 'scade oggi')."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:
        now = datetime.now().astimezone()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = (midnight - now).total_seconds() / 3600
    return max(1.0, min(24.0, hours))


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
