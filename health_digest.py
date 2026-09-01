"""Digest settimanale: lotti analizzati, errori WAF, margine teorico."""

from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict

from dotenv import load_dotenv

from database import ensure_db, run_stats_since
from telegram_notifier import send_telegram_message


def build_health_digest(*, days: int = 7) -> str:
    ensure_db()
    since = time.time() - days * 86400
    stats = run_stats_since(since)
    if not stats:
        return (
            f"📊 <b>Health check ({days} giorni)</b>\n\n"
            "Nessuna statistica nel database. "
            "Assicurati che USE_DATABASE=true e che il monitor giri almeno una volta."
        )

    by_source: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "runs": 0,
            "fetched": 0,
            "discarded": 0,
            "sent": 0,
            "waf": 0,
            "margin": 0.0,
        }
    )
    for row in stats:
        bucket = by_source[row.source]
        bucket["runs"] += 1
        bucket["fetched"] += row.fetched
        bucket["discarded"] += row.discarded
        bucket["sent"] += row.sent
        bucket["waf"] += row.waf_blocked
        bucket["margin"] += row.theoretical_margin_eur

    total_fetched = sum(int(v["fetched"]) for v in by_source.values())
    total_sent = sum(int(v["sent"]) for v in by_source.values())
    total_waf = sum(int(v["waf"]) for v in by_source.values())
    total_margin = sum(float(v["margin"]) for v in by_source.values())
    waf_rate = (total_waf / total_fetched * 100) if total_fetched else 0.0

    lines = [
        f"📊 <b>Health check ({days} giorni)</b>",
        "",
        f"Lotti analizzati: <b>{total_fetched}</b>",
        f"Alert inviati: <b>{total_sent}</b>",
        f"Errori WAF stimati: <b>{total_waf}</b> ({waf_rate:.1f}%)",
        f"Margine teorico totale: <b>{total_margin:.0f} €</b>",
        "",
        "<b>Per fonte</b>",
    ]
    for source in sorted(by_source):
        data = by_source[source]
        source_waf = (data["waf"] / data["fetched"] * 100) if data["fetched"] else 0.0
        lines.append(
            f"• <b>{source}</b>: {int(data['fetched'])} lotti, "
            f"{int(data['sent'])} alert, WAF {source_waf:.0f}%, "
            f"margine ~{data['margin']:.0f} €"
        )
    return "\n".join(lines)


def build_stats_table(*, hours: int = 24) -> str:
    """Tabella compatta per comando /stats."""
    ensure_db()
    since = time.time() - hours * 3600
    stats = run_stats_since(since)
    if not stats:
        return f"📈 <b>Stats ultime {hours}h</b>\n\nNessun run registrato."

    by_source: dict[str, dict[str, float]] = defaultdict(
        lambda: {"runs": 0, "fetched": 0, "discarded": 0, "sent": 0, "waf": 0, "margin": 0.0}
    )
    for row in stats:
        bucket = by_source[row.source]
        bucket["runs"] += 1
        bucket["fetched"] += row.fetched
        bucket["discarded"] += row.discarded
        bucket["sent"] += row.sent
        bucket["waf"] += row.waf_blocked
        bucket["margin"] += row.theoretical_margin_eur

    total_fetched = sum(int(v["fetched"]) for v in by_source.values())
    total_sent = sum(int(v["sent"]) for v in by_source.values())
    hit_rate = (total_sent / total_fetched * 100) if total_fetched else 0.0
    total_margin = sum(float(v["margin"]) for v in by_source.values())

    lines = [
        f"📈 <b>Stats ultime {hours}h</b>",
        "",
        f"Lotti: <b>{total_fetched}</b> · Alert: <b>{total_sent}</b> · Hit rate: <b>{hit_rate:.1f}%</b>",
        f"Margine teorico: <b>{total_margin:.0f} €</b>",
        "",
        "<pre>fonte      run  lotti  alert  WAF  marg€",
    ]
    for source in sorted(by_source):
        data = by_source[source]
        lines.append(
            f"{source[:10]:10} {int(data['runs']):4} {int(data['fetched']):5} "
            f"{int(data['sent']):5} {int(data['waf']):4} {data['margin']:5.0f}"
        )
    lines.append("</pre>")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Invia digest settimanale su Telegram.")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    text = build_health_digest(days=args.days)
    if args.dry_run:
        safe = text.replace("<b>", "").replace("</b>", "")
        try:
            print(safe)
        except UnicodeEncodeError:
            print(safe.encode("ascii", errors="replace").decode("ascii"))
        return
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID mancanti")
    send_telegram_message(token, chat_id, text)


if __name__ == "__main__":
    main()
